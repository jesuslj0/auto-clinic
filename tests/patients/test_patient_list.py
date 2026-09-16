"""Directorio de pacientes: búsqueda, orden, filtros calculados y paginación."""
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from appointments.models import Appointment
from patients.models import Patient


def _patient(clinic, first, last, *, days_ago=0):
    patient = Patient.objects.create(
        clinic=clinic, first_name=first, last_name=last,
        email=f'{first.lower()}.{last.lower()}@example.com', phone=f'600{abs(hash(first + last)) % 1000000:06d}',
    )
    # `created_at` es auto_now_add: se retrocede después de crear (solo en tests).
    Patient.objects.filter(pk=patient.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
    return patient


def _appointment(patient, service, professional, *, days, status):
    start = timezone.now() + timedelta(days=days)
    return Appointment.objects.create(
        clinic=patient.clinic, patient=patient, service=service, professional=professional,
        scheduled_at=start, end_at=start + timedelta(minutes=30), status=status,
    )


def _names(response):
    return [str(p) for p in response.context['patients']]


@pytest.fixture
def listing(client, admin_user):
    client.force_login(admin_user)
    return lambda **params: client.get(reverse('patients:list'), params)


@pytest.mark.django_db
class TestPatientList:
    def test_default_order_is_most_recent_first(self, listing, clinic_a):
        _patient(clinic_a, 'Vieja', 'Alta', days_ago=100)
        _patient(clinic_a, 'Nueva', 'Alta', days_ago=1)
        assert _names(listing()) == ['Nueva Alta', 'Vieja Alta']
        assert _names(listing(orden='antiguos')) == ['Vieja Alta', 'Nueva Alta']

    def test_unknown_sort_falls_back_to_default(self, listing, clinic_a):
        _patient(clinic_a, 'Ana', 'Zeta', days_ago=5)
        _patient(clinic_a, 'Bea', 'Alfa', days_ago=1)
        assert _names(listing(orden='lo-que-sea')) == ['Bea Alfa', 'Ana Zeta']
        assert _names(listing(orden='apellidos')) == ['Bea Alfa', 'Ana Zeta']

    def test_full_name_search_matches_across_fields(self, listing, clinic_a):
        _patient(clinic_a, 'Ana', 'López')
        _patient(clinic_a, 'Ana', 'García')
        assert _names(listing(q='Ana López')) == ['Ana López']

    def test_upcoming_appointment_filter(self, listing, clinic_a, service_a, professional_a):
        con = _patient(clinic_a, 'Con', 'Cita')
        cancelada = _patient(clinic_a, 'Solo', 'Cancelada')
        _patient(clinic_a, 'Sin', 'Nada')
        _appointment(con, service_a, professional_a, days=3, status=Appointment.Status.CONFIRMED)
        _appointment(cancelada, service_a, professional_a, days=4, status=Appointment.Status.CANCELLED)

        assert _names(listing(cita='proxima')) == ['Con Cita']
        assert sorted(_names(listing(cita='sin'))) == ['Sin Nada', 'Solo Cancelada']

    def test_inactive_filter_ignores_recent_signups(self, listing, clinic_a, service_a, professional_a):
        perdido = _patient(clinic_a, 'Perdido', 'Hace', days_ago=400)
        reciente = _patient(clinic_a, 'Vino', 'Hace', days_ago=400)
        _patient(clinic_a, 'Nunca', 'Viejo', days_ago=400)
        _patient(clinic_a, 'Nunca', 'Nuevo', days_ago=2)
        _appointment(perdido, service_a, professional_a, days=-200, status=Appointment.Status.COMPLETED)
        _appointment(reciente, service_a, professional_a, days=-20, status=Appointment.Status.COMPLETED)

        assert sorted(_names(listing(inactivo='6'))) == ['Nunca Viejo', 'Perdido Hace']

    def test_last_visit_ignores_no_shows(self, listing, clinic_a, service_a, professional_a):
        patient = _patient(clinic_a, 'Falta', 'Siempre', days_ago=400)
        _appointment(patient, service_a, professional_a, days=-10, status=Appointment.Status.NO_SHOW)
        assert listing().context['patients'][0].last_visit is None
        assert _names(listing(inactivo='3')) == ['Falta Siempre']

    def test_joined_filter(self, listing, clinic_a):
        _patient(clinic_a, 'Nuevo', 'Uno', days_ago=3)
        _patient(clinic_a, 'Viejo', 'Dos', days_ago=90)
        assert _names(listing(alta='30d')) == ['Nuevo Uno']

    def test_count_excludes_cancelled(self, listing, clinic_a, service_a, professional_a):
        patient = _patient(clinic_a, 'Cuenta', 'Citas')
        _appointment(patient, service_a, professional_a, days=2, status=Appointment.Status.PENDING)
        _appointment(patient, service_a, professional_a, days=5, status=Appointment.Status.CANCELLED)
        assert listing().context['patients'][0].appointment_count == 1

    def test_pagination_keeps_filters(self, listing, clinic_a):
        for i in range(25):
            _patient(clinic_a, f'P{i:02d}', 'Pag', days_ago=i)
        response = listing(alta='30d', orden='apellidos')
        assert response.context['is_paginated']
        assert len(response.context['patients']) == 20
        assert 'orden=apellidos&amp;alta=30d&amp;page=2' in response.content.decode()

    def test_other_clinic_patients_are_not_listed(self, listing, clinic_a, patient_b):
        _patient(clinic_a, 'Mia', 'Clinica')
        assert _names(listing()) == ['Mia Clinica']
