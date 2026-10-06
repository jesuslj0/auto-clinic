"""Lista de profesionales: los servicios se resumen en «N más» y no multiplican consultas."""
import pytest
from django.urls import reverse

from services.models import Service


@pytest.mark.django_db
def test_los_servicios_se_resumen(client, admin_user, clinic_a, professional_a):
    for index in range(6):
        service = Service.objects.create(
            clinic=clinic_a, name=f'Servicio {index}', price=10, duration_minutes=30,
        )
        professional_a.services.add(service)
    total = professional_a.services.count()
    client.force_login(admin_user)

    html = client.get(reverse('professionals:list')).content.decode()

    # Tabla: 3 y «N más». Tarjeta de móvil: 2 y «M más».
    assert f'>{total - 3} más<' in html and f'>{total - 2} más<' in html
    assert html.count('Servicio 5') >= 2  # solo en el `title` con la lista completa


@pytest.mark.django_db
def test_sin_servicios_lo_dice(client, admin_user, professional_a):
    professional_a.services.clear()
    client.force_login(admin_user)

    html = client.get(reverse('professionals:list')).content.decode()

    assert 'Sin servicios' in html
