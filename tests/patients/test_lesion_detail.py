"""Detalle de una lesión y su seguimiento (`patients:lesion-detail` y compañía).

El mapa y el alta de la lesión tienen sus pruebas en `test_lesion_map.py` y
`test_lesion_create.py`. Aquí se defiende lo que aporta el detalle, que es lo que
convierte una lesión en una **serie**:

1. Al abrir una lesión se leen **sus** observaciones y las de ninguna otra, y el
   marcador correspondiente queda señalado en el mapa.
2. Una observación necesita una **visita del mismo episodio**: la del episodio de
   al lado no se cuela ni mandándola por POST, y si no hay ninguna se registra
   una en vez de dejar el seguimiento a medias.
3. Las **fechas imposibles** son errores de campo del servidor: una observación
   en el futuro o anterior a la detección de la lesión, y lo mismo al darla de
   alta.
4. **Resolver es `Lesion.resolve()`**, no un `update`: estado y fecha quedan
   coherentes, el marcador cambia de color en la misma respuesta y el cambio deja
   rastro en el `ChangeLog`.
5. El **aislamiento** es el de siempre: la lesión de otra persona —o de otra
   clínica— es un 404 también en estas URLs, que son invocables directamente.
"""
import pytest
from django.urls import reverse
from django.utils import timezone

from audit.models import AccessLog, ChangeLog
from clinical.models import Episode, Lesion, LesionObservation, Visit
from patients.models import Patient


def detail_url(patient, lesion):
    return reverse('patients:lesion-detail', kwargs={'id': patient.pk, 'lesion_id': lesion.pk})


def observation_url(patient, lesion):
    return reverse(
        'patients:observation-create', kwargs={'id': patient.pk, 'lesion_id': lesion.pk}
    )


def resolve_url(patient, lesion):
    return reverse('patients:lesion-resolve', kwargs={'id': patient.pk, 'lesion_id': lesion.pk})


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


def observation_payload(**kwargs):
    """POST mínimo válido: sin visita elegida, se registra una."""
    data = {
        'visit': '',
        'observed_at': timezone.localdate().isoformat(),
        'length_mm': '',
        'width_mm': '',
        'depth_mm': '',
        'description': 'Lecho limpio, bordes epitelizados.',
    }
    data.update(kwargs)
    return data


@pytest.fixture
def logged_client(client, admin_user):
    client.force_login(admin_user)
    return client


@pytest.fixture
def lesion_a(db, episode_a):
    return make_lesion(episode_a)


# ---------------------------------------------------------------------------
# Abrir el detalle
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAbrirElDetalle:
    def test_trae_los_datos_de_la_lesion_y_su_seguimiento(
        self, logged_client, patient_a, lesion_a, visit_a, professional_a
    ):
        LesionObservation.objects.create(
            lesion=lesion_a, visit=visit_a, observed_at=timezone.localdate(),
            length_mm='14.0', width_mm='9.5', depth_mm='3.0',
            description='Úlcera con bordes hiperqueratósicos.',
            created_by=professional_a,
        )

        response = logged_client.get(detail_url(patient_a, lesion_a))
        html = response.content.decode()

        assert response.status_code == 200
        assert response.context['selected_lesion'] == lesion_a
        assert len(response.context['lesion_observations']) == 1
        assert 'bordes hiperqueratósicos' in html
        assert '14,0 mm' in html or '14.0 mm' in html

    def test_solo_las_observaciones_de_esa_lesion(
        self, logged_client, patient_a, episode_a, lesion_a, visit_a
    ):
        """Dos lesiones del mismo episodio no comparten seguimiento."""
        otra = make_lesion(episode_a, anatomical_zone=Lesion.AnatomicalZone.HEEL)
        LesionObservation.objects.create(
            lesion=otra, visit=visit_a, description='Fisura en el talón.',
        )

        response = logged_client.get(detail_url(patient_a, lesion_a))

        assert response.context['lesion_observations'] == []
        assert 'Fisura en el talón' not in response.content.decode()

    def test_el_marcador_de_la_lesion_abierta_queda_senalado(
        self, logged_client, patient_a, lesion_a
    ):
        html = logged_client.get(detail_url(patient_a, lesion_a)).content.decode()

        assert f'data-lesion-seleccionada="{lesion_a.pk}"' in html

    def test_el_mapa_se_abre_por_la_vista_de_la_lesion(
        self, logged_client, patient_a, episode_a
    ):
        """Sin htmx se pinta la página entera: el mapa tiene que enseñar el punto."""
        lesion = make_lesion(
            episode_a, view=Lesion.View.MEDIAL, laterality=Lesion.Laterality.RIGHT,
        )

        response = logged_client.get(detail_url(patient_a, lesion))

        assert response.context['default_view'] == Lesion.View.MEDIAL
        assert response.context['default_laterality'] == Lesion.Laterality.RIGHT

    def test_una_lesion_sin_seguimiento_lo_dice(self, logged_client, patient_a, lesion_a):
        assert 'Sin observaciones todavía' in logged_client.get(
            detail_url(patient_a, lesion_a)
        ).content.decode()

    def test_con_htmx_llega_solo_la_region(self, logged_client, patient_a, lesion_a):
        html = logged_client.get(
            detail_url(patient_a, lesion_a), HTTP_HX_REQUEST='true'
        ).content.decode()

        assert '<!DOCTYPE html>' not in html
        assert 'Seguimiento' in html

    def test_sin_htmx_es_una_pagina_completa(self, logged_client, patient_a, lesion_a):
        """El detalle es una URL de verdad: sin JavaScript también se lee."""
        html = logged_client.get(detail_url(patient_a, lesion_a)).content.decode()

        assert '<!DOCTYPE html>' in html
        assert 'Seguimiento' in html


# ---------------------------------------------------------------------------
# Registrar una observación
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestRegistrarObservacion:
    def test_se_cuelga_de_la_visita_elegida(
        self, logged_client, patient_a, lesion_a, visit_a
    ):
        response = logged_client.post(
            observation_url(patient_a, lesion_a),
            observation_payload(visit=visit_a.pk, length_mm='12.5', width_mm='7'),
            HTTP_HX_REQUEST='true',
        )
        observation = LesionObservation.objects.get(lesion=lesion_a)

        assert response.status_code == 200
        assert observation.visit == visit_a
        assert str(observation.length_mm) == '12.5'
        # Vuelve el panel con la serie al día y el formulario cerrado.
        assert 'Observación del' in response.content.decode()

    def test_sin_visita_elegida_se_registra_una(
        self, logged_client, patient_a, lesion_a, episode_a, admin_user
    ):
        """Observar una lesión ES un encuentro clínico: no se queda a medias."""
        assert not Visit.objects.filter(episode=episode_a).exists()

        logged_client.post(
            observation_url(patient_a, lesion_a), observation_payload(),
            HTTP_HX_REQUEST='true',
        )
        visit = Visit.objects.get(episode=episode_a)

        assert LesionObservation.objects.get(lesion=lesion_a).visit == visit
        assert visit.professional == admin_user.professional_profile

    def test_la_visita_de_otro_episodio_no_se_cuela_por_post(
        self, logged_client, patient_a, history_a, lesion_a, professional_a
    ):
        """El modelo lo rechazaría; aquí ni llega: el campo valida su queryset."""
        otro_episodio = Episode.objects.create(
            history=history_a, reason='Otro proceso', responsible_professional=professional_a,
        )
        visita_ajena = Visit.objects.create(
            episode=otro_episodio, professional=professional_a,
        )

        response = logged_client.post(
            observation_url(patient_a, lesion_a),
            observation_payload(visit=visita_ajena.pk),
            HTTP_HX_REQUEST='true',
        )

        assert 'visit' in response.context['observation_form'].errors
        assert not LesionObservation.objects.exists()

    def test_quien_registra_queda_anotado(
        self, logged_client, patient_a, lesion_a, visit_a, admin_user
    ):
        logged_client.post(
            observation_url(patient_a, lesion_a),
            observation_payload(visit=visit_a.pk),
            HTTP_HX_REQUEST='true',
        )

        assert LesionObservation.objects.get().created_by == admin_user.professional_profile

    @pytest.mark.parametrize('dias', [1, 30])
    def test_una_observacion_futura_se_rechaza(
        self, logged_client, patient_a, lesion_a, visit_a, dias
    ):
        futuro = (timezone.localdate() + timezone.timedelta(days=dias)).isoformat()

        response = logged_client.post(
            observation_url(patient_a, lesion_a),
            observation_payload(visit=visit_a.pk, observed_at=futuro),
            HTTP_HX_REQUEST='true',
        )

        assert 'observed_at' in response.context['observation_form'].errors
        assert not LesionObservation.objects.exists()

    def test_una_observacion_anterior_a_la_deteccion_se_rechaza(
        self, logged_client, patient_a, episode_a, visit_a
    ):
        """Antes de detectarla no había nada que observar: casi siempre es un dedazo."""
        lesion = make_lesion(
            episode_a, detected_at=timezone.localdate() - timezone.timedelta(days=3),
        )
        antes = (timezone.localdate() - timezone.timedelta(days=10)).isoformat()

        response = logged_client.post(
            observation_url(patient_a, lesion),
            observation_payload(visit=visit_a.pk, observed_at=antes),
            HTTP_HX_REQUEST='true',
        )

        assert 'observed_at' in response.context['observation_form'].errors

    def test_una_medida_negativa_se_rechaza(
        self, logged_client, patient_a, lesion_a, visit_a
    ):
        """El `CheckConstraint` de la tabla no se llega a rozar."""
        response = logged_client.post(
            observation_url(patient_a, lesion_a),
            observation_payload(visit=visit_a.pk, length_mm='-4'),
            HTTP_HX_REQUEST='true',
        )

        assert 'length_mm' in response.context['observation_form'].errors
        assert not LesionObservation.objects.exists()

    def test_las_medidas_son_opcionales(
        self, logged_client, patient_a, lesion_a, visit_a
    ):
        """Una hiperqueratosis se describe, no se mide."""
        logged_client.post(
            observation_url(patient_a, lesion_a),
            observation_payload(visit=visit_a.pk),
            HTTP_HX_REQUEST='true',
        )
        observation = LesionObservation.objects.get()

        assert (observation.length_mm, observation.width_mm, observation.depth_mm) == (
            None, None, None
        )

    def test_sin_htmx_redirige_al_detalle_con_su_aviso(
        self, logged_client, patient_a, lesion_a, visit_a
    ):
        response = logged_client.post(
            observation_url(patient_a, lesion_a),
            observation_payload(visit=visit_a.pk), follow=True,
        )
        textos = [str(m) for m in response.context['messages']]

        assert LesionObservation.objects.count() == 1
        assert any('Observación registrada' in texto for texto in textos)


# ---------------------------------------------------------------------------
# Episodio cerrado
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestEpisodioCerrado:
    def test_sin_visitas_no_se_pueden_anotar_observaciones(
        self, logged_client, patient_a, episode_a
    ):
        """Un episodio cerrado no admite visitas nuevas, y sin visita no hay observación."""
        lesion = make_lesion(episode_a)
        episode_a.close()

        response = logged_client.get(detail_url(patient_a, lesion))

        assert response.context['can_add_observation'] is False
        assert 'está cerrado y no tiene visitas registradas' in response.content.decode()

    def test_con_visitas_la_visita_pasa_a_ser_obligatoria(
        self, logged_client, patient_a, episode_a, visit_a
    ):
        lesion = make_lesion(episode_a)
        episode_a.close()

        response = logged_client.post(
            observation_url(patient_a, lesion), observation_payload(visit=''),
            HTTP_HX_REQUEST='true',
        )

        assert 'visit' in response.context['observation_form'].errors
        assert not LesionObservation.objects.exists()

        # Sobre una visita que sí hubo, la observación se registra igual.
        logged_client.post(
            observation_url(patient_a, lesion), observation_payload(visit=visit_a.pk),
            HTTP_HX_REQUEST='true',
        )
        assert LesionObservation.objects.count() == 1


# ---------------------------------------------------------------------------
# Dar de alta la lesión
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestResolverLaLesion:
    def test_la_cierra_con_su_fecha_y_repinta_el_marcador(
        self, logged_client, patient_a, lesion_a
    ):
        hoy = timezone.localdate()

        response = logged_client.post(
            resolve_url(patient_a, lesion_a), {'resolved_at': hoy.isoformat()},
            HTTP_HX_REQUEST='true',
        )
        lesion_a.refresh_from_db()
        html = response.content.decode()

        assert lesion_a.status == Lesion.Status.RESOLVED
        assert lesion_a.resolved_at == hoy
        # El marcador viaja en la misma respuesta, ya en verde.
        assert 'fill-success-soft stroke-success' in html
        assert 'Lesión dada de alta' in html

    def test_sin_fecha_no_se_cierra(self, logged_client, patient_a, lesion_a):
        response = logged_client.post(
            resolve_url(patient_a, lesion_a), {'resolved_at': ''}, HTTP_HX_REQUEST='true',
        )
        lesion_a.refresh_from_db()

        assert 'resolved_at' in response.context['resolve_form'].errors
        assert lesion_a.status == Lesion.Status.ACTIVE

    @pytest.mark.parametrize('desplazamiento', [1, 5])
    def test_una_fecha_futura_se_rechaza(
        self, logged_client, patient_a, lesion_a, desplazamiento
    ):
        futuro = (timezone.localdate() + timezone.timedelta(days=desplazamiento)).isoformat()

        response = logged_client.post(
            resolve_url(patient_a, lesion_a), {'resolved_at': futuro},
            HTTP_HX_REQUEST='true',
        )
        lesion_a.refresh_from_db()

        assert 'resolved_at' in response.context['resolve_form'].errors
        assert lesion_a.status == Lesion.Status.ACTIVE

    def test_no_se_puede_resolver_antes_de_detectarse(
        self, logged_client, patient_a, episode_a
    ):
        lesion = make_lesion(
            episode_a, detected_at=timezone.localdate() - timezone.timedelta(days=2),
        )
        antes = (timezone.localdate() - timezone.timedelta(days=9)).isoformat()

        response = logged_client.post(
            resolve_url(patient_a, lesion), {'resolved_at': antes}, HTTP_HX_REQUEST='true',
        )

        assert 'resolved_at' in response.context['resolve_form'].errors

    def test_una_lesion_ya_resuelta_no_se_vuelve_a_cerrar(
        self, logged_client, patient_a, lesion_a
    ):
        alta = timezone.localdate() - timezone.timedelta(days=4)
        lesion_a.resolve(on=alta)

        response = logged_client.post(
            resolve_url(patient_a, lesion_a),
            {'resolved_at': timezone.localdate().isoformat()},
            HTTP_HX_REQUEST='true',
        )
        lesion_a.refresh_from_db()

        assert response.status_code == 200
        assert lesion_a.resolved_at == alta   # la fecha original, intacta

    def test_sin_htmx_redirige_al_detalle_con_su_aviso(
        self, logged_client, patient_a, lesion_a
    ):
        response = logged_client.post(
            resolve_url(patient_a, lesion_a),
            {'resolved_at': timezone.localdate().isoformat()}, follow=True,
        )
        textos = [str(m) for m in response.context['messages']]

        assert any('resuelta' in texto for texto in textos)


# ---------------------------------------------------------------------------
# Aislamiento
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAislamiento:
    @pytest.fixture
    def lesion_ajena(self, db, clinic_a, professional_a):
        """Lesión de OTRO paciente de la misma clínica."""
        otro = Patient.objects.create(
            clinic=clinic_a, first_name='Otra', last_name='Persona',
            email='otra@alpha.test', phone='+34600111222',
        )
        episodio = Episode.objects.create(
            history=otro.medical_history, reason='Proceso de otra persona',
            responsible_professional=professional_a,
        )
        return make_lesion(episodio)

    def test_la_lesion_de_otro_paciente_es_404(
        self, logged_client, patient_a, lesion_ajena
    ):
        """Filtrar por clínica no basta: la lesión se busca dentro del paciente."""
        for url in (
            detail_url(patient_a, lesion_ajena),
            observation_url(patient_a, lesion_ajena),
            resolve_url(patient_a, lesion_ajena),
        ):
            assert logged_client.get(url, HTTP_HX_REQUEST='true').status_code == 404

    def test_tampoco_se_le_puede_escribir(
        self, logged_client, patient_a, lesion_ajena, visit_a
    ):
        response = logged_client.post(
            observation_url(patient_a, lesion_ajena),
            observation_payload(visit=visit_a.pk), HTTP_HX_REQUEST='true',
        )

        assert response.status_code == 404
        assert not LesionObservation.objects.exists()

    def test_un_paciente_de_otra_clinica_es_404(
        self, logged_client, patient_b, professional_a
    ):
        episodio_b = Episode.objects.create(
            history=patient_b.medical_history, reason='Proceso de otra clínica',
        )
        lesion_b = make_lesion(episodio_b)

        assert logged_client.get(detail_url(patient_b, lesion_b)).status_code == 404
        # Un 404 no es una lectura: no deja rastro de acceso.
        assert not AccessLog.objects.exists()

    def test_sin_sesion_no_se_lee_ni_se_escribe(self, client, patient_a, lesion_a, visit_a):
        assert client.get(detail_url(patient_a, lesion_a)).status_code == 302
        assert client.post(
            observation_url(patient_a, lesion_a), observation_payload(visit=visit_a.pk)
        ).status_code == 302
        assert not LesionObservation.objects.exists()


# ---------------------------------------------------------------------------
# Auditoría
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_la_lectura_del_detalle_y_las_escrituras_quedan_registradas(
    logged_client, patient_a, lesion_a, visit_a
):
    """La lectura la registra `AccessLogMixin`; las escrituras, las señales."""
    logged_client.get(detail_url(patient_a, lesion_a))
    assert AccessLog.objects.filter(
        patient=patient_a, action=AccessLog.Action.VIEW
    ).exists()

    logged_client.post(
        observation_url(patient_a, lesion_a), observation_payload(visit=visit_a.pk),
        HTTP_HX_REQUEST='true',
    )
    assert ChangeLog.objects.filter(
        content_type__model='lesionobservation', action=ChangeLog.Action.CREATE
    ).exists()

    logged_client.post(
        resolve_url(patient_a, lesion_a),
        {'resolved_at': timezone.localdate().isoformat()}, HTTP_HX_REQUEST='true',
    )
    assert ChangeLog.objects.filter(
        content_type__model='lesion', action=ChangeLog.Action.UPDATE
    ).exists()
