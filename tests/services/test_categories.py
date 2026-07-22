"""Categorías del catálogo: semillas, aislamiento por clínica y alta desde el panel."""
import pytest
from django.core.exceptions import ValidationError as DjangoValidationError
from django.urls import reverse

from core.models import Clinic
from services.models import DEFAULT_CATEGORIES, Service, ServiceCategory


@pytest.mark.django_db
class TestDefaultCategories:
    def test_una_clinica_nueva_nace_con_las_categorias_de_podologia(self):
        clinic = Clinic.objects.create(name='Podología Norte')

        nombres = set(ServiceCategory.objects.filter(clinic=clinic).values_list('name', flat=True))

        assert nombres == {nombre for nombre, _ in DEFAULT_CATEGORIES}

    def test_las_semillas_traen_color(self):
        clinic = Clinic.objects.create(name='Podología Sur')

        quiropodia = ServiceCategory.objects.get(clinic=clinic, name='Quiropodia')

        assert quiropodia.color.startswith('#')

    def test_no_se_resiembran_al_editar_la_clinica(self, clinic_a):
        antes = ServiceCategory.objects.filter(clinic=clinic_a).count()

        clinic_a.name = 'Otro nombre'
        clinic_a.save()

        assert ServiceCategory.objects.filter(clinic=clinic_a).count() == antes


@pytest.mark.django_db
class TestCategoryIsolation:
    def test_un_servicio_no_puede_colgar_de_la_categoria_de_otra_clinica(self, clinic_a, clinic_b):
        ajena = ServiceCategory.objects.get(clinic=clinic_b, name='Quiropodia')
        servicio = Service(
            clinic=clinic_a, category=ajena, name='Quiropodia', duration_minutes=30, price='40.00',
        )

        with pytest.raises(DjangoValidationError) as exc:
            servicio.clean()

        assert 'category' in exc.value.message_dict

    def test_la_api_rechaza_la_categoria_de_otra_clinica(self, admin_client, clinic_a, clinic_b):
        ajena = ServiceCategory.objects.get(clinic=clinic_b, name='Quiropodia')

        response = admin_client.post(
            '/api/services/',
            {
                'clinic': clinic_a.pk,
                'category': ajena.pk,
                'name': 'Quiropodia',
                'duration_minutes': 30,
                'price': '40.00',
            },
        )

        assert response.status_code == 400
        assert 'category' in response.data

    def test_la_api_solo_lista_las_categorias_propias(self, admin_client, clinic_a, clinic_b):
        response = admin_client.get('/api/service-categories/')

        assert response.status_code == 200
        clinicas = {c['clinic'] for c in response.data['results']}
        assert clinicas == {clinic_a.pk}


@pytest.mark.django_db
class TestCategoryFromServiceForm:
    def test_asigna_una_categoria_existente_por_nombre(self, client, admin_user, service_a, clinic_a):
        client.force_login(admin_user)

        response = client.post(
            reverse('services:edit', args=[service_a.pk]),
            {
                'name': service_a.name,
                'description': '',
                'category_name': 'Quiropodia',
                'duration_minutes': 30,
                'price': '50.00',
                'is_active': 'on',
            },
        )

        assert response.status_code == 302
        service_a.refresh_from_db()
        assert service_a.category == ServiceCategory.objects.get(clinic=clinic_a, name='Quiropodia')

    def test_crea_la_categoria_si_no_existe(self, client, admin_user, service_a, clinic_a):
        client.force_login(admin_user)

        client.post(
            reverse('services:edit', args=[service_a.pk]),
            {
                'name': service_a.name,
                'description': '',
                'category_name': 'Reflexología',
                'duration_minutes': 30,
                'price': '50.00',
                'is_active': 'on',
            },
        )

        service_a.refresh_from_db()
        assert service_a.category.name == 'Reflexología'
        assert service_a.category.clinic == clinic_a
        assert service_a.category.color.startswith('#')

    def test_no_duplica_por_mayusculas(self, client, admin_user, service_a, clinic_a):
        client.force_login(admin_user)

        client.post(
            reverse('services:edit', args=[service_a.pk]),
            {
                'name': service_a.name,
                'description': '',
                'category_name': 'quiropodia',
                'duration_minutes': 30,
                'price': '50.00',
                'is_active': 'on',
            },
        )

        service_a.refresh_from_db()
        assert ServiceCategory.objects.filter(clinic=clinic_a, name__iexact='quiropodia').count() == 1
        assert service_a.category.name == 'Quiropodia'

    def test_dejarla_en_blanco_la_desasigna(self, client, admin_user, service_a, clinic_a):
        service_a.category = ServiceCategory.objects.get(clinic=clinic_a, name='Quiropodia')
        service_a.save(update_fields=['category'])
        client.force_login(admin_user)

        client.post(
            reverse('services:edit', args=[service_a.pk]),
            {
                'name': service_a.name,
                'description': '',
                'category_name': '',
                'duration_minutes': 30,
                'price': '50.00',
                'is_active': 'on',
            },
        )

        service_a.refresh_from_db()
        assert service_a.category is None
