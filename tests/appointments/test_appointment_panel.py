"""Nueva cita en el panel lateral de la agenda, y desplegables precargados.

`AppointmentCreateView` responde ahora en dos modos, y lo que se defiende aquí es
que el modo nuevo no se come al viejo:

1. Con la cabecera de htmx devuelve el **fragmento del panel**, ya fechado en la
   casilla pulsada; sin ella, la **página completa** de siempre — que es la única
   vía sin JavaScript y la que usan los tests que ya existían.
2. Al guardar desde el panel la respuesta es un `HX-Redirect` a la agenda **de la
   semana que se estaba mirando**: sin eso, guardar una cita de la semana que
   viene devolvería a la actual y parecería que no se ha creado nada.
3. Los desplegables llegan con su lista dentro, y esa lista está **acotada a la
   clínica del usuario** y a los servicios activos. Es el mismo aislamiento que el
   resto del panel, y aquí importa más que en otros sitios porque los datos van
   escritos en el HTML.
4. Por encima del tope de precarga la lista se marca como truncada, que es lo que
   enciende la búsqueda en servidor como respaldo.
"""
import datetime
from datetime import time, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from appointments.models import Appointment
from appointments.views import build_option_payload
from audit.models import AccessLog
from patients.models import Patient
from services.models import Service


def create_url(**params):
    url = reverse('appointments:create')
    if not params:
        return url
    from urllib.parse import urlencode

    return f'{url}?{urlencode(params)}'


@pytest.fixture
def panel_client(client, admin_user):
    client.force_login(admin_user)
    return client


@pytest.fixture
def manana():
    """Mañana, para que la cita nunca caiga en el pasado."""
    return timezone.localdate() + timedelta(days=1)


def payload(patient, service, professional, date, **kwargs):
    data = {
        'patient': patient.pk,
        'service': service.pk,
        'professional': professional.pk,
        'date': date.isoformat(),
        'time': '10:00',
        'notes': '',
    }
    data.update(kwargs)
    return data


# ---------------------------------------------------------------------------
# Los dos modos de la misma vista
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestDosModos:
    def test_con_htmx_llega_solo_el_fragmento_del_panel(
        self, panel_client, patient_a, service_a, professional_a, manana
    ):
        response = panel_client.get(
            create_url(date=manana.isoformat(), time='10:00'), HTTP_HX_REQUEST='true'
        )
        html = response.content.decode()

        assert response.status_code == 200
        assert '<!DOCTYPE html>' not in html
        # El fragmento se repinta en el propio panel, no en un objetivo aparte.
        assert 'hx-target="#calendar-panel-content"' in html
        assert 'Hueco marcado' in html
        assert response.context['is_panel'] is True

    def test_el_titulo_y_el_cierre_los_pone_el_marco_del_panel(
        self, panel_client, professional_a, manana
    ):
        """El fragmento no trae cabecera: la del panel ya está en la agenda."""
        html = panel_client.get(
            create_url(date=manana.isoformat(), time='10:00'), HTTP_HX_REQUEST='true'
        ).content.decode()

        assert '<h2' not in html
        assert 'Cerrar panel' not in html

    def test_la_casilla_pulsada_llega_prerrellenada(
        self, panel_client, professional_a, manana
    ):
        response = panel_client.get(
            create_url(date=manana.isoformat(), time='16:00'), HTTP_HX_REQUEST='true'
        )
        form = response.context['form']

        assert str(form['date'].value()) == manana.isoformat()
        assert str(form['time'].value()) == '16:00'

    def test_sin_htmx_sigue_siendo_la_pagina_completa(
        self, panel_client, patient_a, service_a, professional_a
    ):
        """La vía sin JavaScript no se toca: es la única que funciona sin él."""
        response = panel_client.get(create_url())
        html = response.content.decode()

        assert '<!DOCTYPE html>' in html
        assert 'Datos de la cita' in html
        assert response.context['is_panel'] is False

    def test_la_restauracion_del_historial_no_devuelve_el_fragmento(
        self, panel_client, professional_a
    ):
        """Volver atrás sin caché pide la página con `HX-Request`: se da entera."""
        response = panel_client.get(
            create_url(), HTTP_HX_REQUEST='true', HTTP_HX_HISTORY_RESTORE_REQUEST='true'
        )

        assert '<!DOCTYPE html>' in response.content.decode()


# ---------------------------------------------------------------------------
# Guardar desde el modal
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestGuardar:
    def test_crea_la_cita_y_manda_recargar_la_agenda(
        self, panel_client, clinic_a, patient_a, service_a, professional_a, manana
    ):
        semana = (manana - timedelta(days=manana.weekday())).isoformat()

        response = panel_client.post(
            create_url(week=semana),
            payload(patient_a, service_a, professional_a, manana),
            HTTP_HX_REQUEST='true',
        )

        assert response.status_code == 204
        assert response['HX-Redirect'] == f"{reverse('appointments:calendar')}?week={semana}"
        cita = Appointment.objects.get()
        assert cita.patient == patient_a
        assert cita.source == Appointment.Source.STAFF

    def test_sin_semana_vuelve_a_la_agenda_a_secas(
        self, panel_client, patient_a, service_a, professional_a, manana
    ):
        response = panel_client.post(
            create_url(),
            payload(patient_a, service_a, professional_a, manana),
            HTTP_HX_REQUEST='true',
        )

        assert response['HX-Redirect'] == reverse('appointments:calendar')

    def test_un_formulario_invalido_vuelve_al_panel_con_sus_errores(
        self, panel_client, patient_a, service_a, professional_a, manana
    ):
        """Y sin crear nada: el panel se repinta, no se cierra."""
        datos = payload(patient_a, service_a, professional_a, manana)
        datos['service'] = ''   # el servicio es obligatorio

        response = panel_client.post(create_url(), datos, HTTP_HX_REQUEST='true')
        html = response.content.decode()

        assert response.status_code == 200
        assert 'HX-Redirect' not in response
        assert 'service' in response.context['form'].errors
        # Sigue siendo el fragmento del panel, no la página.
        assert '<!DOCTYPE html>' not in html
        assert 'hx-target="#calendar-panel-content"' in html
        assert not Appointment.objects.exists()

    def test_una_cita_en_el_pasado_es_un_error_de_campo(
        self, panel_client, patient_a, service_a, professional_a
    ):
        ayer = timezone.localdate() - timedelta(days=1)

        response = panel_client.post(
            create_url(),
            payload(patient_a, service_a, professional_a, ayer),
            HTTP_HX_REQUEST='true',
        )

        assert response.status_code == 200
        assert not Appointment.objects.exists()

    def test_sin_htmx_el_post_sigue_redirigiendo(
        self, panel_client, patient_a, service_a, professional_a, manana
    ):
        response = panel_client.post(
            create_url(), payload(patient_a, service_a, professional_a, manana)
        )

        assert response.status_code == 302
        assert Appointment.objects.count() == 1


# ---------------------------------------------------------------------------
# Las opciones precargadas
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestJavaScriptDeLosDesplegables:
    """Dónde vive el JS de los desplegables, y por qué no dentro del fragmento.

    Definido en el propio fragmento, Alpine puede inicializar los nodos que htmx
    acaba de insertar **antes** de que ese `<script>` se haya evaluado: el `x-data`
    falla y la consola se llena de «abierto no está definido» en la primera
    apertura. Y un bloque de script largo dentro de una plantilla es carne de
    formateador: basta un paréntesis de más para que deje de evaluarse entero y el
    desplegable se caiga en silencio. Las dos cosas ya pasaron.
    """

    def test_el_fragmento_no_define_la_factoria(
        self, panel_client, patient_a, service_a, professional_a
    ):
        html = panel_client.get(create_url(), HTTP_HX_REQUEST='true').content.decode()

        assert 'window.appointmentPicker' not in html
        # Los datos sí salen de aquí, en su `json_script`.
        assert 'id="appointment-patient-options"' in html
        assert 'id="appointment-service-options"' in html

    def test_la_agenda_carga_el_fichero_antes_de_abrir_nada(
        self, panel_client, professional_a
    ):
        """La página anfitriona es la que lo trae, y sin `defer`."""
        html = panel_client.get(reverse('appointments:calendar')).content.decode()

        assert 'js/appointment_picker.js' in html
        assert 'defer src="/static/js/appointment_picker.js"' not in html

    def test_la_pagina_de_nueva_cita_tambien(
        self, panel_client, patient_a, service_a, professional_a
    ):
        html = panel_client.get(create_url()).content.decode()

        assert 'js/appointment_picker.js' in html

    def test_los_desplegables_nacen_cerrados_y_los_abre_la_flecha(
        self, panel_client, patient_a, service_a, professional_a
    ):
        """Enfocar el campo no despliega: solo la flecha, escribir o ↓."""
        html = panel_client.get(create_url(), HTTP_HX_REQUEST='true').content.decode()

        assert '@focus="abrir()"' not in html
        assert '@click="abrir()"' not in html
        # La lista nace cerrada por CSS, no por `x-show`: si Alpine no llega a
        # evaluar la expresión (swaps sucesivos del panel), sigue cerrada.
        assert 'x-show="abierto"' not in html
        assert """abierto ? 'picker-list-open' : ''""" in html
        # Limpiar y la flecha van juntos a la derecha del campo (`.picker-actions`),
        # y es la flecha la que abre y cierra.
        assert html.count('class="picker-actions"') == 2
        assert '@click="abierto ? cerrar() : abrir()"' in html

    def test_los_estilos_del_desplegable_son_componentes_del_head(
        self, panel_client, patient_a, service_a, professional_a
    ):
        """El CDN de Tailwind no compila utilidades que solo viven en el fragmento.

        Este formulario llega por htmx, así que su estilo va en clases `picker-*`
        declaradas con la página (`partials/_head_theme.html`). Con utilidades
        sueltas, `right-2` no generaba regla y los botones acababan sobre el texto.
        """
        html = panel_client.get(create_url(), HTTP_HX_REQUEST='true').content.decode()

        for clase in ('picker-field', 'picker-input', 'picker-actions', 'picker-list'):
            assert clase in html, clase
        # Ninguna utilidad de posicionamiento suelta en el fragmento.
        assert 'right-2' not in html
        assert 'pr-16' not in html

    def test_el_desplegable_abierto_se_pone_por_encima_del_otro(
        self, panel_client, patient_a, service_a, professional_a
    ):
        """Son hermanos: sin elevarlo, el de servicio taparía la lista de paciente."""
        html = panel_client.get(create_url(), HTTP_HX_REQUEST='true').content.decode()

        assert html.count("""abierto ? 'picker-field-open' : ''""") == 2

    def test_el_formulario_no_se_cachea(
        self, panel_client, patient_a, service_a, professional_a
    ):
        """Lleva la lista de pacientes: no puede quedarse en el caché del navegador."""
        respuesta = panel_client.get(create_url(), HTTP_HX_REQUEST='true')

        assert respuesta['Cache-Control'] == 'private, no-store, max-age=0'

    def test_el_fichero_es_javascript_valido(self):
        """Un error de sintaxis deja el desplegable muerto sin que nada falle.

        Se comprueba con `node --check` si está disponible; si no, al menos que el
        fichero exista y siga exponiendo las tres factorías.
        """
        import shutil
        import subprocess
        from pathlib import Path

        from django.conf import settings

        ruta = Path(settings.BASE_DIR) / 'static' / 'js' / 'appointment_picker.js'
        contenido = ruta.read_text()

        assert 'window.appointmentPicker' in contenido
        assert 'window.appointmentPatientPicker' in contenido
        assert 'window.appointmentServicePicker' in contenido

        node = shutil.which('node')
        if node:
            comprobacion = subprocess.run(
                [node, '--check', str(ruta)], capture_output=True, text=True
            )
            assert comprobacion.returncode == 0, comprobacion.stderr


@pytest.mark.django_db
class TestOpciones:
    def test_solo_las_de_la_clinica_del_usuario(
        self, panel_client, clinic_a, patient_a, patient_b, service_a, service_b, professional_a
    ):
        """`patient_b` y `service_b` son de otra clínica: no pueden asomar."""
        response = panel_client.get(create_url(), HTTP_HX_REQUEST='true')
        pacientes = {opcion['id'] for opcion in response.context['patient_options']}
        servicios = {opcion['id'] for opcion in response.context['service_options']}

        assert pacientes == {patient_a.pk}
        assert servicios == {service_a.pk}
        assert patient_b.last_name not in response.content.decode()

    def test_los_servicios_inactivos_no_se_ofrecen(
        self, panel_client, clinic_a, service_a, professional_a
    ):
        """El formulario los rechazaría: ofrecerlos sería prometer de más."""
        retirado = Service.objects.create(
            clinic=clinic_a, name='Servicio retirado', duration_minutes=30,
            price='10.00', is_active=False,
        )

        response = panel_client.get(create_url(), HTTP_HX_REQUEST='true')

        assert retirado.pk not in {o['id'] for o in response.context['service_options']}

    def test_cada_opcion_trae_lo_que_se_lee_y_lo_que_se_filtra(
        self, panel_client, clinic_a, service_a, professional_a
    ):
        paciente = Patient.objects.create(
            clinic=clinic_a, first_name='Begoña', last_name='Núñez',
            email='bego@alpha.test', phone='+34600999888',
        )

        response = panel_client.get(create_url(), HTTP_HX_REQUEST='true')
        opcion = next(
            o for o in response.context['patient_options'] if o['id'] == paciente.pk
        )

        assert opcion['label'] == 'Begoña Núñez'
        assert opcion['hint'] == '+34600999888'
        # El texto de búsqueda va sin acentos: «nunez» tiene que encontrarla.
        assert 'nunez' in opcion['haystack']
        assert 'begona' in opcion['haystack']

    def test_el_email_tambien_filtra(self, panel_client, clinic_a, service_a, professional_a):
        paciente = Patient.objects.create(
            clinic=clinic_a, first_name='Ana', last_name='Gil',
            email='ana.gil@correo.test', phone='+34600111000',
        )

        response = panel_client.get(create_url(), HTTP_HX_REQUEST='true')
        opcion = next(
            o for o in response.context['patient_options'] if o['id'] == paciente.pk
        )

        assert 'ana.gil@correo.test' in opcion['haystack']

    def test_el_servicio_ensena_duracion_y_precio(
        self, panel_client, service_a, professional_a
    ):
        response = panel_client.get(create_url(), HTTP_HX_REQUEST='true')
        opcion = next(
            o for o in response.context['service_options'] if o['id'] == service_a.pk
        )

        assert opcion['label'] == service_a.name
        assert opcion['hint'] == f'{service_a.duration_display} · {service_a.price_display}'

    def test_por_encima_del_tope_la_lista_se_marca_truncada(
        self, panel_client, clinic_a, service_a, professional_a, settings
    ):
        """Es lo que enciende la búsqueda en servidor como respaldo."""
        settings.APPOINTMENT_PICKER_PRELOAD_LIMIT = 2
        for indice in range(4):
            Patient.objects.create(
                clinic=clinic_a, first_name=f'Paciente{indice}', last_name='Prueba',
                email=f'p{indice}@alpha.test', phone=f'+3460000000{indice}',
            )

        response = panel_client.get(create_url(), HTTP_HX_REQUEST='true')

        assert len(response.context['patient_options']) == 2
        assert response.context['patients_truncated'] is True
        assert response.context['options_truncated'] is True
        # La URL de respaldo viaja en el fragmento para que el selector la use.
        assert reverse('patient-list') in response.content.decode()

    def test_dentro_del_tope_no_hay_respaldo_que_encender(
        self, panel_client, patient_a, service_a, professional_a
    ):
        response = panel_client.get(create_url(), HTTP_HX_REQUEST='true')

        assert response.context['patients_truncated'] is False
        assert response.context['services_truncated'] is False

    def test_la_lista_de_pacientes_queda_registrada_como_acceso(
        self, panel_client, patient_a, service_a, professional_a
    ):
        """Antes lo registraba el viewset en cada búsqueda; al precargar, aquí."""
        panel_client.get(create_url(), HTTP_HX_REQUEST='true')

        assert AccessLog.objects.filter(action=AccessLog.Action.LIST).exists()


def test_el_texto_de_busqueda_se_normaliza():
    """Función pura: sin acentos, en minúsculas y con todo lo buscable dentro."""
    opcion = build_option_payload(
        pk=7, label='Begoña Núñez', hint='+34600999888',
        extra=('+34600999888', 'BEGO@Alpha.test'),
    )

    assert opcion['haystack'].startswith('begona nunez')
    assert 'bego@alpha.test' in opcion['haystack']


# ---------------------------------------------------------------------------
# La agenda
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAgenda:
    @pytest.fixture
    def agenda(self, panel_client):
        return panel_client.get(reverse('appointments:calendar')).content.decode()

    def test_las_casillas_laborables_abren_el_formulario_con_su_fecha_y_hora(
        self, panel_client, clinic_a, professional_a
    ):
        """`professional_a` tiene horario completo, así que toda casilla es laborable."""
        html = panel_client.get(reverse('appointments:calendar')).content.decode()
        hoy = timezone.localdate()
        lunes = hoy - timedelta(days=hoy.weekday())

        assert f'date={lunes.isoformat()}&amp;time=08:00' in html
        # La semana visible viaja para volver a ella al guardar.
        assert f'week={lunes.isoformat()}' in html
        # El formulario se carga en el ÚNICO panel lateral, el mismo del detalle.
        assert f"abrirFormulario('{lunes.isoformat()}T08:00')" in html

    def test_hay_un_solo_panel_y_los_dos_gestos_van_a_el(
        self, panel_client, clinic_a, patient_a, service_a, professional_a
    ):
        cita = Appointment.objects.create(
            clinic=clinic_a, patient=patient_a, service=service_a,
            professional=professional_a,
            scheduled_at=timezone.now() + timedelta(hours=2),
        )

        html = panel_client.get(reverse('appointments:calendar')).content.decode()

        assert html.count('id="calendar-panel-content"') == 1
        assert 'abrirDetalle()' in html
        assert 'abrirFormulario(' in html
        assert reverse('core:appointment-quick-detail', args=[cita.id]) in html

    def test_la_casilla_activa_se_marca_mientras_se_crea(
        self, panel_client, clinic_a, professional_a
    ):
        """El hueco marcado tiene que destacar: es el que dice dónde se crea."""
        html = panel_client.get(reverse('appointments:calendar')).content.decode()

        assert 'esSlotActivo(' in html
        assert 'animate-spin' in html      # icono de carga mientras llega el form
        assert 'animate-pulse' in html     # y la marca de «creando aquí» después

    def test_el_velo_no_difumina_el_calendario_en_modo_formulario(self, agenda):
        """Con el formulario abierto hay que poder leer la casilla marcada."""
        assert "veloClase()" in agenda
        assert "'bg-slate-900/20'" in agenda

    def test_el_descanso_y_lo_no_laboral_no_son_pulsables(
        self, panel_client, admin_user, clinic_a, service_a
    ):
        """Una cita en un hueco no laborable la rechazaría el dominio.

        Jornada partida el lunes (9–14 y 16–20): las 14 y las 15 son descanso, y
        el resto de la semana no es laboral. Ninguna de esas casillas puede
        ofrecer el alta.
        """
        from appointments.models import ProfessionalSchedule

        profesional = admin_user.professional_profile
        profesional.services.add(service_a)
        for inicio, fin in ((time(9, 0), time(14, 0)), (time(16, 0), time(20, 0))):
            ProfessionalSchedule.objects.create(
                professional=profesional, day_of_week=0,
                start_time=inicio, end_time=fin, is_active=True,
            )

        html = panel_client.get(reverse('appointments:calendar')).content.decode()
        hoy = timezone.localdate()
        lunes = hoy - timedelta(days=hoy.weekday())
        martes = lunes + timedelta(days=1)

        # Laborable: sí se ofrece.
        assert f'date={lunes.isoformat()}&amp;time=09:00' in html
        # Descanso entre turnos y día no laboral: no.
        assert f'date={lunes.isoformat()}&amp;time=14:00' not in html
        assert f'date={martes.isoformat()}' not in html
