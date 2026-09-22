"""Filtros del listado de citas del panel (`appointments:list`).

Lo que se prueba aquí es sobre todo el convenio de «ausente no es vacío»: sin
parámetros la pantalla abre acotada al mes en curso y a la ficha de quien mira,
y con `desde=`/`profesional=` vacíos se abre del todo. Los enlaces del panel de
control dependen de esa distinción, así que romperla haría que una tarjeta
llevara a una lista con menos citas que la cifra sobre la que se pinchó.

El filtro de procedimiento cruza a la capa clínica y tiene su propio bloque: lo
que se comprueba es que el borrado lógico se respeta en los DOS saltos.
"""
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from appointments.models import Appointment, Professional
from clinical.models import PerformedProcedure, Visit
from core.models import User


LIST_URL = reverse('appointments:list')


@pytest.fixture
def panel_client(client, admin_user):
    client.force_login(admin_user)
    return client


@pytest.fixture
def otro_profesional(db, clinic_a, service_a):
    """Un segundo profesional de la misma clínica, para el filtro «Asignado a»."""
    user = User.objects.create_user(
        email='otra@alpha.test', password='x', clinic=clinic_a, role=User.Role.STAFF,
        first_name='Lucía', last_name='Prat',
    )
    professional = user.professional_profile
    professional.services.add(service_a)
    return professional


def _cita(clinic, patient, service, professional, cuando):
    return Appointment.objects.create(
        clinic=clinic, patient=patient, service=service,
        professional=professional, scheduled_at=cuando,
    )


@pytest.fixture
def citas(db, clinic_a, patient_a, service_a, professional_a, otro_profesional):
    """Tres citas: mía este mes, mía el mes que viene, y de otra profesional hoy."""
    hoy = timezone.localtime().replace(hour=10, minute=0, second=0, microsecond=0)
    mes_que_viene = hoy + timedelta(days=40)
    return {
        'mia_este_mes': _cita(clinic_a, patient_a, service_a, professional_a, hoy),
        'mia_otro_mes': _cita(clinic_a, patient_a, service_a, professional_a, mes_que_viene),
        'suya_este_mes': _cita(clinic_a, patient_a, service_a, otro_profesional, hoy + timedelta(hours=2)),
    }


def _ids(response):
    return {a.pk for a in response.context['appointments']}


# ---------------------------------------------------------------------------
# Valores por defecto: mi mes
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestPorDefecto:
    def test_abre_en_mis_citas_del_mes_en_curso(self, panel_client, citas):
        ids = _ids(panel_client.get(LIST_URL))
        assert citas['mia_este_mes'].pk in ids
        assert citas['mia_otro_mes'].pk not in ids   # fuera del mes
        assert citas['suya_este_mes'].pk not in ids  # de otra profesional

    def test_el_rango_por_defecto_es_el_mes_completo(self, panel_client, citas):
        filtros = panel_client.get(LIST_URL).context['filters']
        hoy = timezone.localdate()
        assert filtros.date_from == hoy.replace(day=1)
        assert filtros.date_to.month == hoy.month
        assert filtros.date_to > filtros.date_from or hoy.day == 1

    def test_sin_ficha_profesional_abre_con_todos(self, client, superuser, citas):
        """Un superusuario de plataforma no tiene ficha: no hay «lo mío» al que acotar."""
        assert not hasattr(superuser, 'professional_profile')
        client.force_login(superuser)
        assert client.get(LIST_URL).context['filters'].professional is None


# ---------------------------------------------------------------------------
# Vacío explícito: el convenio del que dependen los enlaces del panel
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestVacioExplicito:
    def test_fechas_vacias_quitan_el_limite(self, panel_client, citas):
        ids = _ids(panel_client.get(LIST_URL, {'desde': '', 'hasta': '', 'profesional': ''}))
        assert ids == {c.pk for c in citas.values()}

    def test_profesional_vacio_trae_a_todos(self, panel_client, citas):
        ids = _ids(panel_client.get(LIST_URL, {'profesional': ''}))
        assert citas['suya_este_mes'].pk in ids

    def test_los_enlaces_del_panel_de_control_abren_toda_la_clinica(self, panel_client, citas):
        """`?desde=&hasta=&profesional=&status=pending` es lo que enlaza el panel."""
        response = panel_client.get(
            LIST_URL, {'desde': '', 'hasta': '', 'profesional': '', 'status': 'pending'}
        )
        assert _ids(response) == {c.pk for c in citas.values()}


# ---------------------------------------------------------------------------
# Asignado a
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAsignadoA:
    def test_filtra_por_profesional(self, panel_client, citas, otro_profesional):
        response = panel_client.get(LIST_URL, {'profesional': otro_profesional.pk, 'desde': '', 'hasta': ''})
        assert _ids(response) == {citas['suya_este_mes'].pk}

    def test_el_desplegable_solo_trae_los_de_la_clinica(self, panel_client, citas, admin_user_b):
        profesionales = panel_client.get(LIST_URL).context['professionals']
        ajeno = admin_user_b.professional_profile
        assert ajeno.pk not in {p.pk for p in profesionales}

    def test_un_id_inventado_no_llega_al_queryset(self, panel_client, citas):
        """Una dirección pegada a mano con basura cae al valor por defecto."""
        filtros = panel_client.get(LIST_URL, {'profesional': 'pepe'}).context['filters']
        assert filtros.professional is None


# ---------------------------------------------------------------------------
# Rango de fechas
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestRangoDeFechas:
    def test_acota_por_los_dos_extremos(self, panel_client, citas):
        dia = timezone.localtime(citas['mia_otro_mes'].scheduled_at).date().isoformat()
        response = panel_client.get(LIST_URL, {'desde': dia, 'hasta': dia, 'profesional': ''})
        assert _ids(response) == {citas['mia_otro_mes'].pk}

    def test_un_rango_del_reves_se_endereza(self, panel_client, citas):
        hoy = timezone.localdate()
        filtros = panel_client.get(LIST_URL, {
            'desde': (hoy + timedelta(days=5)).isoformat(),
            'hasta': hoy.isoformat(),
        }).context['filters']
        assert filtros.date_from == hoy
        assert filtros.date_to == hoy + timedelta(days=5)


# ---------------------------------------------------------------------------
# Con / sin procedimiento
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestProcedimiento:
    @pytest.fixture
    def con_procedimiento(self, citas, episode_a, professional_a, service_a):
        """Le cuelga a `mia_este_mes` una visita con un procedimiento."""
        cita = citas['mia_este_mes']
        visita = Visit.objects.create(
            episode=episode_a, professional=professional_a, appointment=cita,
        )
        procedimiento = PerformedProcedure.objects.create(visit=visita, service=service_a)
        return cita, visita, procedimiento

    def test_con_procedimiento(self, panel_client, citas, con_procedimiento):
        cita, _, _ = con_procedimiento
        response = panel_client.get(LIST_URL, {'procedimiento': 'con', 'desde': '', 'hasta': '', 'profesional': ''})
        assert _ids(response) == {cita.pk}

    def test_sin_procedimiento(self, panel_client, citas, con_procedimiento):
        cita, _, _ = con_procedimiento
        ids = _ids(panel_client.get(LIST_URL, {'procedimiento': 'sin', 'desde': '', 'hasta': '', 'profesional': ''}))
        assert cita.pk not in ids
        assert citas['mia_otro_mes'].pk in ids

    def test_la_anotacion_viaja_aunque_no_se_filtre(self, panel_client, citas, con_procedimiento):
        """La tabla pinta el distintivo, así que `has_procedure` no depende del filtro."""
        response = panel_client.get(LIST_URL, {'desde': '', 'hasta': '', 'profesional': ''})
        por_id = {a.pk: a for a in response.context['appointments']}
        assert por_id[con_procedimiento[0].pk].has_procedure is True
        assert por_id[citas['mia_otro_mes'].pk].has_procedure is False

    def test_un_procedimiento_de_baja_no_cuenta(self, panel_client, citas, con_procedimiento):
        cita, _, procedimiento = con_procedimiento
        procedimiento.delete()
        ids = _ids(panel_client.get(LIST_URL, {'procedimiento': 'con', 'desde': '', 'hasta': '', 'profesional': ''}))
        assert cita.pk not in ids

    def test_una_visita_de_baja_no_arrastra_sus_procedimientos(self, panel_client, citas, con_procedimiento):
        """El salto a `visit` es un JOIN y no pasa por el manager de `Visit`.

        Sin el `deleted_at` explícito de `annotate_procedures`, la cita seguiría
        saliendo como «con procedimiento» con la visita dada de baja.
        """
        cita, visita, _ = con_procedimiento
        Visit.all_objects.filter(pk=visita.pk).update(deleted_at=timezone.now())
        ids = _ids(panel_client.get(LIST_URL, {'procedimiento': 'con', 'desde': '', 'hasta': '', 'profesional': ''}))
        assert cita.pk not in ids

    def test_dos_procedimientos_no_duplican_la_fila(self, panel_client, citas, con_procedimiento, service_a):
        """`Exists` y no un JOIN: si no, el paginador contaría la cita dos veces."""
        cita, visita, _ = con_procedimiento
        PerformedProcedure.objects.create(visit=visita, service=service_a)
        response = panel_client.get(LIST_URL, {'procedimiento': 'con', 'desde': '', 'hasta': '', 'profesional': ''})
        assert response.context['paginator'].count == 1


# ---------------------------------------------------------------------------
# URLs y auditoría
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestUrlsYAuditoria:
    def test_las_urls_conservan_las_fechas_vacias(self, panel_client, citas):
        """Omitir un `desde=` vacío convertiría «todas» en «este mes» al paginar."""
        base = panel_client.get(
            LIST_URL, {'desde': '', 'hasta': '', 'profesional': ''}
        ).context['appointment_query_base']
        assert base['desde'] == ''
        assert base['hasta'] == ''
        assert base['profesional'] == ''

    def test_ordenar_conserva_los_filtros(self, panel_client, citas, otro_profesional):
        response = panel_client.get(LIST_URL, {'profesional': otro_profesional.pk, 'status': 'pending'})
        sort_query = response.context['sort_query']
        assert f'profesional={otro_profesional.pk}' in sort_query
        assert 'status=pending' in sort_query
        assert 'sort=desc' in sort_query  # el por defecto es asc, así que lo invierte

    def test_el_listado_no_registra_accesslog(self, panel_client, citas):
        """Del filtro de procedimiento solo sale un sí o un no, que es agenda.

        Mismo criterio que el recuento del detalle de la cita: el contenido vive
        en la pestaña de la ficha, y esa sí lo registra. Si este listado pasa a
        enseñar el nombre, el importe o la zona de un procedimiento, esto tiene
        que cambiar a la vez que se le pone `AccessLogMixin`.
        """
        from audit.models import AccessLog

        antes = AccessLog.objects.count()
        panel_client.get(LIST_URL, {'procedimiento': 'con'})
        assert AccessLog.objects.count() == antes


# ---------------------------------------------------------------------------
# Aislamiento multitenant
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAislamiento:
    def test_no_se_ven_las_citas_de_otra_clinica(self, panel_client, citas, appointment_b):
        ids = _ids(panel_client.get(LIST_URL, {'desde': '', 'hasta': '', 'profesional': ''}))
        assert appointment_b.pk not in ids

    def test_el_profesional_de_otra_clinica_no_abre_sus_citas(
        self, panel_client, citas, appointment_b, admin_user_b
    ):
        """Pasar a mano el id de un profesional ajeno no salta el filtro de clínica."""
        ajeno = admin_user_b.professional_profile
        Appointment.objects.filter(pk=appointment_b.pk).update(professional=ajeno)
        response = panel_client.get(
            LIST_URL, {'profesional': ajeno.pk, 'desde': '', 'hasta': ''}
        )
        assert _ids(response) == set()
