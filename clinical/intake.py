"""Ficha desde el móvil del paciente: el enlace y lo que se hace con él.

Aquí vive toda la lógica de `PatientFormLink` que no es la vista: emitir el
enlace, resolver un token, rellenar la ficha y medir fallos. La vista pública
(`clinical/public_views.py`) y la del panel (`patients/intake_views.py`) son
delgadas y llaman a esto.

Reglas que atraviesan el módulo:

- **El token no se guarda.** Se genera con `secrets` (256 bits), se entrega una
  sola vez a quien emite el enlace y en la base de datos queda su SHA-256. Un
  volcado de la tabla no da acceso a ninguna ficha.
- **Un solo uso, y atómico.** `complete_link()` bloquea la fila, vuelve a mirar
  que siga siendo usable y escribe todo —episodio, anamnesis, firma y el sello de
  completado— en una transacción: o entra todo o no entra nada, y dos envíos a la
  vez no escriben dos veces.
- **Se verifica a quien contesta.** Si el paciente tiene fecha de nacimiento, hay
  que repetirla. No es autenticación fuerte: es la segunda llave que impide que
  quien reciba el enlace por error (o reenviado) escriba en una historia clínica
  ajena. Cinco fallos bloquean el enlace.
- **Lo que escribe el paciente queda atribuido a su canal.** El actor del
  `ChangeLog`/`AccessLog` es «paciente (enlace #N)» con origen `public`; la
  respuesta lleva `source=patient_web` y no tiene profesional.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import replace
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from audit.context import ORIGIN_PUBLIC, get_audit_context, reset_audit_context, set_audit_context
from clinical.conf import form_link_ttl_hours, intake_throttle, minor_age
from clinical.models import (
    Episode,
    MedicalHistory,
    PatientFormLink,
    QuestionnaireResponse,
    SignedConsent,
)

logger = logging.getLogger(__name__)

#: Versión del aviso de protección de datos que enseña la página pública
#: (`templates/intake/_privacy_notice.html`). Se guarda en el enlace al completarlo:
#: si el aviso cambia, hay que subirla, y así se sabe qué texto vio cada paciente.
#: Es el texto de primera capa del art. 13 RGPD; su redacción definitiva es de la
#: clínica y hay que revisarla con quien lleve lo legal.
PRIVACY_NOTICE_VERSION = '2026-10-28.1'

#: Motivo del episodio que se abre cuando nadie lo eligió y el paciente envía.
DEFAULT_EPISODE_REASON = 'Ficha del paciente (enlace)'


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------


def hash_token(token: str) -> str:
    return hashlib.sha256((token or '').encode()).hexdigest()


def new_token() -> str:
    # 32 bytes = 256 bits; urlsafe para que viaje en la URL sin escapar.
    return secrets.token_urlsafe(32)


def find_link(token: str) -> PatientFormLink | None:
    """El enlace de ese token, o `None`. No dice si está caducado: eso es `is_usable()`.

    Un token con forma imposible ni se consulta. El resto se busca por el hash,
    así que el tiempo de respuesta no depende de cuánto se parezca a uno real.
    """
    if not token or len(token) > 100:
        return None
    return (
        PatientFormLink.objects
        .select_related('clinic', 'patient', 'questionnaire_template', 'consent_template', 'episode')
        .filter(token_hash=hash_token(token))
        .first()
    )


def link_is_openable(link: PatientFormLink | None, now=None) -> bool:
    """¿Se puede abrir la página? Usable, y con la ficha en alta y no archivada."""
    if link is None or not link.is_usable(now):
        return False
    patient = link.patient
    return patient is not None and not patient.is_archived and patient.clinic_id == link.clinic_id


# ---------------------------------------------------------------------------
# Emisión
# ---------------------------------------------------------------------------


def issue_link(
    *,
    patient,
    created_by,
    questionnaire_template=None,
    consent_template=None,
    episode=None,
    episode_reason='',
    now=None,
) -> tuple[PatientFormLink, str]:
    """Crea el enlace y devuelve `(enlace, token en claro)`.

    El token en claro solo existe en este valor de retorno: quien lo reciba tiene
    que enviarlo o enseñarlo ya, porque no se puede volver a obtener.

    Emitir uno nuevo **revoca los pendientes del mismo paciente**: dos enlaces
    vivos a la vez son dos llaves que hay que vigilar.
    """
    now = now or timezone.now()
    token = new_token()

    with transaction.atomic():
        for old in PatientFormLink.objects.filter(
            patient=patient, completed_at__isnull=True, revoked_at__isnull=True,
            expires_at__gt=now,
        ):
            old.revoke(by=created_by)
        link = PatientFormLink.objects.create(
            clinic=patient.clinic,
            patient=patient,
            questionnaire_template=questionnaire_template,
            consent_template=consent_template,
            episode=episode,
            episode_reason=(episode_reason or '').strip()[:255],
            token_hash=hash_token(token),
            created_by=created_by,
            expires_at=now + timedelta(hours=form_link_ttl_hours()),
        )
    return link, token


def mark_sent(link: PatientFormLink, *, channel: str) -> None:
    link.channel = channel
    link.sent_at = timezone.now()
    link.save(update_fields=['channel', 'sent_at', 'updated_at'])


def link_url(request, token: str) -> str:
    from django.urls import reverse

    return request.build_absolute_uri(reverse('intake:fill', kwargs={'token': token}))


def whatsapp_text(link: PatientFormLink, url: str) -> str:
    """El mensaje que recibe el paciente. Sin datos clínicos: solo el nombre de pila."""
    local = timezone.localtime(link.expires_at)
    return (
        f'Hola, {link.patient.first_name}. Para preparar tu visita en {link.clinic.name} '
        f'necesitamos que rellenes tu ficha desde el móvil:\n\n{url}\n\n'
        f'El enlace es personal, solo funciona una vez y caduca el '
        f'{local:%d/%m/%Y} a las {local:%H:%M}. Si no lo esperabas, ignora este mensaje.'
    )


# ---------------------------------------------------------------------------
# Quién es el paciente de cara a la página
# ---------------------------------------------------------------------------


def is_minor(patient, today=None) -> bool:
    """¿Es menor de la edad de consentimiento? Sin fecha de nacimiento, no se sabe: no."""
    born = patient.date_of_birth
    if born is None:
        return False
    today = today or timezone.localdate()
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    return age < minor_age()


# ---------------------------------------------------------------------------
# Límite de intentos
# ---------------------------------------------------------------------------


def _throttle_key(ip: str) -> str:
    return f'intake-unknown-token:{ip or "?"}'


def throttled(ip: str) -> bool:
    """¿Esta IP ha agotado sus tokens desconocidos?"""
    limit, _ = intake_throttle()
    return (cache.get(_throttle_key(ip)) or 0) >= limit


def register_unknown_token(ip: str) -> None:
    _, window = intake_throttle()
    key = _throttle_key(ip)
    cache.add(key, 0, timeout=window)
    try:
        cache.incr(key)
    except ValueError:  # expiró entre el add y el incr
        cache.set(key, 1, timeout=window)


def register_failed_verification(link: PatientFormLink) -> bool:
    """Suma un fallo de verificación. Devuelve `True` si el enlace queda bloqueado.

    Bajo bloqueo de fila: dos envíos simultáneos no pueden saltarse el contador.
    """
    with transaction.atomic():
        locked = PatientFormLink.objects.select_for_update().get(pk=link.pk)
        locked.failed_attempts += 1
        locked.save(update_fields=['failed_attempts', 'updated_at'])
    link.failed_attempts = locked.failed_attempts
    return locked.failed_attempts >= PatientFormLink.MAX_FAILED_ATTEMPTS


# ---------------------------------------------------------------------------
# Actor de la auditoría
# ---------------------------------------------------------------------------


class patient_actor:
    """Contexto en el que lo que se escriba queda atribuido al enlace.

    Sin sesión no hay usuario al que atribuir nada, pero un registro sin actor no
    sirve: aquí el actor es «paciente (enlace #N)», con origen `public` y la IP y
    el user agent de la petición, que el middleware ya capturó.
    """

    def __init__(self, link: PatientFormLink):
        self.link = link
        self._token = None

    def __enter__(self):
        context = get_audit_context()
        self._token = set_audit_context(replace(
            context,
            origin=ORIGIN_PUBLIC,
            user=None,
            user_repr=f'paciente (enlace de ficha #{self.link.pk})',
        ))
        return self

    def __exit__(self, *exc):
        reset_audit_context(self._token)


# ---------------------------------------------------------------------------
# Completar
# ---------------------------------------------------------------------------


class LinkNoLongerUsable(Exception):
    """El enlace dejó de valer entre que se abrió la página y se envió."""


def _resolve_episode(link: PatientFormLink) -> Episode:
    """El episodio elegido si sigue abierto; si no, uno nuevo con el motivo indicado."""
    if link.episode_id:
        episode = Episode.objects.filter(pk=link.episode_id).first()
        if episode is not None and episode.status == Episode.Status.OPEN:
            return episode
    history = MedicalHistory.objects.filter(patient=link.patient).first()
    if history is None:
        history = MedicalHistory.objects.create(patient=link.patient, clinic=link.patient.clinic)
    return Episode.objects.create(
        history=history,
        reason=link.episode_reason or DEFAULT_EPISODE_REASON,
    )


def complete_link(
    link: PatientFormLink,
    *,
    questionnaire_version=None,
    answers=None,
    consent_version=None,
    signature_image=None,
    completed_by_name='',
    completed_by_relationship='',
) -> PatientFormLink:
    """Escribe lo que envió el paciente y consume el enlace. Todo o nada.

    Lanza `LinkNoLongerUsable` si entre tanto el enlace caducó, se revocó o ya se
    usó (otra pestaña, otro envío).
    """
    with transaction.atomic():
        locked = (
            PatientFormLink.objects.select_for_update()
            .select_related('patient', 'clinic')
            .get(pk=link.pk)
        )
        if not link_is_openable(locked):
            raise LinkNoLongerUsable()

        with patient_actor(locked):
            episode = _resolve_episode(locked)

            if questionnaire_version is not None:
                locked.questionnaire_response = QuestionnaireResponse.record(
                    version=questionnaire_version,
                    patient=locked.patient,
                    episode=episode,
                    answers=answers or {},
                    source=QuestionnaireResponse.Source.PATIENT_WEB,
                    created_by=None,
                )
            if consent_version is not None:
                locked.signed_consent = SignedConsent.sign(
                    version=consent_version,
                    patient=locked.patient,
                    episode=episode,
                    signature_image=signature_image,
                    signer_name=completed_by_name,
                    signer_relationship=completed_by_relationship,
                )

            locked.completed_at = timezone.now()
            locked.completed_by_name = (completed_by_name or '').strip()
            locked.completed_by_relationship = (completed_by_relationship or '').strip()
            locked.privacy_notice_version = PRIVACY_NOTICE_VERSION
            locked.save()
    return locked
