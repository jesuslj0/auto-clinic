"""Copia legible de la ficha de un paciente, en PDF.

Es lo que lee una persona: el paciente que pide su copia, o el profesional de otro
centro. Los datos completos y verificables van en los JSONL del mismo ZIP; esto es
la misma información puesta para leerla.

Reglas:

- **Misma fuente que el ZIP.** Las filas salen de `exports.schema.queryset_for`, así
  que el PDF y los JSONL no pueden discrepar en qué incluyen, y lo borrado
  lógicamente también sale (marcado).
- **Nada sale a la red.** La plantilla es autocontenida (sin CDNs ni fuentes web) y el
  `url_fetcher` rechaza cualquier URL que no sea `data:`: renderizar el PDF no puede
  filtrar la IP del servidor ni descargar nada.
- **Las fotos van reducidas.** El PDF lleva miniaturas para leerlas; los originales
  están en `adjuntos/`.
- **Si algo falla, falla todo.** Un ZIP de paciente sin su copia legible no se entrega.
"""
from __future__ import annotations

import base64
import logging
from io import BytesIO
from zoneinfo import ZoneInfo

from django.template.loader import render_to_string
from django.utils import timezone
from PIL import Image

from clinical.hashing import verify_note_hash
from clinical.soap import sections_for
from exports.schema import Scope, queryset_for

logger = logging.getLogger(__name__)

#: Lado mayor de las fotos dentro del PDF, en píxeles.
PHOTO_MAX_PX = 900
PHOTO_QUALITY = 78

TEMPLATE = 'exports/patient_document.html'


def _offline_fetcher():
    """Cargador de recursos que solo admite `data:` y que falla si se pide otra cosa.

    `fail_on_errors` hace que una referencia accidental a una URL externa (una
    fuente web, una imagen remota) pare el render en lugar de saltarse en silencio.
    """
    from weasyprint.urls import URLFetcher

    return URLFetcher(allowed_protocols=('data',), fail_on_errors=True)


def _person(professional) -> str:
    if professional is None:
        return ''
    name = str(professional)
    title = getattr(professional, 'title', '')
    return f'{title} {name}'.strip() if title else name


def _answer_text(answer) -> str | None:
    """La respuesta de una anamnesis como texto legible; `None` si quedó sin contestar.

    `False` y `0` son respuestas («no» y «cero» son datos clínicos), no ausencias.
    """
    if answer is None or answer == '' or answer == []:
        return None
    if answer is True:
        return 'Sí'
    if answer is False:
        return 'No'
    if isinstance(answer, (list, tuple)):
        return ', '.join(str(item) for item in answer)
    return str(answer)


def _inline_image(fieldfile, *, max_px=PHOTO_MAX_PX) -> str | None:
    """Una imagen del bucket como `data:` JPEG reducido, o `None` si no se puede leer."""
    if not fieldfile or not fieldfile.name:
        return None
    try:
        with fieldfile.storage.open(fieldfile.name, 'rb') as stored:
            image = Image.open(stored)
            image.load()
        if image.mode in ('RGBA', 'LA', 'P'):
            background = Image.new('RGB', image.size, (255, 255, 255))
            rgba = image.convert('RGBA')
            background.paste(rgba, mask=rgba.split()[3])
            image = background
        else:
            image = image.convert('RGB')
        image.thumbnail((max_px, max_px))
        buffer = BytesIO()
        image.save(buffer, format='JPEG', quality=PHOTO_QUALITY)
    except Exception:
        logger.warning('No se pudo incrustar %s en el PDF', fieldfile.name, exc_info=True)
        return None
    return 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')


def build_context(scope: Scope) -> dict:
    patient = scope.patient
    clinic = scope.clinic

    notes_by_visit: dict[int, list] = {}
    addenda_by_note: dict[int, list] = {}
    for addendum in queryset_for('addenda', scope).select_related('author__user').order_by('created_at', 'pk'):
        addenda_by_note.setdefault(addendum.note_id, []).append(
            {'obj': addendum, 'author': _person(addendum.author)}
        )
    for note in queryset_for('clinical_notes', scope).select_related('signed_by__user').order_by('created_at', 'pk'):
        notes_by_visit.setdefault(note.visit_id, []).append({
            'obj': note,
            'sections': sections_for(note),
            'signed_by': _person(note.signed_by),
            'sealed': bool(note.content_hash),
            'seal_ok': verify_note_hash(note) if note.content_hash else None,
            'addenda': addenda_by_note.get(note.pk, []),
        })

    procedures_by_visit: dict[int, list] = {}
    for procedure in queryset_for('performed_procedures', scope).order_by('performed_at', 'pk'):
        procedures_by_visit.setdefault(procedure.visit_id, []).append(procedure)

    visits_by_episode: dict[int, list] = {}
    for visit in queryset_for('visits', scope).select_related('professional__user').order_by('occurred_at', 'pk'):
        visits_by_episode.setdefault(visit.episode_id, []).append({
            'obj': visit,
            'professional': _person(visit.professional),
            'notes': notes_by_visit.get(visit.pk, []),
            'procedures': procedures_by_visit.get(visit.pk, []),
        })

    episodes = [
        {
            'obj': episode,
            'responsible': _person(episode.responsible_professional),
            'visits': visits_by_episode.get(episode.pk, []),
        }
        for episode in queryset_for('episodes', scope)
        .select_related('responsible_professional__user').order_by('opened_at', 'pk')
    ]

    observations_by_lesion: dict[int, list] = {}
    photos_by_observation: dict[int, list] = {}
    for attachment in queryset_for('lesion_attachments', scope).order_by('uploaded_at', 'pk'):
        uri = _inline_image(attachment.file)
        photos_by_observation.setdefault(attachment.observation_id, []).append(
            {'obj': attachment, 'uri': uri}
        )
    for observation in queryset_for('lesion_observations', scope).order_by('observed_at', 'pk'):
        observations_by_lesion.setdefault(observation.lesion_id, []).append({
            'obj': observation,
            'photos': photos_by_observation.get(observation.pk, []),
        })
    lesions = [
        {'obj': lesion, 'observations': observations_by_lesion.get(lesion.pk, [])}
        for lesion in queryset_for('lesions', scope).order_by('detected_at', 'pk')
    ]

    consents = [
        {'obj': consent, 'signature': _inline_image(consent.signature_image, max_px=500)}
        for consent in queryset_for('signed_consents', scope).order_by('signed_at', 'pk')
    ]

    payments_by_invoice: dict[int, list] = {}
    for payment in queryset_for('payments', scope).order_by('paid_at', 'pk'):
        payments_by_invoice.setdefault(payment.invoice_id, []).append(payment)
    invoices = [
        {'obj': invoice, 'lines': invoice.lines or [], 'payments': payments_by_invoice.get(invoice.pk, [])}
        for invoice in queryset_for('invoices', scope).order_by('created_at', 'pk')
    ]

    guardians = {g.pk: g for g in queryset_for('guardians', scope)}
    guardian_links = [
        {'obj': link, 'guardian': guardians.get(link.guardian_id)}
        for link in queryset_for('patient_guardians', scope).order_by('pk')
    ]

    return {
        'clinic': clinic,
        'patient': patient,
        'generated_at': timezone.now(),
        'history': queryset_for('medical_histories', scope).first(),
        'guardian_links': guardian_links,
        'alerts': list(queryset_for('clinical_alerts', scope).order_by('-is_active', 'created_at', 'pk')),
        'episodes': episodes,
        'responses': [
            {
                'obj': response,
                'entries': [
                    {'text': entry.get('text', ''), 'answer': _answer_text(entry.get('answer'))}
                    for entry in (response.snapshot or [])
                ],
            }
            for response in queryset_for('questionnaire_responses', scope).order_by('filled_at', 'pk')
        ],
        'lesions': lesions,
        'consents': consents,
        'appointments': list(queryset_for('appointments', scope).order_by('scheduled_at')),
        'invoices': invoices,
        'chat_messages': queryset_for('chat_messages', scope).count(),
    }


def render_patient_html(scope: Scope) -> str:
    """El HTML autocontenido de la ficha, con las fechas en la zona de la clínica."""
    try:
        zone = ZoneInfo(scope.clinic.timezone or 'Europe/Madrid')
    except Exception:
        zone = ZoneInfo('Europe/Madrid')
    with timezone.override(zone):
        return render_to_string(TEMPLATE, build_context(scope))


def render_patient_pdf(scope: Scope) -> bytes:
    """El PDF de la ficha completa del paciente del ámbito."""
    from weasyprint import HTML
    from weasyprint.urls import FatalURLFetchingError

    html = render_patient_html(scope)
    try:
        return HTML(string=html, url_fetcher=_offline_fetcher()).write_pdf()
    except FatalURLFetchingError as exc:
        # Hereda de `BaseException`: sin este cambio saltaría por encima de los
        # `except Exception` de quien llama y dejaría la exportación «generando».
        raise RuntimeError(f'El PDF intentó cargar un recurso externo: {exc}') from exc
