"""Lateralidad del mapa del pie: qué dibujo se ve en cada combinación.

Lo que se defiende aquí:

1. **La anatomía de cada combinación**: en dorsal el hallux del pie derecho queda
   a la izquierda y el del izquierdo a la derecha; en plantar, al revés; en las
   vistas de perfil los dedos apuntan a lados opuestos entre medial y lateral, y
   entre pies. Escrito como tabla, no derivado: si alguien «simplifica» la regla,
   este test dice qué pie ha dejado de ser el que era.
2. **Las coordenadas guardadas son del dibujo canónico**: un mismo punto
   anatómico vale lo mismo en los dos pies, y el marcador de una combinación
   reflejada se pinta en `(1 - x) · ancho`.
3. **Los textos no se reflejan**: los rótulos se colocan ya reflejados, con el
   anclaje intercambiado, y nunca dentro de un grupo con `transform`.
"""
import pytest
from django.urls import reverse

from clinical.models import Lesion
from patients import foot_map


# ---------------------------------------------------------------------------
# La regla, sin base de datos
# ---------------------------------------------------------------------------

#: Hacia dónde queda, para el observador, lo que da la orientación de cada vista.
#: dorsal/plantar: el hallux (lado medial). medial/lateral: los dedos.
ANATOMIA = {
    ('dorsal', 'right'): 'izquierda',
    ('dorsal', 'left'): 'derecha',
    ('plantar', 'right'): 'derecha',
    ('plantar', 'left'): 'izquierda',
    ('medial', 'right'): 'izquierda',
    ('medial', 'left'): 'derecha',
    ('lateral', 'right'): 'derecha',
    ('lateral', 'left'): 'izquierda',
}

#: En los cuatro dibujos canónicos, eso queda a la izquierda.
CANONICO = 'izquierda'


@pytest.mark.parametrize('combinacion, lado', ANATOMIA.items())
def test_cada_combinacion_tiene_su_orientacion_anatomica(combinacion, lado):
    view, laterality = combinacion
    se_ve_reflejado = foot_map.is_mirrored(view, laterality)
    assert se_ve_reflejado == (lado != CANONICO)


@pytest.mark.parametrize('view', foot_map.VIEWS)
def test_los_dos_pies_de_una_vista_son_imagen_especular(view):
    assert foot_map.is_mirrored(view, 'left') != foot_map.is_mirrored(view, 'right')


@pytest.mark.parametrize('laterality', foot_map.LATERALITIES)
def test_medial_y_lateral_miran_a_lados_opuestos_en_el_mismo_pie(laterality):
    assert foot_map.is_mirrored('medial', laterality) != foot_map.is_mirrored('lateral', laterality)


@pytest.mark.parametrize('laterality', foot_map.LATERALITIES)
def test_dorsal_y_plantar_miran_a_lados_opuestos_en_el_mismo_pie(laterality):
    assert foot_map.is_mirrored('dorsal', laterality) != foot_map.is_mirrored('plantar', laterality)


def test_la_tabla_del_navegador_es_la_misma_regla():
    tabla = foot_map.mirror_table()
    assert set(tabla) == set(foot_map.VIEWS)
    for view in foot_map.VIEWS:
        for laterality in foot_map.LATERALITIES:
            assert tabla[view][laterality] is foot_map.is_mirrored(view, laterality)


@pytest.mark.parametrize('combinacion', ANATOMIA)
@pytest.mark.parametrize('x', [0.0, 0.25, 0.5, 0.9731, 1.0])
def test_desreflejar_y_reflejar_devuelve_el_mismo_punto(combinacion, x):
    view, laterality = combinacion
    ida = foot_map.canonical_x(x, view, laterality)
    assert foot_map.canonical_x(ida, view, laterality) == pytest.approx(x)


def test_el_transform_solo_lo_llevan_las_combinaciones_reflejadas():
    assert foot_map.mirror_transform('dorsal', 'right') == ''
    assert foot_map.mirror_transform('dorsal', 'left') == f'matrix(-1 0 0 1 {foot_map.WIDTH} 0)'


@pytest.mark.parametrize('combinacion', ANATOMIA)
def test_los_rotulos_caben_y_se_reflejan_sin_invertir_el_texto(combinacion):
    view, laterality = combinacion
    rotulos = foot_map.scene_labels(view, laterality)
    canonicos = foot_map.LABELS[view]
    assert len(rotulos) == len(canonicos)
    for rotulo, canonico in zip(rotulos, canonicos):
        for clave in ('cx', 'tx', 'lx'):
            assert 0 <= rotulo[clave] <= foot_map.WIDTH
        for clave in ('cy', 'ty', 'ly'):
            assert 0 <= rotulo[clave] <= foot_map.HEIGHT
        if foot_map.is_mirrored(view, laterality):
            assert rotulo['cx'] == foot_map.WIDTH - canonico.cx
            assert rotulo['anchor'] == {'start': 'end', 'end': 'start'}.get(
                canonico.anchor, canonico.anchor
            )
        else:
            assert rotulo['cx'] == canonico.cx
            assert rotulo['anchor'] == canonico.anchor


@pytest.mark.parametrize('laterality', foot_map.LATERALITIES)
def test_el_rotulo_medial_queda_del_lado_del_hallux(laterality):
    """En dorsal y plantar, «Medial» se escribe del mismo lado que el hallux."""
    for view in ('dorsal', 'plantar'):
        medial = next(t for t in foot_map.scene_side_tags(view, laterality) if t['text'] == 'Medial')
        hallux = next(r for r in foot_map.scene_labels(view, laterality) if r['text'] == 'Hallux')
        mitad = foot_map.WIDTH / 2
        assert (medial['x'] < mitad) == (hallux['cx'] < mitad)


def test_hay_una_escena_por_combinacion_y_dice_que_pie_es():
    escenas = foot_map.build_map_scenes()
    assert {(e['view'], e['laterality']) for e in escenas} == set(ANATOMIA)
    for escena in escenas:
        esperado = 'Pie derecho' if escena['laterality'] == 'right' else 'Pie izquierdo'
        assert escena['caption'].startswith(esperado)


# ---------------------------------------------------------------------------
# El pintado en la ficha
# ---------------------------------------------------------------------------

def tab_url(patient):
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


def marker_html(html, lesion):
    """El fragmento del marcador de esa lesión (hasta el cierre de su grupo)."""
    start = html.index(f'data-lesion="{lesion.pk}"')
    return html[start:html.index('</g>', start)]


@pytest.mark.django_db
class TestPintado:
    def test_el_marcador_de_una_combinacion_reflejada_se_refleja(
        self, client, admin_user, patient_a, episode_a
    ):
        """Mismo punto guardado, mismo hallux: a la izquierda en plantar izquierdo,
        a la derecha en plantar derecho."""
        izquierdo = make_lesion(episode_a, laterality='left', x=0.25, y=0.5)
        derecho = make_lesion(episode_a, laterality='right', x=0.25, y=0.5)

        client.force_login(admin_user)
        html = client.get(tab_url(patient_a)).content.decode()

        assert 'cx="100"' in marker_html(html, izquierdo)
        assert 'cx="300"' in marker_html(html, derecho)
        assert 'cy="260"' in marker_html(html, derecho)
        # El marcador no se refleja con un `transform`: lo que lleva dentro se lee.
        assert 'transform' not in marker_html(html, derecho)
        # Y el dato guardado no se toca.
        derecho.refresh_from_db()
        assert derecho.x == 0.25

    def test_el_dibujo_se_abre_reflejado_si_la_combinacion_lo_esta(
        self, client, admin_user, patient_a, episode_a
    ):
        make_lesion(episode_a, view='medial', laterality='left')

        client.force_login(admin_user)
        html = client.get(tab_url(patient_a)).content.decode()

        start = html.index('data-dibujo="medial"')
        grupo = html[html.rindex('<g', 0, start):start]
        assert f'transform="{foot_map.MIRROR_TRANSFORM}"' in grupo

    def test_el_navegador_recibe_la_regla_ya_resuelta(self, client, admin_user, patient_a):
        client.force_login(admin_user)
        html = client.get(tab_url(patient_a)).content.decode()

        assert 'id="mapa-lesiones-espejo"' in html
        assert '"plantar": {"left": false, "right": true}' in html

    def test_los_rotulos_no_van_en_un_grupo_reflejado(self, client, admin_user, patient_a):
        client.force_login(admin_user)
        html = client.get(tab_url(patient_a)).content.decode()

        for view in foot_map.VIEWS:
            for laterality in foot_map.LATERALITIES:
                start = html.index(f'data-rotulos="{view}-{laterality}"')
                grupo = html[html.rindex('<g', 0, start):start]
                assert 'transform' not in grupo

    def test_activa_y_resuelta_se_distinguen_tambien_por_forma(
        self, client, admin_user, patient_a, episode_a
    ):
        activa = make_lesion(episode_a)
        resuelta = make_lesion(episode_a, x=0.6)
        resuelta.resolve()

        client.force_login(admin_user)
        html = client.get(tab_url(patient_a)).content.decode()

        assert 'r="5.5"' in marker_html(html, activa)
        assert 'stroke-linecap="round"' in marker_html(html, resuelta)
        assert '<path' in marker_html(html, resuelta)

    def test_el_punto_pendiente_se_refleja_igual_que_los_marcadores(
        self, client, admin_user, patient_a, episode_a
    ):
        client.force_login(admin_user)
        response = client.get(
            reverse('patients:lesion-create', kwargs={'id': patient_a.pk}),
            {'vista': 'plantar', 'pie': 'right', 'x': '0.3', 'y': '0.7'},
            HTTP_HX_REQUEST='true',
        )
        html = response.content.decode()

        # Se prerrellena con la fracción canónica tal cual llegó…
        assert response.context['lesion_form']['x'].value() == 0.3
        # …y se pinta reflejada, donde se hizo el clic.
        start = html.index('data-lesion-pendiente')
        assert f'cx="{round(0.7 * foot_map.WIDTH)}"' in html[start:html.index('</g>', start)]
