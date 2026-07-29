"""Alta de una lesión marcada sobre el mapa del pie (`patients:lesion-create`).

Lo que se defiende aquí no es que salga un formulario, sino las cinco cosas que
lo hacen correcto y que fallarían en silencio:

1. La lesión se registra **donde se marcó**: el punto que llega del clic viaja en
   fracciones 0–1 y el marcador aparece en el mapa sin recargar la página.
2. **Nunca se confía en el cliente.** Una coordenada fuera de 0–1 es un error de
   campo del servidor, no una excepción ni una fila torcida en la tabla.
3. El **episodio** sale del paciente: el de otra persona no se cuela ni mandándolo
   por POST a mano, y un paciente sin episodios abiertos no se queda sin poder
   registrar su lesión.
4. El **aislamiento** es el mismo que el del resto de la ficha: paciente de otra
   clínica, 404, también en esta URL, que es invocable directamente.
5. La **localización y el estado** son los que dice el modelo: se nace activa y el
   punto queda congelado.
"""
import pytest
from django.urls import reverse
from django.utils import timezone

from audit.models import AccessLog, ChangeLog
from clinical.models import Episode, Lesion
from patients.models import Patient


#: Las mismas unidades que declara el `viewBox` de `patients/_lesion_map.html`.
MAP_WIDTH = 400
MAP_HEIGHT = 520


def create_url(patient):
    return reverse('patients:lesion-create', kwargs={'id': patient.pk})


def map_url(patient):
    return reverse('patients:lesion-map', kwargs={'id': patient.pk})


def payload(episode=None, **kwargs):
    """POST mínimo válido: el mapa pone la localización y el usuario la zona."""
    data = {
        'laterality': Lesion.Laterality.LEFT,
        'view': Lesion.View.PLANTAR,
        'x': '0.25',
        'y': '0.5',
        'anatomical_zone': Lesion.AnatomicalZone.FIRST_METATARSAL,
        'lesion_type': Lesion.LesionType.ULCER,
        'detected_at': timezone.localdate().isoformat(),
        'status': Lesion.Status.ACTIVE,
    }
    if episode is not None:
        data['episode'] = getattr(episode, 'pk', episode)
    data.update(kwargs)
    return data


@pytest.fixture
def logged_client(client, admin_user):
    client.force_login(admin_user)
    return client


# ---------------------------------------------------------------------------
# Abrir el formulario con lo que hay puesto en el mapa
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAbrirElAlta:
    def test_el_formulario_llega_con_la_vista_el_pie_y_el_punto_del_clic(
        self, logged_client, patient_a, episode_a
    ):
        response = logged_client.get(create_url(patient_a), {
            'vista': 'medial', 'pie': 'right', 'x': '0.42', 'y': '0.61',
        })
        form = response.context['lesion_form']

        assert response.status_code == 200
        assert form['view'].value() == 'medial'
        assert form['laterality'].value() == 'right'
        assert form['x'].value() == 0.42
        assert form['y'].value() == 0.61
        assert response.context['lesion_point']['marked'] is True

    def test_sin_punto_marcado_la_lesion_se_situa_en_el_centro(
        self, logged_client, patient_a
    ):
        """La vía sin ratón: se abre el alta y el punto es el centro de la vista.

        `x`/`y` no admiten nulo —solo sirven para dibujar—, así que «sin marcar»
        necesita un valor, y el centro es el único neutro.
        """
        response = logged_client.get(create_url(patient_a), {'vista': 'dorsal', 'pie': 'left'})
        point = response.context['lesion_point']

        assert (point['x'], point['y']) == (0.5, 0.5)
        assert point['marked'] is False
        assert 'Sin punto marcado' in response.content.decode()

    def test_una_vista_o_un_punto_ilegibles_no_rompen_la_pantalla(
        self, logged_client, patient_a
    ):
        """Prerrellenar es solo prerrellenar: lo que se valida de verdad es el POST."""
        response = logged_client.get(create_url(patient_a), {
            'vista': 'inventada', 'pie': 'ninguno', 'x': 'hola', 'y': '12',
        })
        form = response.context['lesion_form']

        assert response.status_code == 200
        assert form['view'].value() in dict(Lesion.View.choices)
        assert form['laterality'].value() in dict(Lesion.Laterality.choices)
        assert (form['x'].value(), form['y'].value()) == (0.5, 0.5)

    def test_con_htmx_llega_solo_la_region_del_mapa(
        self, logged_client, patient_a
    ):
        response = logged_client.get(
            create_url(patient_a), {'vista': 'plantar', 'pie': 'left', 'x': '0.3', 'y': '0.7'},
            HTTP_HX_REQUEST='true',
        )
        html = response.content.decode()

        assert '<!DOCTYPE html>' not in html
        assert 'Secciones de la ficha del paciente' not in html   # ni la barra de pestañas
        assert 'Nueva lesión' in html
        # El punto pendiente se pinta en unidades del `viewBox`, como todo lo demás.
        assert f'cx="{round(0.3 * MAP_WIDTH)}"' in html
        assert f'cy="{round(0.7 * MAP_HEIGHT)}"' in html

    def test_sin_htmx_el_alta_es_una_pagina_completa(self, logged_client, patient_a):
        """Sin JavaScript el alta sigue existiendo: es una URL de verdad."""
        response = logged_client.get(create_url(patient_a))
        html = response.content.decode()

        assert '<!DOCTYPE html>' in html
        assert 'Nueva lesión' in html


# ---------------------------------------------------------------------------
# El alta
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestRegistrarLaLesion:
    def test_se_registra_donde_se_marco_y_aparece_en_el_mapa(
        self, logged_client, patient_a, episode_a
    ):
        """El caso completo: se guarda la fracción y vuelve pintada."""
        response = logged_client.post(
            create_url(patient_a), payload(episode_a, x='0.735', y='0.128'),
            HTTP_HX_REQUEST='true',
        )
        html = response.content.decode()
        lesion = Lesion.objects.get(episode=episode_a)

        assert response.status_code == 200
        assert (lesion.x, lesion.y) == (0.735, 0.128)
        assert lesion.anatomical_zone == Lesion.AnatomicalZone.FIRST_METATARSAL
        assert lesion.view == Lesion.View.PLANTAR
        assert lesion.laterality == Lesion.Laterality.LEFT
        # El fragmento que vuelve trae el marcador nuevo, ya en unidades del
        # `viewBox`, y el formulario cerrado.
        assert f'data-lesion="{lesion.pk}"' in html
        assert f'cx="{round(0.735 * MAP_WIDTH)}"' in html
        assert f'cy="{round(0.128 * MAP_HEIGHT)}"' in html
        assert 'Nueva lesión' not in html
        assert 'Lesión registrada' in html

    def test_el_recuento_del_resumen_se_actualiza_con_la_lesion_nueva(
        self, logged_client, patient_a, episode_a
    ):
        """Mapa y recuentos viajan en la misma respuesta: no se contradicen."""
        response = logged_client.post(
            create_url(patient_a), payload(episode_a), HTTP_HX_REQUEST='true'
        )

        assert '1 lesión registrada' in response.content.decode()

    def test_nace_activa_y_sin_fecha_de_resolucion(
        self, logged_client, patient_a, episode_a
    ):
        logged_client.post(create_url(patient_a), payload(episode_a), HTTP_HX_REQUEST='true')
        lesion = Lesion.objects.get(episode=episode_a)

        assert lesion.status == Lesion.Status.ACTIVE
        assert lesion.resolved_at is None

    def test_pedir_registrarla_como_resuelta_es_un_error_de_campo(
        self, logged_client, patient_a, episode_a
    ):
        """El `CheckConstraint` exige fecha de resolución: no se llega a rozarlo.

        Una lesión se registra al detectarla; cerrarla es su ciclo de vida, no su
        alta. Un POST manipulado se queda en un error limpio del formulario.
        """
        response = logged_client.post(
            create_url(patient_a), payload(episode_a, status=Lesion.Status.RESOLVED),
            HTTP_HX_REQUEST='true',
        )

        assert response.status_code == 200
        assert 'status' in response.context['lesion_form'].errors
        assert not Lesion.objects.exists()

    def test_quien_registra_queda_anotado(
        self, logged_client, patient_a, episode_a, admin_user
    ):
        logged_client.post(create_url(patient_a), payload(episode_a), HTTP_HX_REQUEST='true')

        assert Lesion.objects.get().created_by == admin_user.professional_profile

    def test_sin_htmx_redirige_a_la_pestana_con_su_aviso(
        self, logged_client, patient_a, episode_a
    ):
        response = logged_client.post(create_url(patient_a), payload(episode_a), follow=True)
        textos = [str(m) for m in response.context['messages']]

        assert Lesion.objects.count() == 1
        assert any('Lesión registrada' in texto for texto in textos)

    def test_con_htmx_no_se_encola_ningun_mensaje(
        self, logged_client, patient_a, episode_a
    ):
        """Un `messages` sin recarga saldría después, en otra pantalla."""
        logged_client.post(create_url(patient_a), payload(episode_a), HTTP_HX_REQUEST='true')

        siguiente = logged_client.get(reverse('patients:tab-lesions', kwargs={'id': patient_a.pk}))
        assert list(siguiente.context['messages']) == []


# ---------------------------------------------------------------------------
# Las coordenadas se revalidan en el servidor
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCoordenadasEnElServidor:
    @pytest.mark.parametrize('x, y', [
        ('1.5', '0.5'),      # fuera por arriba
        ('0.5', '-0.2'),     # fuera por abajo
        ('250', '380'),      # píxeles, que es el error clásico
        ('', '0.5'),         # sin marcar
        ('hola', '0.5'),     # basura
    ])
    def test_un_punto_fuera_de_0_1_se_rechaza(
        self, logged_client, patient_a, episode_a, x, y
    ):
        response = logged_client.post(
            create_url(patient_a), payload(episode_a, x=x, y=y), HTTP_HX_REQUEST='true'
        )
        errores = response.context['lesion_form'].errors

        assert response.status_code == 200
        assert 'x' in errores or 'y' in errores
        assert not Lesion.objects.exists()

    def test_los_extremos_si_valen(self, logged_client, patient_a, episode_a):
        """0 y 1 son puntos legítimos del dibujo: el rango es cerrado."""
        logged_client.post(
            create_url(patient_a), payload(episode_a, x='0', y='1'), HTTP_HX_REQUEST='true'
        )

        assert (Lesion.objects.get().x, Lesion.objects.get().y) == (0.0, 1.0)

    def test_el_error_del_punto_se_ve_en_el_formulario(
        self, logged_client, patient_a, episode_a
    ):
        """Un campo oculto no puede enseñar su error al lado: se junta arriba."""
        response = logged_client.post(
            create_url(patient_a), payload(episode_a, x='3'), HTTP_HX_REQUEST='true'
        )
        html = response.content.decode()

        assert 'role="alert"' in html
        assert 'cae fuera del mapa' in html


# ---------------------------------------------------------------------------
# Zona anatómica y coordenadas son dos datos distintos
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestZonaYPunto:
    def test_la_zona_es_obligatoria_y_se_elige_a_mano(
        self, logged_client, patient_a, episode_a
    ):
        """No se deduce del clic: sin zona no hay lesión."""
        response = logged_client.post(
            create_url(patient_a), payload(episode_a, anatomical_zone=''),
            HTTP_HX_REQUEST='true',
        )

        assert 'anatomical_zone' in response.context['lesion_form'].errors
        assert not Lesion.objects.exists()

    def test_la_zona_elegida_no_mueve_el_punto(
        self, logged_client, patient_a, episode_a
    ):
        """Ni el punto rotula la zona ni la zona recoloca el punto."""
        logged_client.post(
            create_url(patient_a),
            payload(episode_a, x='0.9', y='0.05', anatomical_zone=Lesion.AnatomicalZone.HEEL),
            HTTP_HX_REQUEST='true',
        )
        lesion = Lesion.objects.get()

        assert (lesion.x, lesion.y) == (0.9, 0.05)
        assert lesion.anatomical_zone == Lesion.AnatomicalZone.HEEL

    def test_una_fecha_de_deteccion_futura_se_rechaza(
        self, logged_client, patient_a, episode_a
    ):
        futuro = (timezone.localdate() + timezone.timedelta(days=1)).isoformat()

        response = logged_client.post(
            create_url(patient_a), payload(episode_a, detected_at=futuro),
            HTTP_HX_REQUEST='true',
        )

        assert 'detected_at' in response.context['lesion_form'].errors


# ---------------------------------------------------------------------------
# Episodio
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestEpisodio:
    def test_el_episodio_abierto_se_preselecciona(
        self, logged_client, patient_a, episode_a
    ):
        form = logged_client.get(create_url(patient_a)).context['lesion_form']

        assert form.has_open_episodes
        assert form['episode'].value() == episode_a.pk

    def test_un_paciente_sin_episodios_no_es_un_callejon_sin_salida(
        self, logged_client, clinic_a, admin_user
    ):
        nuevo = Patient.objects.create(
            clinic=clinic_a, first_name='Nueva', last_name='Paciente',
            email='nueva@alpha.test', phone='+34600333444',
        )
        assert logged_client.get(create_url(nuevo)).context['lesion_form'].has_open_episodes is False

        response = logged_client.post(
            create_url(nuevo), payload(episode_reason='Úlcera en el antepié derecho'),
            HTTP_HX_REQUEST='true',
        )

        assert response.status_code == 200
        lesion = Lesion.objects.get()
        assert lesion.episode.reason == 'Úlcera en el antepié derecho'
        assert lesion.episode.history.patient_id == nuevo.pk
        assert lesion.episode.responsible_professional == admin_user.professional_profile

    def test_abrir_episodio_sin_motivo_es_un_error(
        self, logged_client, patient_a, episode_a
    ):
        response = logged_client.post(
            create_url(patient_a), payload(episode='', episode_reason='   '),
            HTTP_HX_REQUEST='true',
        )

        assert 'episode_reason' in response.context['lesion_form'].errors
        # Ni episodio huérfano ni lesión: la transacción es atómica.
        assert Episode.objects.filter(history__patient=patient_a).count() == 1
        assert not Lesion.objects.exists()

    def test_los_episodios_cerrados_no_se_ofrecen(
        self, logged_client, patient_a, episode_a
    ):
        episode_a.close()

        assert logged_client.get(create_url(patient_a)).context['lesion_form'].has_open_episodes is False


# ---------------------------------------------------------------------------
# Aislamiento
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAislamiento:
    def test_el_episodio_de_otro_paciente_no_se_cuela_por_post(
        self, logged_client, clinic_a, patient_a, professional_a, episode_a
    ):
        """No basta con que el desplegable no lo ofrezca: el campo lo rechaza."""
        otro = Patient.objects.create(
            clinic=clinic_a, first_name='Otra', last_name='Persona',
            email='otra@alpha.test', phone='+34600111222',
        )
        episodio_ajeno = Episode.objects.create(
            history=otro.medical_history, reason='Episodio de otra persona',
            responsible_professional=professional_a,
        )

        response = logged_client.post(
            create_url(patient_a), payload(episodio_ajeno), HTTP_HX_REQUEST='true'
        )

        assert response.status_code == 200
        assert 'episode' in response.context['lesion_form'].errors
        assert not Lesion.objects.exists()

    def test_un_paciente_de_otra_clinica_es_404_al_abrir_el_alta(
        self, logged_client, patient_b
    ):
        assert logged_client.get(create_url(patient_b)).status_code == 404
        assert logged_client.get(
            create_url(patient_b), HTTP_HX_REQUEST='true'
        ).status_code == 404
        # Un 404 no es una lectura: no deja rastro de acceso.
        assert not AccessLog.objects.exists()

    def test_un_paciente_de_otra_clinica_es_404_tambien_al_registrar(
        self, logged_client, patient_b, professional_a
    ):
        episodio_b = Episode.objects.create(
            history=patient_b.medical_history, reason='Proceso de otra clínica',
        )

        response = logged_client.post(
            create_url(patient_b), payload(episodio_b), HTTP_HX_REQUEST='true'
        )

        assert response.status_code == 404
        assert not Lesion.objects.exists()

    def test_sin_sesion_no_se_registra_nada(self, client, patient_a, episode_a):
        response = client.post(create_url(patient_a), payload(episode_a))

        assert response.status_code == 302   # al login
        assert not Lesion.objects.exists()

    def test_el_mapa_a_secas_tambien_aisla(self, logged_client, patient_b):
        """La URL de «cancelar» es igual de invocable: mismo 404."""
        assert logged_client.get(map_url(patient_b), HTTP_HX_REQUEST='true').status_code == 404


# ---------------------------------------------------------------------------
# Cancelar
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_cancelar_devuelve_el_mapa_sin_formulario(
    logged_client, patient_a, episode_a
):
    from tests.patients.test_lesion_map import make_lesion

    lesion = make_lesion(episode_a)

    response = logged_client.get(map_url(patient_a), HTTP_HX_REQUEST='true')
    html = response.content.decode()

    assert 'Nueva lesión' not in html
    assert f'data-lesion="{lesion.pk}"' in html
    assert 'data-lesion-pendiente' not in html


# ---------------------------------------------------------------------------
# Auditoría
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_la_escritura_queda_en_el_registro_y_la_lectura_tambien(
    logged_client, patient_a, episode_a
):
    """La escritura la capta `ChangeLog` por señales; la lectura, `AccessLogMixin`."""
    logged_client.get(create_url(patient_a))
    assert AccessLog.objects.filter(patient=patient_a).exists()

    logged_client.post(create_url(patient_a), payload(episode_a), HTTP_HX_REQUEST='true')
    assert ChangeLog.objects.filter(
        content_type__model='lesion', action=ChangeLog.Action.CREATE
    ).exists()
