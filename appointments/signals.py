from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db.models.signals import post_save
from django.dispatch import receiver

from appointments.models import Appointment, Professional
from core.models import User


@receiver(post_save, sender=Appointment)
def broadcast_appointment_update(sender, instance, created, **kwargs):
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f'clinic_{instance.clinic_id}_appointments',
        {
            'type': 'appointment_update',
            'payload': {
                'id': instance.id,
                'status': instance.status,
                'scheduled_at': instance.scheduled_at.isoformat(),
                'created': created,
            },
        },
    )


@receiver(post_save, sender=User)
def ensure_professional_for_user(sender, instance, **kwargs):
    if not instance.role:
        return

    if not instance.clinic_id:
        Professional.objects.filter(user=instance).delete()
        return

    professional, _ = Professional.objects.get_or_create(
        user=instance,
        defaults={'clinic': instance.clinic},
    )

    if professional.clinic_id != instance.clinic_id:
        professional.clinic = instance.clinic
        professional.save(update_fields=['clinic'])
