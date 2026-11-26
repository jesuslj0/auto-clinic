"""Notificaciones in-app del panel: un único punto de entrada, `notify()`.

Reglas (ver CLAUDE.md, «Notificaciones»):

- **Push genérico siempre; el panel puede llevar el nombre.** `title`/`body` no
  llevan nombre de paciente, teléfono ni texto de mensajes: viajan por
  Google/Apple, y un nombre junto a «cita» ya es dato de salud. El nombre va solo
  en `detail`, que únicamente se pinta en el panel (sesión). Nunca el contenido
  de un mensaje. Ampliar el push es una decisión de privacidad, no de UI.
- **Nunca dentro de la petición que lo origina.** Todo sale con `on_commit` y un
  fallo aquí no tumba la reserva ni el mensaje de WhatsApp.
- **Agrupa mientras no se lea.** Misma persona, tipo y objeto con una sin leer →
  se actualiza esa fila (`count`, `created_at`) en vez de apilar otra.
- Los hilos de prueba y los usuarios sin clínica no generan ni reciben avisos.

Import local desde `appointments`/`agent`/`clinical`: `notifications` los importa
a ellos, no al revés.
"""

import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.models import User
from core.realtime import clinic_group_name, send_to_group
from notifications.consumers import NOTIFICATIONS_STREAM
from notifications.models import Notification, NotificationPreference

logger = logging.getLogger(__name__)

Kind = Notification.Kind

#: (título, cuerpo) genéricos por categoría. `{n}` se rellena con el contador.
TEXTS = {
    Kind.APPOINTMENT_PENDING: ('Nueva cita pendiente', 'El agente ha reservado una cita.'),
    Kind.APPOINTMENT_RESCHEDULED: ('Cita reagendada', 'Un paciente ha movido su cita.'),
    Kind.APPOINTMENT_CANCELLED: ('Cita cancelada', 'Se ha cancelado una cita.'),
    Kind.CHAT_MESSAGE: ('Mensaje nuevo', 'Tienes mensajes sin leer en un chat.'),
    Kind.CHAT_NEW: ('Conversación nueva', 'Alguien ha escrito por primera vez.'),
    Kind.MESSAGE_FAILED: ('Mensaje no entregado', 'Un mensaje de WhatsApp no ha llegado.'),
    Kind.PATIENT_FORM_COMPLETED: ('Ficha completada', 'Un paciente ha rellenado su ficha.'),
    Kind.TEMPLATE_STATUS: ('Plantilla de WhatsApp revisada', 'Meta ha respondido a una plantilla.'),
    Kind.EXPORT_READY: ('Exportación de datos', 'Una exportación ha terminado.'),
}
#: Cuerpo cuando una fila agrupa varios avisos.
GROUPED_BODY = '{n} avisos nuevos.'

#: Categorías que solo ven los administradores de la clínica.
ADMIN_ONLY = {Kind.TEMPLATE_STATUS, Kind.EXPORT_READY}


def kind_is_valid(kind) -> bool:
    return kind in Kind.values


def push_enabled() -> bool:
    return bool(getattr(settings, 'VAPID_PRIVATE_KEY', '') and getattr(settings, 'VAPID_PUBLIC_KEY', ''))


def recipients(clinic, kind, *, exclude_user=None, only_user=None):
    """Usuarios activos de la clínica a los que este tipo les interesa (in-app).

    `only_user` limita el aviso a una sola persona (la que pidió una exportación,
    por ejemplo): sigue pasando por el filtro de rol y por sus preferencias.
    """
    users = User.objects.filter(clinic=clinic, is_active=True)
    if kind in ADMIN_ONLY:
        users = users.filter(role=User.Role.ADMIN)
    if exclude_user is not None:
        users = users.exclude(pk=getattr(exclude_user, 'pk', exclude_user))
    if only_user is not None:
        users = users.filter(pk=getattr(only_user, 'pk', only_user))
    muted = set(
        NotificationPreference.objects.filter(kind=kind, in_app=False).values_list('user_id', flat=True)
    )
    return [u for u in users if u.pk not in muted]


def notify(clinic, kind, *, url='', object_id='', exclude_user=None, only_user=None, detail='') -> None:
    """Avisa al personal de la clínica. Se ejecuta al confirmarse la transacción."""
    if clinic is None or not kind_is_valid(kind):
        return
    object_id = str(object_id or '')
    detail = str(detail or '')[:200]
    clinic_pk = clinic.pk

    def run():
        try:
            with transaction.atomic():
                _deliver(clinic_pk, kind, url=url, object_id=object_id, exclude_user=exclude_user,
                         only_user=only_user, detail=detail)
        except Exception:
            # El aviso es un extra: nunca debe romper lo que lo originó.
            logger.exception('No se pudo crear la notificación %s', kind)

    transaction.on_commit(run)


def _deliver(clinic_pk, kind, *, url, object_id, exclude_user, only_user=None, detail=''):
    from core.models import Clinic

    clinic = Clinic.objects.filter(pk=clinic_pk).first()
    if clinic is None:
        return
    users = recipients(clinic, kind, exclude_user=exclude_user, only_user=only_user)
    if not users:
        return

    title, body = TEXTS[kind]
    now = timezone.now()
    touched = []
    for user in users:
        existing = None
        if object_id:
            existing = (
                Notification.objects.select_for_update()
                .filter(user=user, kind=kind, object_id=object_id, read_at=None, dismissed_at=None)
                .first()
            )
        if existing is not None:
            existing.count += 1
            existing.created_at = now
            existing.url = url or existing.url
            existing.body = GROUPED_BODY.format(n=existing.count)
            existing.detail = detail or existing.detail
            existing.save(update_fields=['count', 'created_at', 'url', 'body', 'detail'])
            touched.append(existing.pk)
        else:
            created = Notification.objects.create(
                clinic=clinic, user=user, kind=kind, title=title, body=body, detail=detail,
                url=url, object_id=object_id, created_at=now,
            )
            touched.append(created.pk)

    send_to_group(
        clinic_group_name(NOTIFICATIONS_STREAM, clinic_pk),
        {'type': 'notification_new', 'payload': {'kind': kind}},
    )
    if push_enabled():
        try:
            from notifications.tasks import send_web_push

            send_web_push.delay(touched)
        except Exception:
            logger.exception('No se pudo encolar el push')


def unread_count(user) -> int:
    return Notification.objects.filter(user=user, read_at=None, dismissed_at=None).count()


def unread_total(user) -> int:
    """Para el context processor: 0 si el usuario no recibe avisos."""
    if not user.is_authenticated or not user.clinic_id:
        return 0
    return unread_count(user)
