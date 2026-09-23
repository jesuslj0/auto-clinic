"""Alta de procedimientos desde el panel, por sus dos puertas.

Lo que se prueba, por orden de importancia:

1. **El precio de un servicio variable se pide siempre.** Es el error caro:
   `_freeze_from_catalog()` congela `service.price` cuando no le dan importe, y
   en un servicio de rango eso es el SUELO. Como un importe congelado no se
   corrige —hay que dar de baja y registrar otro—, dejarlo pasar significa
   facturar de menos para siempre.
2. **Un encuentro es un encuentro.** Dos procedimientos en la misma cita no
   pueden abrir dos visitas, ni dos del mismo día en la ficha.
3. **Desde la cita, la visita queda enganchada a ella**, que es de lo que vive
   el filtro «con procedimiento» del listado.
4. El aislamiento multitenant de las dos puertas.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from appointments.models import Appointment
from clinical.models import Episode, PerformedProcedure, Visit
from services.models import Service


@pytest.fixture
def panel_client(client, admin_user):
    client.force_login(admin_user)
    return client


@pytest.fixture
def servicio_variable(db, clinic_a):
    """«Plantilla personalizada», 145 – 200 €: el caso que hay que cazar."""
    return Service.objects.create(
        clinic=clinic_a,
        name='Plantilla personalizada',
        duration_minutes=50,
        price=Decimal('145.00'),
        price_type=Service.ValueType.VARIABLE,
        price_max=Decimal('200.00'),
    )


@pytest.fixture
def cita(db, clinic_a, patient_a, service_a, professional_a):
    return Appointment.objects.create(
        clinic=clinic_a, patient=patient_a, service=service_a,
        professional=professional_a,
        scheduled_at=timezone.now() - timedelta(hours=2),
    )


def _url_cita(cita):
    return reverse('appointments:procedure-create', args=[cita.pk])


def _url_ficha(patient):
    return reverse('patients:procedure-create', args=[patient.pk])


def _datos(service, **extra):
    datos = {
        'service': service.pk,
        'performed_on': timezone.localdate().isoformat(),
        'episode_reason': 'Dolor en el antepié',
        'laterality': '',
        'affected_zone': '',
    }
    datos.update(extra)
    return datos


# ---------------------------------------------------------------------------
# El precio variable
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestPrecioVariable:
    def test_sin_importe_el_formulario_lo_rechaza(self, panel_client, cita, servicio_variable):
        response = panel_client.post(_url_cita(cita), _datos(servicio_variable))

        assert response.status_code == 200  # se repinta con el error
        assert not PerformedProcedure.objects.exists()
        assert 'frozen_price' in response.context['form'].errors

    def test_el_error_dice_el_rango(self, panel_client, cita, servicio_variable):
        response = panel_client.post(_url_cita(cita), _datos(servicio_variable))
        error = ' '.join(response.context['form'].errors['frozen_price'])
        assert '145 – 200 €' in error

    def test_con_importe_congela_lo_que_se_cobro(self, panel_client, cita, servicio_variable):
        panel_client.post(
            _url_cita(cita), _datos(servicio_variable, frozen_price='180.00')
        )
        procedimiento = PerformedProcedure.objects.get()
        assert procedimiento.frozen_price == Decimal('180.00')
        assert procedimiento.frozen_service_name == 'Plantilla personalizada'

    def test_un_servicio_desde_tambien_exige_importe(self, panel_client, cita, clinic_a):
        """«Desde 40 €» es un rango abierto: `price` sigue siendo el suelo."""
        abierto = Service.objects.create(
            clinic=clinic_a, name='Cirugía ungueal', duration_minutes=60,
            price=Decimal('40.00'), price_type=Service.ValueType.VARIABLE, price_max=None,
        )
        response = panel_client.post(_url_cita(cita), _datos(abierto))
        assert 'frozen_price' in response.context['form'].errors

    def test_con_precio_fijo_el_importe_es_opcional(self, panel_client, cita, service_a):
        panel_client.post(_url_cita(cita), _datos(service_a))
        procedimiento = PerformedProcedure.objects.get()
        # Decimal y no el valor de la fixture: el modelo normaliza al congelar,
        # justamente para que el snapshot no quede como texto en memoria.
        assert procedimiento.frozen_price == Decimal(str(service_a.price))

    def test_con_precio_fijo_se_puede_forzar_otro_importe(self, panel_client, cita, service_a):
        """Un descuento puntual es legítimo; lo que no vale es el silencio."""
        panel_client.post(_url_cita(cita), _datos(service_a, frozen_price='10.00'))
        assert PerformedProcedure.objects.get().frozen_price == Decimal('10.00')


# ---------------------------------------------------------------------------
# Desde la cita
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestDesdeLaCita:
    def test_la_visita_queda_enganchada_a_la_cita(self, panel_client, cita, service_a):
        """De esto vive el filtro «con procedimiento» del listado."""
        panel_client.post(_url_cita(cita), _datos(service_a))
        visita = Visit.objects.get()
        assert visita.appointment_id == cita.pk
        assert visita.professional_id == cita.professional_id
        assert visita.occurred_at == cita.scheduled_at

    def test_dos_procedimientos_reutilizan_la_visita(self, panel_client, cita, service_a, servicio_variable):
        panel_client.post(_url_cita(cita), _datos(service_a))
        panel_client.post(_url_cita(cita), _datos(servicio_variable, frozen_price='150.00'))

        assert Visit.objects.count() == 1
        assert PerformedProcedure.objects.count() == 2
        assert Episode.objects.count() == 1

    def test_con_visita_previa_no_se_pregunta_el_episodio(self, panel_client, cita, service_a):
        """Su episodio manda: colgarlo de otro sería incoherente con la visita."""
        panel_client.post(_url_cita(cita), _datos(service_a))
        response = panel_client.get(_url_cita(cita))
        form = response.context['form']
        assert form.fixed_episode == Visit.objects.get().episode
        assert 'episode' not in form.fields

    def test_el_formulario_llega_con_el_servicio_de_la_cita(self, panel_client, cita, service_a):
        form = panel_client.get(_url_cita(cita)).context['form']
        assert form.fields['service'].initial == service_a.pk

    def test_una_cita_sin_ficha_no_deja_registrar(self, panel_client, clinic_a, service_a, professional_a):
        """La historia clínica cuelga del paciente; el agente abre citas sin ficha."""
        huerfana = Appointment.objects.create(
            clinic=clinic_a, service=service_a, professional=professional_a,
            patient_name='Quien sea', scheduled_at=timezone.now() - timedelta(hours=1),
        )
        assert panel_client.get(_url_cita(huerfana)).status_code == 200  # lo explica
        assert panel_client.post(_url_cita(huerfana), _datos(service_a)).status_code == 403
        assert not PerformedProcedure.objects.exists()


# ---------------------------------------------------------------------------
# Desde la ficha
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestDesdeLaFicha:
    def test_registra_sin_cita(self, panel_client, patient_a, service_a):
        panel_client.post(_url_ficha(patient_a), _datos(service_a))
        visita = Visit.objects.get()
        assert visita.appointment_id is None
        assert PerformedProcedure.objects.get().visit_id == visita.pk

    def test_dos_del_mismo_dia_comparten_visita(self, panel_client, patient_a, service_a, servicio_variable):
        panel_client.post(_url_ficha(patient_a), _datos(service_a))
        episodio = Episode.objects.get()
        panel_client.post(
            _url_ficha(patient_a),
            _datos(servicio_variable, frozen_price='150.00', episode=episodio.pk),
        )
        assert Visit.objects.count() == 1
        assert PerformedProcedure.objects.count() == 2

    def test_no_se_cuelga_de_la_visita_de_una_cita(self, panel_client, cita, patient_a, service_a):
        """Si ese día hubo cita, su visita es suya: no se toca desde la ficha.

        Colgar aquí el procedimiento haría que la cita figurase «con
        procedimiento» sin que nadie la haya atendido.
        """
        panel_client.post(_url_cita(cita), _datos(service_a))
        visita_cita = Visit.objects.get()

        panel_client.post(
            _url_ficha(patient_a), _datos(service_a, episode=visita_cita.episode_id)
        )
        assert Visit.objects.count() == 2
        suelta = Visit.objects.exclude(pk=visita_cita.pk).get()
        assert suelta.appointment_id is None

    def test_sin_episodio_ni_motivo_lo_rechaza(self, panel_client, patient_a, service_a):
        datos = _datos(service_a)
        datos['episode_reason'] = ''
        response = panel_client.post(_url_ficha(patient_a), datos)
        assert 'episode_reason' in response.context['form'].errors
        assert not PerformedProcedure.objects.exists()

    def test_reutiliza_el_episodio_abierto_elegido(self, panel_client, patient_a, service_a, episode_a):
        panel_client.post(_url_ficha(patient_a), _datos(service_a, episode=episode_a.pk))
        assert Episode.objects.count() == 1
        assert Visit.objects.get().episode_id == episode_a.pk


# ---------------------------------------------------------------------------
# Fecha, aislamiento y auditoría
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestReglasComunes:
    def test_no_se_puede_fechar_en_el_futuro(self, panel_client, patient_a, service_a):
        manana = (timezone.localdate() + timedelta(days=1)).isoformat()
        response = panel_client.post(
            _url_ficha(patient_a), _datos(service_a, performed_on=manana)
        )
        assert 'performed_on' in response.context['form'].errors

    def test_el_servicio_de_otra_clinica_no_pasa(self, panel_client, patient_a, service_b):
        """El queryset ya lo acota, así que es error de validación y no llega al modelo."""
        response = panel_client.post(_url_ficha(patient_a), _datos(service_b))
        assert 'service' in response.context['form'].errors
        assert not PerformedProcedure.objects.exists()

    def test_un_paciente_de_otra_clinica_es_404(self, panel_client, patient_b, service_a):
        assert panel_client.get(_url_ficha(patient_b)).status_code == 404

    def test_una_cita_de_otra_clinica_no_se_toca(self, panel_client, appointment_b, service_a):
        assert panel_client.get(_url_cita(appointment_b)).status_code == 403

    def test_la_escritura_queda_en_el_changelog(self, panel_client, cita, service_a):
        """`PerformedProcedure` está en el registro de auditoría: lo hacen las señales."""
        from audit.models import ChangeLog

        panel_client.post(_url_cita(cita), _datos(service_a))
        procedimiento = PerformedProcedure.objects.get()
        assert ChangeLog.objects.filter(
            model_label='clinical.PerformedProcedure',
            object_id=str(procedimiento.pk),
            action=ChangeLog.Action.CREATE,
        ).exists()

    def test_la_lectura_queda_en_el_accesslog(self, panel_client, cita):
        from audit.models import AccessLog

        panel_client.get(_url_cita(cita))
        assert AccessLog.objects.filter(path=_url_cita(cita)).exists()


# ---------------------------------------------------------------------------
# El contador en el detalle de la cita
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestContadorEnElDetalle:
    """El detalle de la cita dice CUÁNTOS hay, nunca qué son.

    Es la frontera deliberada de esa pantalla: un recuento es información de
    agenda —la misma que ya distingue el listado entre «con» y «sin»— y por eso
    no lleva `AccessLog`. El contenido está a un clic, en la ficha, que sí lo
    registra. Estos tests son los que impiden que la frontera se cruce sin darse
    cuenta.
    """

    def _url_detalle(self, cita):
        return reverse('core:dashboard-manage-appointment', args=[cita.pk])

    def test_sin_procedimientos_el_contador_es_cero(self, panel_client, cita):
        response = panel_client.get(self._url_detalle(cita))
        assert response.context['procedure_count'] == 0
        assert 'Ver procedimientos' not in response.content.decode()

    def test_cuenta_los_de_esta_cita(self, panel_client, cita, service_a, servicio_variable):
        panel_client.post(_url_cita(cita), _datos(service_a))
        panel_client.post(_url_cita(cita), _datos(servicio_variable, frozen_price='150.00'))

        response = panel_client.get(self._url_detalle(cita))
        assert response.context['procedure_count'] == 2
        assert 'Ver procedimientos' in response.content.decode()

    def test_no_se_filtra_ningun_dato_clinico(self, panel_client, cita, servicio_variable):
        """Ni el servicio, ni el importe, ni la zona: solo el número.

        El POST se sigue (`follow=True`) para consumir su mensaje de
        confirmación antes de mirar la pantalla. Ese mensaje SÍ nombra el
        servicio y el importe, y es deliberado: confirma lo que acaba de teclear
        quien lo teclea —y el importe congelado conviene verlo, porque después no
        se corrige—, no es una lectura de lo que hay guardado. Lo que este test
        fija es que al volver a la pantalla, sin mensaje de por medio, no queda
        ningún dato clínico.
        """
        panel_client.post(
            _url_cita(cita),
            _datos(servicio_variable, frozen_price='180.00', laterality='right'),
            follow=True,
        )
        html = panel_client.get(self._url_detalle(cita)).content.decode()

        assert 'Plantilla personalizada' not in html
        # El importe, en los dos formatos en que podría escaparse. A secas, «180»
        # aparece en las variables de color del tema (`--c-warning: 180 83 9`).
        assert '180,00' not in html
        assert '180.00' not in html
        assert 'Sin facturar' not in html

    def test_el_enlace_lleva_a_la_ficha(self, panel_client, cita, service_a, patient_a):
        panel_client.post(_url_cita(cita), _datos(service_a))
        html = panel_client.get(self._url_detalle(cita)).content.decode()
        assert reverse('patients:tab-procedures', args=[patient_a.pk]) in html

    def test_no_cuenta_los_de_otra_cita_del_mismo_paciente(
        self, panel_client, cita, clinic_a, patient_a, service_a, professional_a
    ):
        otra = Appointment.objects.create(
            clinic=clinic_a, patient=patient_a, service=service_a,
            professional=professional_a, scheduled_at=timezone.now() - timedelta(days=3),
        )
        panel_client.post(_url_cita(otra), _datos(service_a))
        assert panel_client.get(self._url_detalle(cita)).context['procedure_count'] == 0

    def test_un_procedimiento_de_baja_no_cuenta(self, panel_client, cita, service_a):
        panel_client.post(_url_cita(cita), _datos(service_a))
        PerformedProcedure.objects.get().delete()
        assert panel_client.get(self._url_detalle(cita)).context['procedure_count'] == 0

    def test_una_visita_de_baja_no_arrastra_los_suyos(self, panel_client, cita, service_a):
        """El salto a `visit` es un JOIN y no pasa por el manager de `Visit`."""
        panel_client.post(_url_cita(cita), _datos(service_a))
        Visit.all_objects.filter(pk=Visit.objects.get().pk).update(deleted_at=timezone.now())
        assert panel_client.get(self._url_detalle(cita)).context['procedure_count'] == 0

    def test_el_detalle_no_registra_accesslog(self, panel_client, cita, service_a):
        """Si algún día esta pantalla enseña contenido clínico, esto tiene que
        cambiar a la vez que se le pone `AccessLogMixin`."""
        from audit.models import AccessLog

        panel_client.post(_url_cita(cita), _datos(service_a))
        antes = AccessLog.objects.count()
        panel_client.get(self._url_detalle(cita))
        assert AccessLog.objects.count() == antes

    def test_la_ficha_si_lo_registra(self, panel_client, cita, service_a, patient_a):
        """Cruzar la frontera por el enlace sí queda anotado."""
        from audit.models import AccessLog

        url = reverse('patients:tab-procedures', args=[patient_a.pk])
        panel_client.get(url)
        assert AccessLog.objects.filter(path=url, patient_id=patient_a.pk).exists()
