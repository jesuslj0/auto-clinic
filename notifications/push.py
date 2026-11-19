"""Web Push (VAPID). Sin claves configuradas, el push queda desactivado y el in-app sigue.

El payload es SIEMPRE genérico: pasa por Google/Apple/Mozilla. Ver `inbox.py`.
"""

import json
import logging

from django.conf import settings
from django.utils import timezone

from notifications.models import Notification, NotificationPreference, PushSubscription

logger = logging.getLogger(__name__)

TTL_SECONDS = 60 * 60
MAX_FAILURES = 5


def build_payload(notification) -> dict:
    return {
        'title': notification.title,
        'body': notification.body,
        'url': notification.url or '/notificaciones/',
        'tag': f'{notification.kind}:{notification.object_id}' if notification.object_id else notification.kind,
    }


def _webpush(**kwargs):
    # Import perezoso: sin la dependencia instalada el resto del panel arranca igual.
    from pywebpush import webpush

    return webpush(**kwargs)


def deliver(notification_ids) -> int:
    """Manda el push de cada notificación a las suscripciones de su usuario. Devuelve los envíos."""
    if not (settings.VAPID_PRIVATE_KEY and settings.VAPID_PUBLIC_KEY):
        return 0
    from pywebpush import WebPushException

    sent = 0
    notifications = Notification.objects.filter(pk__in=notification_ids, read_at=None, dismissed_at=None)
    for notification in notifications:
        pref = NotificationPreference.objects.filter(user_id=notification.user_id, kind=notification.kind).first()
        if pref is not None and not pref.push:
            continue
        data = json.dumps(build_payload(notification))
        for sub in PushSubscription.objects.filter(user_id=notification.user_id):
            try:
                _webpush(
                    subscription_info={'endpoint': sub.endpoint, 'keys': {'p256dh': sub.p256dh, 'auth': sub.auth}},
                    data=data,
                    vapid_private_key=settings.VAPID_PRIVATE_KEY,
                    vapid_claims={'sub': f'mailto:{settings.VAPID_CLAIMS_EMAIL}'},
                    ttl=TTL_SECONDS,
                )
            except WebPushException as exc:
                status = getattr(getattr(exc, 'response', None), 'status_code', None)
                if status in (404, 410):
                    sub.delete()
                else:
                    sub.failures += 1
                    if sub.failures >= MAX_FAILURES:
                        sub.delete()
                    else:
                        sub.save(update_fields=['failures'])
                    logger.warning('Push fallido (%s) para la suscripción %s', status, sub.pk)
            except Exception:
                logger.exception('Error inesperado enviando push')
            else:
                sub.last_success_at = timezone.now()
                sub.failures = 0
                sub.save(update_fields=['last_success_at', 'failures'])
                sent += 1
    return sent
