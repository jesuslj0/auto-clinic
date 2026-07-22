from django.db.models.signals import post_save
from django.dispatch import receiver

from core.models import Clinic
from services.models import DEFAULT_CATEGORIES, ServiceCategory


@receiver(post_save, sender=Clinic)
def create_default_categories(sender, instance, created, **kwargs):
    """
    Una clínica recién dada de alta arranca con el catálogo de podología ya
    categorizado, para que no tenga que inventarse la estructura antes de
    poder crear su primer servicio. Son solo semillas: puede renombrarlas o
    borrarlas.
    """
    if not created:
        return

    ServiceCategory.objects.bulk_create(
        [
            ServiceCategory(clinic=instance, name=nombre, color=color)
            for nombre, color in DEFAULT_CATEGORIES
        ],
        ignore_conflicts=True,
    )
