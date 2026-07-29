"""Fotos de una observación y vista de evolución de la lesión.

El detalle y las observaciones (texto y medidas) se prueban en
`test_lesion_detail.py`. Aquí va lo que añaden las fotografías, que es lo que
convierte el seguimiento en algo que se puede *mirar*:

1. Lo que se sube va al **almacén clínico privado** con clave UUID y validado por
   su contenido; lo que no es una imagen admitida no entra y lo dice por su
   nombre, sin dejar la observación registrada a medias.
2. **En el HTML no hay ninguna URL del bucket.** Cada imagen apunta a la vista
   protegida, que comprueba el permiso y firma en el momento — así la miniatura
   sigue resolviendo cuando la firma de hace diez minutos ya habría caducado.
3. La **evolución se lee en orden cronológico** y resume la variación de las
   medidas de la primera a la última.
4. El **comparador** solo compara observaciones de esa lesión, ordenadas por
   fecha, y el aislamiento es el mismo que en el resto de la ficha.
"""
from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from audit.models import AccessLog, ChangeLog
from clinical.models import Episode, LesionAttachment, LesionObservation
from patients.models import Patient

from tests.patients.test_lesion_detail import (
    make_lesion,
    observation_payload,
    observation_url,
)


def image_bytes(image_format='JPEG', size=(24, 24), color=(180, 60, 55)):
    """Una imagen de verdad: la validación mira el contenido, no el nombre."""
    buffer = BytesIO()
    Image.new('RGB', size, color).save(buffer, format=image_format)
    return buffer.getvalue()


def photo(name='pie_izquierdo.jpg', content=None, content_type='image/jpeg'):
    return SimpleUploadedFile(
        name, content if content is not None else image_bytes(), content_type=content_type
    )


def evolution_url(patient, lesion):
    return reverse(
        'patients:lesion-evolution', kwargs={'id': patient.pk, 'lesion_id': lesion.pk}
    )


def compare_url(patient, lesion):
    return reverse(
        'patients:lesion-compare', kwargs={'id': patient.pk, 'lesion_id': lesion.pk}
    )


@pytest.fixture
def logged_client(client, admin_user):
    client.force_login(admin_user)
    return client


@pytest.fixture
def lesion_a(db, episode_a):
    return make_lesion(episode_a)


def make_observation(lesion, visit, days_ago=0, **kwargs):
    return LesionObservation.objects.create(
        lesion=lesion, visit=visit,
        observed_at=timezone.localdate() - timezone.timedelta(days=days_ago),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Subida
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSubirFotos:
    def test_se_suben_con_la_observacion_y_van_al_almacen_privado(
        self, logged_client, patient_a, lesion_a, visit_a
    ):
        response = logged_client.post(
            observation_url(patient_a, lesion_a),
            observation_payload(
                visit=visit_a.pk,
                photos=[photo('foto_juan_perez_pie_izq.jpg'), photo('otra.png', image_bytes('PNG'))],
            ),
            HTTP_HX_REQUEST='true',
        )
        observation = LesionObservation.objects.get(lesion=lesion_a)
        attachments = list(observation.attachments.all())

        assert response.status_code == 200
        assert len(attachments) == 2
        for attachment in attachments:
            # Clave opaca bajo su prefijo: el nombre original no viaja al bucket.
            assert attachment.file.name.startswith('lesion-attachments/')
            assert 'juan' not in attachment.file.name
            assert 'perez' not in attachment.file.name
            # Lo que se guarda es lo que el fichero ES, examinado al guardarlo.
            assert attachment.mime_type in ('image/jpeg', 'image/png')
            assert attachment.checksum.startswith('sha256:')
            assert attachment.size_bytes > 0
            assert attachment.source == LesionAttachment.Source.PROFESSIONAL

    def test_una_observacion_puede_no_llevar_fotos(
        self, logged_client, patient_a, lesion_a, visit_a
    ):
        logged_client.post(
            observation_url(patient_a, lesion_a),
            observation_payload(visit=visit_a.pk), HTTP_HX_REQUEST='true',
        )

        assert LesionObservation.objects.get().attachments.count() == 0

    def test_lo_que_no_es_una_imagen_no_entra_ni_deja_la_observacion(
        self, logged_client, patient_a, lesion_a, visit_a
    ):
        """Un PDF renombrado a `.jpg` muere en la validación por contenido."""
        falsa = SimpleUploadedFile(
            'radiografia.jpg', b'%PDF-1.7\n%fake pdf content\n', content_type='image/jpeg'
        )

        response = logged_client.post(
            observation_url(patient_a, lesion_a),
            observation_payload(visit=visit_a.pk, photos=[falsa]),
            HTTP_HX_REQUEST='true',
        )
        errores = response.context['observation_form'].errors

        assert 'photos' in errores
        # El error dice DE QUÉ fichero habla: con cinco fotos, «una no vale» no sirve.
        assert 'radiografia.jpg' in str(errores['photos'])
        assert not LesionObservation.objects.exists()
        assert not LesionAttachment.objects.exists()

    def test_una_foto_mala_entre_varias_buenas_para_el_alta_entera(
        self, logged_client, patient_a, lesion_a, visit_a
    ):
        """O entra la observación con todas sus fotos, o no entra nada."""
        response = logged_client.post(
            observation_url(patient_a, lesion_a),
            observation_payload(
                visit=visit_a.pk,
                photos=[photo(), SimpleUploadedFile('mala.jpg', b'no soy una imagen')],
            ),
            HTTP_HX_REQUEST='true',
        )

        assert 'photos' in response.context['observation_form'].errors
        assert not LesionObservation.objects.exists()
        assert not LesionAttachment.objects.exists()

    def test_hay_un_tope_de_fotos_por_observacion(
        self, logged_client, patient_a, lesion_a, visit_a
    ):
        from clinical.forms import LesionObservationForm

        demasiadas = [
            photo(f'foto{indice}.jpg') for indice in range(LesionObservationForm.MAX_PHOTOS + 1)
        ]

        response = logged_client.post(
            observation_url(patient_a, lesion_a),
            observation_payload(visit=visit_a.pk, photos=demasiadas),
            HTTP_HX_REQUEST='true',
        )

        assert 'photos' in response.context['observation_form'].errors
        assert not LesionAttachment.objects.exists()

    def test_la_subida_queda_en_el_registro_de_cambios(
        self, logged_client, patient_a, lesion_a, visit_a
    ):
        """Se crean una a una precisamente para que las señales las capten."""
        logged_client.post(
            observation_url(patient_a, lesion_a),
            observation_payload(visit=visit_a.pk, photos=[photo(), photo('dos.jpg')]),
            HTTP_HX_REQUEST='true',
        )

        assert ChangeLog.objects.filter(
            content_type__model='lesionattachment', action=ChangeLog.Action.CREATE
        ).count() == 2


# ---------------------------------------------------------------------------
# Cómo se sirven las miniaturas
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestMiniaturas:
    @pytest.fixture
    def attachment(self, db, lesion_a, visit_a):
        observation = make_observation(lesion_a, visit_a, description='Con foto')
        return LesionAttachment.objects.create(observation=observation, file=photo())

    def test_el_panel_enlaza_a_la_vista_protegida_y_no_al_bucket(
        self, logged_client, patient_a, lesion_a, attachment
    ):
        from tests.patients.test_lesion_detail import detail_url

        html = logged_client.get(detail_url(patient_a, lesion_a)).content.decode()
        protegida = reverse('clinical:lesion-attachment', args=[attachment.public_id])

        assert protegida in html
        # Ni la URL del almacén ni la clave del objeto se escriben en la página.
        assert '/test-clinical-media/' not in html
        assert attachment.file.name not in html

    def test_la_evolucion_tampoco_escribe_urls_del_bucket(
        self, logged_client, patient_a, lesion_a, attachment
    ):
        html = logged_client.get(evolution_url(patient_a, lesion_a)).content.decode()

        assert reverse('clinical:lesion-attachment', args=[attachment.public_id]) in html
        assert '/test-clinical-media/' not in html

    def test_pedir_la_foto_firma_redirige_y_deja_su_acceso(
        self, logged_client, patient_a, attachment
    ):
        """Cada visualización de una foto clínica es un acceso a datos."""
        response = logged_client.get(
            reverse('clinical:lesion-attachment', args=[attachment.public_id])
        )

        assert response.status_code == 302
        assert response['Cache-Control'] == 'private, no-store, max-age=0'
        assert AccessLog.objects.filter(
            patient=patient_a, action=AccessLog.Action.DOWNLOAD_ATTACHMENT
        ).exists()


# ---------------------------------------------------------------------------
# Evolución
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestEvolucion:
    @pytest.fixture
    def serie(self, db, lesion_a, visit_a):
        """Tres observaciones, de la más antigua a la más reciente, con la úlcera cerrando."""
        return [
            make_observation(lesion_a, visit_a, days_ago=30, length_mm='14.0', width_mm='9.5'),
            make_observation(lesion_a, visit_a, days_ago=14, length_mm='11.5', width_mm='7.0'),
            make_observation(lesion_a, visit_a, days_ago=2, length_mm='8.0', width_mm='5.5'),
        ]

    def test_la_serie_se_lee_de_la_primera_a_la_ultima(
        self, logged_client, patient_a, lesion_a, serie
    ):
        """El orden del modelo es el contrario: aquí se pide explícitamente."""
        response = logged_client.get(evolution_url(patient_a, lesion_a))

        assert [o.pk for o in response.context['observations']] == [o.pk for o in serie]

    def test_resume_la_variacion_de_cada_medida(
        self, logged_client, patient_a, lesion_a, serie
    ):
        trend = {row['label']: row for row in
                 logged_client.get(evolution_url(patient_a, lesion_a)).context['measurement_trend']}

        assert str(trend['Largo']['first']) == '14.0'
        assert str(trend['Largo']['last']) == '8.0'
        assert float(trend['Largo']['delta']) == -6.0
        # La profundidad no se midió nunca: no aparece.
        assert 'Profundidad' not in trend

    def test_una_medida_tomada_una_sola_vez_no_es_una_tendencia(
        self, logged_client, patient_a, lesion_a, visit_a
    ):
        make_observation(lesion_a, visit_a, days_ago=5, length_mm='10.0')
        make_observation(lesion_a, visit_a, days_ago=1, width_mm='4.0')

        response = logged_client.get(evolution_url(patient_a, lesion_a))

        assert response.context['measurement_trend'] == []

    def test_una_lesion_sin_observaciones_lo_dice(
        self, logged_client, patient_a, lesion_a
    ):
        response = logged_client.get(evolution_url(patient_a, lesion_a))

        assert response.context['observations'] == []
        assert 'Todavía no hay evolución que enseñar' in response.content.decode()

    def test_cuenta_las_fotografias_de_la_serie(
        self, logged_client, patient_a, lesion_a, visit_a
    ):
        observation = make_observation(lesion_a, visit_a, days_ago=3)
        LesionAttachment.objects.create(observation=observation, file=photo())
        LesionAttachment.objects.create(observation=observation, file=photo('dos.jpg'))

        assert logged_client.get(evolution_url(patient_a, lesion_a)).context['photo_count'] == 2

    def test_la_lectura_deja_su_acceso(self, logged_client, patient_a, lesion_a, serie):
        logged_client.get(evolution_url(patient_a, lesion_a))

        assert AccessLog.objects.filter(
            patient=patient_a, action=AccessLog.Action.VIEW
        ).exists()

    def test_la_lesion_de_otro_paciente_es_404(
        self, logged_client, patient_a, clinic_a, professional_a
    ):
        otro = Patient.objects.create(
            clinic=clinic_a, first_name='Otra', last_name='Persona',
            email='otra@alpha.test', phone='+34600111222',
        )
        episodio = Episode.objects.create(
            history=otro.medical_history, reason='Otro proceso',
            responsible_professional=professional_a,
        )
        ajena = make_lesion(episodio)

        assert logged_client.get(evolution_url(patient_a, ajena)).status_code == 404
        assert logged_client.get(compare_url(patient_a, ajena)).status_code == 404


# ---------------------------------------------------------------------------
# Comparación
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestComparacion:
    def test_compara_dos_observaciones_en_orden_cronologico(
        self, logged_client, patient_a, lesion_a, visit_a
    ):
        """Se marquen en el orden que se marquen, la antigua va a la izquierda."""
        antigua = make_observation(lesion_a, visit_a, days_ago=20, length_mm='14.0')
        reciente = make_observation(lesion_a, visit_a, days_ago=2, length_mm='8.0')

        response = logged_client.get(
            compare_url(patient_a, lesion_a), {'a': reciente.pk, 'b': antigua.pk},
            HTTP_HX_REQUEST='true',
        )
        comparison = response.context['comparison']

        assert [o.pk for o in comparison] == [antigua.pk, reciente.pk]
        assert response.context['comparison_days'] == 18
        assert float(response.context['comparison_trend'][0]['delta']) == -6.0

    def test_con_una_sola_observacion_no_compara_nada(
        self, logged_client, patient_a, lesion_a, visit_a
    ):
        observation = make_observation(lesion_a, visit_a, days_ago=1)

        response = logged_client.get(
            compare_url(patient_a, lesion_a), {'a': observation.pk},
            HTTP_HX_REQUEST='true',
        )

        assert response.context['comparison'] == []
        assert 'Marca dos observaciones' in response.content.decode()

    def test_la_observacion_de_otra_lesion_no_se_cuela(
        self, logged_client, patient_a, episode_a, lesion_a, visit_a
    ):
        """Aunque sea del mismo paciente: se comparan visitas de UNA lesión."""
        propia = make_observation(lesion_a, visit_a, days_ago=5)
        otra_lesion = make_lesion(episode_a, anatomical_zone='heel')
        ajena = make_observation(otra_lesion, visit_a, days_ago=1)

        response = logged_client.get(
            compare_url(patient_a, lesion_a), {'a': propia.pk, 'b': ajena.pk},
            HTTP_HX_REQUEST='true',
        )

        assert response.context['comparison'] == []

    def test_unos_parametros_ilegibles_no_rompen_nada(
        self, logged_client, patient_a, lesion_a, visit_a
    ):
        make_observation(lesion_a, visit_a, days_ago=1)

        response = logged_client.get(
            compare_url(patient_a, lesion_a), {'a': 'hola', 'b': '-1'},
            HTTP_HX_REQUEST='true',
        )

        assert response.status_code == 200
        assert response.context['comparison'] == []

    def test_las_fotos_de_las_dos_columnas_van_por_la_vista_protegida(
        self, logged_client, patient_a, lesion_a, visit_a
    ):
        primera = make_observation(lesion_a, visit_a, days_ago=10)
        segunda = make_observation(lesion_a, visit_a, days_ago=1)
        adjunto = LesionAttachment.objects.create(observation=segunda, file=photo())

        html = logged_client.get(
            compare_url(patient_a, lesion_a), {'a': primera.pk, 'b': segunda.pk},
            HTTP_HX_REQUEST='true',
        ).content.decode()

        assert reverse('clinical:lesion-attachment', args=[adjunto.public_id]) in html
        assert '/test-clinical-media/' not in html
        # La visita sin foto lo dice en vez de dejar un hueco.
        assert 'Sin fotografías en esta visita' in html
