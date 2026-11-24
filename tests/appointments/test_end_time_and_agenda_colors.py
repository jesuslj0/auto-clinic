"""Hora de fin editable en las citas, color por servicio y rejilla de media hora.

- Alta y edición del panel aceptan `end_time`; vacía = duración del servicio.
- Editar solo el profesional o solo la hora de inicio NO encoge una cita alargada.
- La agenda pinta cada cita con el color de su servicio (el estado, solo el punto)
  y coloca la tarjeta por minutos sobre filas de 30 min.
"""
from datetime import time, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from appointments.models import Appointment, ProfessionalSchedule
from services.models import SERVICE_PALETTE, Service


@pytest.fixture
def manana():
    return timezone.localdate() + timedelta(days=1)


@pytest.fixture
def panel_client(client, admin_user):
    client.force_login(admin_user)
    return client


def _post_create(client, patient, service, professional, day, **extra):
    data = {
        'patient': patient.pk, 'service': service.pk, 'professional': professional.pk,
        'date': day.isoformat(), 'time': '10:00', 'notes': '',
    }
    data.update(extra)
    return client.post(reverse('appointments:create'), data)


@pytest.mark.django_db
class TestHoraDeFinEnElAlta:
    def test_sin_hora_de_fin_dura_lo_que_el_servicio(
        self, panel_client, patient_a, service_a, professional_a, manana
    ):
        _post_create(panel_client, patient_a, service_a, professional_a, manana)
        cita = Appointment.objects.get()
        assert cita.end_at - cita.scheduled_at == timedelta(minutes=service_a.booking_duration_minutes)

    def test_con_hora_de_fin_manda_la_que_se_pone(
        self, panel_client, patient_a, service_a, professional_a, manana
    ):
        _post_create(panel_client, patient_a, service_a, professional_a, manana, end_time='11:15')
        cita = Appointment.objects.get()
        assert cita.end_at - cita.scheduled_at == timedelta(minutes=75)
        assert service_a.booking_duration_minutes == 30

    def test_el_fin_no_puede_ser_anterior_al_inicio(
        self, panel_client, patient_a, service_a, professional_a, manana
    ):
        response = _post_create(
            panel_client, patient_a, service_a, professional_a, manana, end_time='09:30',
        )
        assert response.status_code == 200
        assert 'posterior a la de inicio' in response.content.decode()
        assert not Appointment.objects.exists()

    def test_la_cita_alargada_bloquea_el_hueco_hasta_su_fin(
        self, panel_client, patient_a, service_a, professional_a, manana
    ):
        _post_create(panel_client, patient_a, service_a, professional_a, manana, end_time='11:00')
        response = _post_create(
            panel_client, patient_a, service_a, professional_a, manana, time='10:45',
        )
        assert response.status_code == 200  # rechazada: solapa con la anterior
        assert Appointment.objects.count() == 1


@pytest.mark.django_db
class TestHoraDeFinEnLaEdicion:
    @pytest.fixture
    def cita(self, clinic_a, patient_a, service_a, professional_a, manana):
        inicio = timezone.make_aware(
            timezone.datetime.combine(manana, time(10, 0)), timezone.get_current_timezone(),
        )
        return Appointment.objects.create(
            clinic=clinic_a, patient=patient_a, service=service_a, professional=professional_a,
            scheduled_at=inicio, end_at=inicio + timedelta(minutes=90),
            status=Appointment.Status.CONFIRMED,
        )

    def _edit(self, client, cita, **changes):
        tz = timezone.get_current_timezone()
        local = cita.scheduled_at.astimezone(tz)
        end = cita.end_at.astimezone(tz)
        data = {
            'service': cita.service_id, 'professional': cita.professional_id,
            'date': local.date().isoformat(), 'time': local.strftime('%H:%M'),
            'end_time': end.strftime('%H:%M'), 'notes': cita.notes, 'notify_patient': '',
        }
        data.update(changes)
        return client.post(reverse('appointments:update', args=[cita.pk]), data)

    def test_cambiar_el_fin(self, panel_client, cita):
        self._edit(panel_client, cita, end_time='10:45')
        cita.refresh_from_db()
        assert cita.end_at - cita.scheduled_at == timedelta(minutes=45)

    def test_cambiar_solo_la_nota_no_toca_el_fin(self, panel_client, cita):
        fin = cita.end_at
        self._edit(panel_client, cita, notes='traer informe')
        cita.refresh_from_db()
        assert cita.end_at == fin

    def test_mover_el_inicio_conserva_la_duracion(self, panel_client, cita):
        self._edit(panel_client, cita, time='14:00')
        cita.refresh_from_db()
        assert cita.end_at - cita.scheduled_at == timedelta(minutes=90)

    def test_cambiar_de_servicio_recalcula_la_duracion(
        self, panel_client, cita, clinic_a, professional_a
    ):
        otro = Service.objects.create(clinic=clinic_a, name='Corta', duration_minutes=20, price='10')
        professional_a.services.add(otro)
        self._edit(panel_client, cita, service=otro.pk)
        cita.refresh_from_db()
        assert cita.end_at - cita.scheduled_at == timedelta(minutes=20)


@pytest.mark.django_db
class TestColorDelServicio:
    def test_un_servicio_nuevo_recibe_un_color_de_la_paleta(self, clinic_a):
        a = Service.objects.create(clinic=clinic_a, name='A', duration_minutes=30, price='1')
        b = Service.objects.create(clinic=clinic_a, name='B', duration_minutes=30, price='1')
        assert a.color in SERVICE_PALETTE and b.color in SERVICE_PALETTE
        assert a.color != b.color

    def test_el_color_elegido_se_respeta(self, clinic_a):
        s = Service.objects.create(clinic=clinic_a, name='C', duration_minutes=30, price='1', color='#123456')
        assert s.color == '#123456'

    def test_el_formulario_rechaza_un_color_invalido(self, clinic_a):
        from services.forms import ServiceForm

        form = ServiceForm(
            {'name': 'X', 'duration_minutes': 30, 'price': '5', 'color': 'rojo', 'is_active': 'on'},
            clinic=clinic_a,
        )
        assert not form.is_valid()
        assert 'color' in form.errors


@pytest.mark.django_db
class TestRejillaDeLaAgenda:
    @pytest.fixture
    def jornada(self, professional_a):
        ProfessionalSchedule.objects.filter(professional=professional_a).delete()
        for dow in range(7):
            ProfessionalSchedule.objects.create(
                professional=professional_a, day_of_week=dow,
                start_time=time(9, 0), end_time=time(13, 0), is_active=True,
            )
        return professional_a

    def _cita(self, clinic, patient, service, professional, day, hora, minutos):
        inicio = timezone.make_aware(
            timezone.datetime.combine(day, hora), timezone.get_current_timezone(),
        )
        return Appointment.objects.create(
            clinic=clinic, patient=patient, service=service, professional=professional,
            scheduled_at=inicio, end_at=inicio + timedelta(minutes=minutos),
            status=Appointment.Status.CONFIRMED,
        )

    def test_filas_de_media_hora(self, panel_client, jornada):
        response = panel_client.get(reverse('appointments:calendar'))
        etiquetas = [s['label'] for s in response.context['time_slots']]
        assert etiquetas == ['9:00', '9:30', '10:00', '10:30', '11:00', '11:30', '12:00', '12:30']
        assert all(len(d['cells']) == 8 for d in response.context['visible_days_info'])

    def test_la_tarjeta_se_coloca_por_minutos_y_lleva_el_color_del_servicio(
        self, panel_client, jornada, clinic_a, patient_a, service_a
    ):
        service_a.color = '#ff0000'
        service_a.save()
        hoy = timezone.localdate()
        lunes = hoy - timedelta(days=hoy.weekday())
        cita = self._cita(clinic_a, patient_a, service_a, jornada, lunes, time(10, 30), 60)

        response = panel_client.get(reverse('appointments:calendar'), {'week': lunes.isoformat()})
        tarjeta = next(
            c for d in response.context['visible_days_info'] for c in d['cards'] if c.pk == cita.pk
        )
        # Rango 9:00–13:00 (240 min): empieza al minuto 90 y dura 60.
        assert float(tarjeta.top_pct) == pytest.approx(37.5)
        assert float(tarjeta.height_pct) == pytest.approx(25.0)
        assert tarjeta.tall is True
        html = response.content.decode()
        assert 'border-left-color: #ff0000' in html
        assert 'bg-emerald-500' in html  # confirmada: el estado es el punto

    def test_una_cita_fuera_de_horario_amplia_el_rango(
        self, panel_client, jornada, clinic_a, patient_a, service_a
    ):
        hoy = timezone.localdate()
        lunes = hoy - timedelta(days=hoy.weekday())
        self._cita(clinic_a, patient_a, service_a, jornada, lunes, time(17, 0), 30)

        response = panel_client.get(reverse('appointments:calendar'), {'week': lunes.isoformat()})
        etiquetas = [s['label'] for s in response.context['time_slots']]
        assert etiquetas[0] == '9:00' and etiquetas[-1] == '17:00'
