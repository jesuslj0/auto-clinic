"""El mapa del pie de la pestaña «Lesiones»: cómo se pinta.

El alta de una lesión sobre ese mapa (captura del clic, formulario y refresco de
los marcadores) tiene sus propias pruebas en `test_lesion_create.py`.

Lo que se defiende aquí:

1. Se pintan las lesiones **de este paciente** y de ninguno más, aunque el otro
   sea de la misma clínica (filtrar por clínica no basta).
2. Los marcadores salen en **unidades del `viewBox`**: `x`/`y` son fracciones
   0–1 y llegan al SVG multiplicadas por el ancho y el alto del dibujo. Es lo que
   hace que el mapa escale a cualquier tamaño, y lo único que no puede fallar.
3. El panel ofrece las **cuatro vistas y los dos pies**, y cada marcador declara
   a qué combinación pertenece.
4. El parcial es una URL invocable directamente: con la cabecera de htmx, un
   paciente de otra clínica sigue siendo un 404.
"""
import pytest
from django.urls import reverse

from audit.models import AccessLog
from clinical.models import Episode, Lesion
from patients.models import Patient


#: Las mismas unidades que declara el `viewBox` de `patients/tabs/_lesiones.html`.
#: Si el dibujo cambia de tamaño, este es el único sitio del test que se toca.
MAP_WIDTH = 400
MAP_HEIGHT = 520


def url(patient):
    return reverse('patients:tab-lesions', kwargs={'id': patient.pk})


def make_lesion(episode, **kwargs):
    defaults = {
        'laterality': Lesion.Laterality.LEFT,
        'view': Lesion.View.PLANTAR,
        'anatomical_zone': Lesion.AnatomicalZone.FIRST_METATARSAL,
        'x': 0.25,
        'y': 0.5,
        'lesion_type': Lesion.LesionType.ULCER,
    }
    defaults.update(kwargs)
    return Lesion.objects.create(episode=episode, **defaults)


@pytest.fixture
def other_patient(db, clinic_a, professional_a):
    """Otro paciente de la MISMA clínica, con su propio episodio."""
    patient = Patient.objects.create(
        clinic=clinic_a, first_name='Otra', last_name='Persona',
        email='otra@alpha.test', phone='+34600111222',
    )
    patient.test_episode = Episode.objects.create(
        history=patient.medical_history,
        reason='Revisión de otra persona',
        responsible_professional=professional_a,
    )
    return patient


# ---------------------------------------------------------------------------
# Qué lesiones se pintan
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestQueLesionesSePintan:
    def test_solo_las_del_paciente_pedido(
        self, client, admin_user, patient_a, episode_a, other_patient
    ):
        """Un vecino de la misma clínica no se cuela en el mapa."""
        mine = make_lesion(episode_a, anatomical_zone=Lesion.AnatomicalZone.HEEL)
        theirs = make_lesion(other_patient.test_episode)

        client.force_login(admin_user)
        response = client.get(url(patient_a))
        html = response.content.decode()

        assert list(response.context['lesions']) == [mine]
        assert f'data-lesion="{mine.pk}"' in html
        assert f'data-lesion="{theirs.pk}"' not in html

    def test_recoge_las_lesiones_de_todos_los_episodios(
        self, client, admin_user, patient_a, episode_a, professional_a
    ):
        """La pestaña es del paciente, no de un episodio: se ven todas."""
        otro_episodio = Episode.objects.create(
            history=patient_a.medical_history,
            reason='Segundo proceso',
            responsible_professional=professional_a,
        )
        primera = make_lesion(episode_a)
        segunda = make_lesion(otro_episodio, view=Lesion.View.DORSAL)

        client.force_login(admin_user)
        lesiones = client.get(url(patient_a)).context['lesions']

        assert set(lesiones) == {primera, segunda}

    def test_una_lesion_borrada_no_se_pinta(
        self, client, admin_user, patient_a, episode_a
    ):
        """El borrado es lógico, pero deja de verse."""
        lesion = make_lesion(episode_a)
        lesion.delete()

        client.force_login(admin_user)
        response = client.get(url(patient_a))

        assert list(response.context['lesions']) == []
        assert f'data-lesion="{lesion.pk}"' not in response.content.decode()


# ---------------------------------------------------------------------------
# Coordenadas: lo único que no puede fallar
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCoordenadasDelMarcador:
    def test_se_pintan_en_unidades_del_viewbox(
        self, client, admin_user, patient_a, episode_a
    ):
        """(x · ancho, y · alto), nunca la fracción cruda ni un píxel fijo."""
        make_lesion(episode_a, x=0.25, y=0.5)

        client.force_login(admin_user)
        html = client.get(url(patient_a)).content.decode()

        assert f'cx="{0.25 * MAP_WIDTH:.0f}"' in html      # 100
        assert f'cy="{0.5 * MAP_HEIGHT:.0f}"' in html      # 260
        # La fracción no se cuela como coordenada del SVG.
        assert 'cx="0.25"' not in html
        assert 'cy="0.5"' not in html

    def test_el_viewbox_es_el_que_esperan_las_coordenadas(
        self, client, admin_user, patient_a
    ):
        """El dibujo declara su sistema de coordenadas; sin él nada escala."""
        client.force_login(admin_user)
        html = client.get(url(patient_a)).content.decode()

        assert f'viewBox="0 0 {MAP_WIDTH} {MAP_HEIGHT}"' in html
        # Escala con el contenedor: ni width ni height fijos en el SVG.
        assert 'class="h-auto w-full"' in html

    @pytest.mark.parametrize(
        'x, y', [(0.0, 0.0), (1.0, 1.0), (0.735, 0.128)],
    )
    def test_los_extremos_y_los_decimales_tambien_cuadran(
        self, client, admin_user, patient_a, episode_a, x, y
    ):
        make_lesion(episode_a, x=x, y=y)

        client.force_login(admin_user)
        html = client.get(url(patient_a)).content.decode()

        assert f'cx="{round(x * MAP_WIDTH)}"' in html
        assert f'cy="{round(y * MAP_HEIGHT)}"' in html

    def test_la_zona_anatomica_no_se_deriva_del_punto(
        self, client, admin_user, patient_a, episode_a
    ):
        """La zona es el dato clínico y se enseña tal cual, caiga donde caiga."""
        make_lesion(
            episode_a, x=0.9, y=0.05,
            anatomical_zone=Lesion.AnatomicalZone.HEEL,
        )

        client.force_login(admin_user)
        html = client.get(url(patient_a)).content.decode()

        assert 'Talón' in html


# ---------------------------------------------------------------------------
# Vistas, pies y filtrado
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestVistasYPies:
    def test_estan_las_cuatro_vistas_y_los_dos_pies(
        self, client, admin_user, patient_a
    ):
        client.force_login(admin_user)
        html = client.get(url(patient_a)).content.decode()

        for label in ('Dorsal', 'Plantar', 'Medial', 'Lateral'):
            assert f'>{label}</button>' in html
        for label in ('Pie izquierdo', 'Pie derecho'):
            assert f'>{label}</button>' in html
        assert 'role="radiogroup"' in html
        assert 'aria-label="Vista del pie"' in html
        assert 'aria-label="Pie"' in html

    def test_cada_marcador_declara_su_vista_y_su_pie(
        self, client, admin_user, patient_a, episode_a
    ):
        """El filtro lo escribe el servidor en el `x-show`; Alpine solo lo evalúa."""
        make_lesion(
            episode_a, view=Lesion.View.MEDIAL,
            laterality=Lesion.Laterality.RIGHT,
        )

        client.force_login(admin_user)
        html = client.get(url(patient_a)).content.decode()

        assert "vista === 'medial' && pie === 'right'" in html

    def test_el_mapa_se_abre_donde_hay_lesiones(
        self, client, admin_user, patient_a, episode_a
    ):
        """Abrir siempre en dorsal/izquierdo dejaría el mapa vacío casi siempre."""
        make_lesion(
            episode_a, view=Lesion.View.LATERAL,
            laterality=Lesion.Laterality.RIGHT,
        )
        make_lesion(
            episode_a, view=Lesion.View.LATERAL,
            laterality=Lesion.Laterality.RIGHT,
            anatomical_zone=Lesion.AnatomicalZone.FIFTH_TOE,
        )
        make_lesion(episode_a, view=Lesion.View.DORSAL)

        client.force_login(admin_user)
        response = client.get(url(patient_a))

        assert response.context['default_view'] == Lesion.View.LATERAL
        assert response.context['default_laterality'] == Lesion.Laterality.RIGHT
        assert "vista: 'lateral', pie: 'right'" in response.content.decode()

    def test_sin_lesiones_se_abre_en_la_primera_combinacion(
        self, client, admin_user, patient_a
    ):
        client.force_login(admin_user)
        response = client.get(url(patient_a))

        assert response.context['default_view'] == Lesion.View.DORSAL
        assert response.context['default_laterality'] == Lesion.Laterality.LEFT


# ---------------------------------------------------------------------------
# Contexto junto al mapa: recuentos, estados y colores
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestResumenDeLaVista:
    def test_hay_una_escena_por_combinacion_con_sus_recuentos(
        self, client, admin_user, patient_a, episode_a
    ):
        activa = make_lesion(episode_a, view=Lesion.View.PLANTAR)
        resuelta = make_lesion(
            episode_a, view=Lesion.View.PLANTAR,
            anatomical_zone=Lesion.AnatomicalZone.HEEL,
        )
        resuelta.resolve()
        assert activa.status == Lesion.Status.ACTIVE

        client.force_login(admin_user)
        scenes = client.get(url(patient_a)).context['lesion_scenes']

        assert len(scenes) == 8  # 4 vistas × 2 pies
        plantar_izq = next(
            scene for scene in scenes
            if scene['view'] == 'plantar' and scene['laterality'] == 'left'
        )
        assert plantar_izq['total'] == 2
        assert plantar_izq['active'] == 1
        assert plantar_izq['resolved'] == 1
        assert all(
            scene['total'] == 0 for scene in scenes if scene is not plantar_izq
        )

    def test_el_estado_vacio_aparece_para_las_combinaciones_sin_lesiones(
        self, client, admin_user, patient_a, episode_a
    ):
        make_lesion(episode_a)

        client.force_login(admin_user)
        html = client.get(url(patient_a)).content.decode()

        # Siete combinaciones vacías de ocho.
        assert html.count('Sin lesiones en esta vista') == 7
        assert '1 lesión registrada' in html

    def test_activa_y_resuelta_se_distinguen_por_color_de_estado(
        self, client, admin_user, patient_a, episode_a
    ):
        make_lesion(episode_a)

        client.force_login(admin_user)
        html = client.get(url(patient_a)).content.decode()
        assert 'fill-danger-soft stroke-danger' in html
        assert 'fill-success-soft stroke-success' not in html

        Lesion.objects.get(pk=Lesion.objects.first().pk).resolve()
        html = client.get(url(patient_a)).content.decode()
        assert 'fill-success-soft stroke-success' in html
        assert 'fill-danger-soft stroke-danger' not in html

    def test_el_marcador_dice_que_lesion_es(
        self, client, admin_user, patient_a, episode_a
    ):
        """Un `<title>` por marcador: el color solo no informa a nadie."""
        make_lesion(
            episode_a,
            lesion_type=Lesion.LesionType.ULCER,
            anatomical_zone=Lesion.AnatomicalZone.HEEL,
        )

        client.force_login(admin_user)
        html = client.get(url(patient_a)).content.decode()

        assert '<title>Úlcera en Talón · activa' in html

    def test_los_marcadores_registrados_no_son_pulsables(
        self, client, admin_user, patient_a, episode_a
    ):
        """El mapa captura el clic para el alta; un marcador no abre nada.

        El detalle de una lesión no existe todavía, así que el grupo de cada
        marcador no puede insinuar que sí: ni `@click`, ni `hx-`, ni `href`. El
        único `@click` del dibujo es el del propio `<svg>`, que marca el punto
        de la lesión que se va a registrar.
        """
        lesion = make_lesion(episode_a)

        client.force_login(admin_user)
        html = client.get(url(patient_a)).content.decode()
        # El grupo del marcador, desde su `data-lesion` hasta que se cierra.
        marcador = html.split(f'data-lesion="{lesion.pk}"')[1].split('</g>')[0]

        assert '@click' not in marcador
        assert 'x-on:click' not in marcador
        assert 'hx-' not in marcador
        assert 'href' not in marcador


# ---------------------------------------------------------------------------
# El parcial es una URL: el permiso se comprueba igual
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestPermisoDelParcial:
    def test_otra_clinica_es_404_tambien_con_la_cabecera_de_htmx(
        self, client, admin_user, patient_b
    ):
        """Un parcial de htmx se puede pedir a pelo: el aislamiento no se relaja.

        Lo comprueba `PatientTabView.get_queryset()`, que filtra por la clínica
        del usuario antes de resolver el objeto; no hace falta una comprobación
        propia en la pestaña.
        """
        client.force_login(admin_user)

        assert client.get(url(patient_b)).status_code == 404
        assert client.get(url(patient_b), HTTP_HX_REQUEST='true').status_code == 404
        # Un 404 no es una lectura: no deja rastro de acceso.
        assert not AccessLog.objects.exists()

    def test_sin_sesion_no_se_sirve_el_parcial(self, client, patient_a):
        assert client.get(url(patient_a), HTTP_HX_REQUEST='true').status_code == 302

    def test_no_filtra_lesiones_de_otra_clinica_por_la_cabecera(
        self, client, admin_user, patient_b
    ):
        """Ni con htmx: el 404 llega antes de tocar ninguna lesión."""
        episodio_b = Episode.objects.create(
            history=patient_b.medical_history,
            reason='Proceso de otra clínica',
        )
        lesion_b = make_lesion(episodio_b)

        client.force_login(admin_user)
        response = client.get(url(patient_b), HTTP_HX_REQUEST='true')

        assert response.status_code == 404
        assert f'data-lesion="{lesion_b.pk}"' not in response.content.decode()

    def test_con_htmx_llega_solo_el_fragmento_y_queda_el_acceso(
        self, client, admin_user, patient_a, episode_a
    ):
        make_lesion(episode_a)

        client.force_login(admin_user)
        response = client.get(url(patient_a), HTTP_HX_REQUEST='true')
        html = response.content.decode()

        assert '<!DOCTYPE html>' not in html
        assert 'viewBox="0 0 400 520"' in html
        assert AccessLog.objects.filter(
            action=AccessLog.Action.VIEW, patient=patient_a
        ).exists()
