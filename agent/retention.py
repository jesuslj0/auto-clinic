"""Retención de las conversaciones de WhatsApp (RGPD art. 5.1.e).

`Clinic.conversation_retention_months` (24 por defecto, 0 = sin límite) dice
cuánto se conserva el contenido de un hilo. Aquí está lo que lo hace cumplir.

Qué se hace, y qué no:

- **El criterio es la inactividad del hilo.** Un hilo cuyo último mensaje es
  anterior al plazo pierde sus mensajes. Mientras el paciente siga escribiendo,
  no se toca nada: el hilo no se recorta por el principio.
- **Se borran los mensajes, no la conversación.** `ConversationSession` se queda
  (vincula teléfono, ficha y contacto responsable), sin la vista previa ni el
  estado del bot, que contienen texto del hilo.
- **Las fotos quedan fuera** (decisión del 2026-10-29). Una foto que mandó un
  paciente puede ser la base de una decisión clínica y, hasta que se pueda
  promover a `Lesion`, borrarla destruiría documentación que la Ley 41/2002
  manda conservar. Su mensaje (con el pie de foto) se queda con ella. Cuando
  exista esa promoción habrá que decidir si caducan.
- **Las notas de voz sí**, y su objeto del bucket privado también: borrar la
  fila y dejar el audio en R2 sería una purga de mentira.
- **`AgentMemory`** del teléfono se borra también: es el contexto del LLM y
  lleva el texto de la conversación.

Cada mensaje se borra con `instance.delete()`, no con `queryset.delete()`: es un
modelo auditado y así deja su `ChangeLog` de baja (con `body` y `raw` enmascarados,
como siempre). Además queda un `ChangeLog` resumen por clínica con el recuento y el
rango de fechas, para que el hueco en el hilo nunca sea silencioso.
"""
from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass, field
from datetime import datetime

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from agent.models import AgentMemory, ChatAttachment, ChatMessage, ConversationSession
from audit.context import ORIGIN_COMMAND, audit_context
from audit.models import ChangeLog
from core.models import Clinic

logger = logging.getLogger(__name__)

#: Plazo para los hilos sin clínica (la FK es `SET_NULL`), que no tienen ajuste propio.
DEFAULT_RETENTION_MONTHS = 24

ACTOR = 'comando purge_conversations'


@dataclass
class ClinicReport:
    sessions: int = 0
    messages: int = 0
    audio_files: int = 0
    photos_kept: int = 0
    memories: int = 0
    oldest: datetime | None = None
    newest: datetime | None = None
    months: int = DEFAULT_RETENTION_MONTHS


@dataclass
class PurgeReport:
    by_clinic: dict = field(default_factory=dict)

    @property
    def messages(self) -> int:
        return sum(r.messages for r in self.by_clinic.values())

    @property
    def sessions(self) -> int:
        return sum(r.sessions for r in self.by_clinic.values())

    @property
    def audio_files(self) -> int:
        return sum(r.audio_files for r in self.by_clinic.values())


def months_before(moment: datetime, months: int) -> datetime:
    """`moment` menos `months` meses de calendario (el día se acota al fin de mes)."""
    index = moment.year * 12 + (moment.month - 1) - months
    year, month = divmod(index, 12)
    month += 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def _targets(now):
    """`(clinic_id, meses, fecha de corte)` de cada grupo con plazo. 0 meses = sin límite."""
    for clinic_id, months in Clinic.objects.values_list('clinic_id', 'conversation_retention_months'):
        if months > 0:
            yield clinic_id, months, months_before(now, months)
    yield None, DEFAULT_RETENTION_MONTHS, months_before(now, DEFAULT_RETENTION_MONTHS)


def _session_ids(clinic_id, cutoff):
    return list(
        ConversationSession.objects.filter(clinic_id=clinic_id, last_message_at__lt=cutoff)
        .order_by('last_message_at')
        .values_list('pk', flat=True)
    )


def _memory_filter(session: ConversationSession) -> Q:
    """El `session_id` de `AgentMemory` es el teléfono tal y como lo escribió n8n."""
    bare = session.phone.lstrip('+')
    return Q(clinic_id=session.clinic_id, session_id__in={session.phone, bare, f'+{bare}'})


def _purgeable_messages(session: ConversationSession):
    """Todo salvo los mensajes que llevan una foto."""
    return session.messages.exclude(attachment__kind=ChatAttachment.Kind.IMAGE)


def _purge_session(session_id, cutoff, report: ClinicReport, *, dry_run: bool) -> None:
    with transaction.atomic():
        session = ConversationSession.objects.select_for_update().get(pk=session_id)
        # Entre elegirlo y bloquearlo el paciente pudo volver a escribir.
        if session.last_message_at is None or session.last_message_at >= cutoff:
            return

        messages = list(_purgeable_messages(session).select_related('attachment'))
        photos = session.messages.filter(attachment__kind=ChatAttachment.Kind.IMAGE).count()
        memories = AgentMemory.objects.filter(_memory_filter(session))

        report.sessions += 1
        report.photos_kept += photos
        report.memories += memories.count()
        report.messages += len(messages)
        for message in messages:
            stamp = message.created_at
            report.oldest = stamp if report.oldest is None or stamp < report.oldest else report.oldest
            report.newest = stamp if report.newest is None or stamp > report.newest else report.newest

        keys = [m.attachment.file.name for m in messages if m.media is not None]
        report.audio_files += len(keys)
        if dry_run:
            return

        for message in messages:
            message.delete()
        memories.delete()
        # `ConversationSession` y `AgentMemory` no están en el registro de auditoría,
        # así que el `update` en bloque no se salta nada que se auditara.
        ConversationSession.objects.filter(pk=session.pk).update(
            last_message_preview='', unread_count=0, session_data={}, appointment_context={},
        )
        # Tras confirmar: si la transacción se revierte, los audios siguen en su sitio.
        transaction.on_commit(lambda: _delete_objects(keys))


def _delete_objects(keys) -> None:
    storage = ChatAttachment._meta.get_field('file').storage
    for key in keys:
        try:
            storage.delete(key)
        except Exception:  # noqa: BLE001 — un fallo del bucket no debe parar la purga
            logger.error('purge_conversations: no se pudo borrar el objeto %s del bucket', key, exc_info=True)


def purge_conversations(*, now=None, dry_run: bool = False) -> PurgeReport:
    now = now or timezone.now()
    report = PurgeReport()

    with audit_context(origin=ORIGIN_COMMAND, user_repr=ACTOR):
        for clinic_id, months, cutoff in _targets(now):
            clinic_report = ClinicReport(months=months)
            for session_id in _session_ids(clinic_id, cutoff):
                _purge_session(session_id, cutoff, clinic_report, dry_run=dry_run)
            if clinic_report.sessions:
                report.by_clinic[clinic_id] = clinic_report
                if not dry_run and clinic_report.messages:
                    _write_summary(clinic_id, clinic_report, cutoff)
    return report


def _write_summary(clinic_id, clinic_report: ClinicReport, cutoff) -> None:
    ChangeLog.objects.create(
        action=ChangeLog.Action.DELETE,
        model_label=ChatMessage._meta.label,
        object_repr=(
            f'Purga de {clinic_report.messages} mensajes de chat '
            f'anteriores a {cutoff:%Y-%m-%d}'
        ),
        origin=ORIGIN_COMMAND,
        user_repr=ACTOR,
        changes={
            'clinic_id': clinic_id,
            'retention_months': clinic_report.months,
            'cutoff': cutoff.isoformat(),
            'purged_messages': clinic_report.messages,
            'purged_audio_files': clinic_report.audio_files,
            'purged_agent_memories': clinic_report.memories,
            'conversations': clinic_report.sessions,
            'photos_kept': clinic_report.photos_kept,
            'oldest_purged': clinic_report.oldest.isoformat() if clinic_report.oldest else None,
            'newest_purged': clinic_report.newest.isoformat() if clinic_report.newest else None,
        },
    )
