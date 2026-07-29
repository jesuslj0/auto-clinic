"""Los tres paneles clínicos de la ficha: anamnesis, procedimientos y consentimientos.

Lo que se defiende aquí no es que la plantilla pinte algo, sino que pinte **lo
que congeló el modelo**:

1. La anamnesis se lee del `snapshot`, con su número de versión, y los booleanos
   se leen como «Sí»/«No» (nunca `True`/`False`).
2. Un procedimiento enseña su precio congelado aunque el catálogo haya subido
   después. Es el punto entero de `PerformedProcedure`.
3. Un consentimiento enseña el texto que se firmó, no el vigente, y la firma solo
   por la vista protegida.

Y, transversal a los tres: los datos son del paciente pedido, y un paciente de
otra clínica sigue siendo un 404.
"""
from decimal import Decimal
from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from clinical.models import (
    ConsentTemplate,
    ConsentVersion,
    Episode,
    PerformedProcedure,
    QuestionnaireResponse,
    SignedConsent,
    Visit,
)
from patients.models import Patient


CONSENT_V1_TEXT = (
    'Se me ha informado de que la cirugia ungueal se realiza con anestesia '
    'local y de sus posibles complicaciones. Consiento en que se me practique.'
)
CONSENT_V2_TEXT = CONSENT_V1_TEXT + ' Autorizo tambien el registro fotografico.'


def signature_file(name='firma.png'):
    """Una imagen de verdad: la firma se valida por contenido, no por extensión."""
    buffer = BytesIO()
    Image.new('RGB', (120, 40), (255, 255, 255)).save(buffer, format='PNG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/png')


@pytest.fixture
def other_patient(db, clinic_a, professional_a):
    """Otro paciente de la MISMA clínica, con su propia visita.

    Es lo que hace falta para probar que los paneles filtran por paciente y no
    solo por clínica: un 404 de otra clínica no demuestra eso.
    """
    patient = Patient.objects.create(
        clinic=clinic_a, first_name='Otra', last_name='Persona',
        email='otra@alpha.test', phone='+34600111222',
    )
    episode = Episode.objects.create(
        history=patient.medical_history,
        reason='Revisión de otra persona',
        responsible_professional=professional_a,
    )
    patient.test_visit = Visit.objects.create(episode=episode, professional=professional_a)
    patient.test_episode = episode
    return patient


# ---------------------------------------------------------------------------
# Anamnesis
# ---------------------------------------------------------------------------

@pytest.fixture
def response_a(db, published_version_a, patient_a, episode_a):
    """Una anamnesis de la v1: diabético que sí, medicación puesta y calzado sin contestar."""
    questions = list(published_version_a.questions.order_by('order'))
    return QuestionnaireResponse.record(
        version=published_version_a,
        patient=patient_a,
        episode=episode_a,
        answers={questions[0].pk: True, questions[1].pk: 'Metformina 850 mg'},
    )


@pytest.mark.django_db
class TestAnamnesisPanel:
    def url(self, patient):
        return reverse('patients:tab-anamnesis', kwargs={'id': patient.pk})

    def test_shows_the_snapshot_of_the_response(self, client, admin_user, patient_a, response_a):
        """Se pinta lo que se preguntó y lo que se contestó, con su versión."""
        client.force_login(admin_user)
        html = client.get(self.url(patient_a)).content.decode()

        assert 'Anamnesis podológica' in html          # plantilla
        assert 'v1' in html                            # número de versión
        assert 'Profesional' in html                   # origen (`source`)
        assert '¿Es usted diabético?' in html          # texto literal del snapshot
        assert 'Metformina 850 mg' in html             # respuesta de texto
        assert 'Sin responder' in html                 # pregunta no contestada

    def test_booleans_read_as_yes_or_no(self, client, admin_user, patient_a, response_a):
        """«Sí»/«No», nunca `True`/`False`: esto lo lee una persona."""
        client.force_login(admin_user)
        html = client.get(self.url(patient_a)).content.decode()

        assert 'True' not in html
        assert 'False' not in html
        assert 'Sí' in html

    def test_the_version_number_makes_versioning_visible(
        self, client, admin_user, patient_a, episode_a, published_version_a, response_a
    ):
        """Dos respuestas sobre versiones distintas se distinguen en la ficha."""
        v2 = published_version_a.template.new_draft_version()
        v2.publish()
        QuestionnaireResponse.record(
            version=v2, patient=patient_a, episode=episode_a,
            source=QuestionnaireResponse.Source.PATIENT_WEB, answers={},
        )

        client.force_login(admin_user)
        html = client.get(self.url(patient_a)).content.decode()

        assert 'v1' in html and 'v2' in html
        assert 'Paciente (web)' in html
        assert len(html.split('anamnesis-detalle-')) - 1 == 2 * 2  # id + aria-controls

    def test_does_not_leak_another_patients_anamnesis(
        self, client, admin_user, patient_a, other_patient, published_version_a
    ):
        QuestionnaireResponse.record(
            version=published_version_a, patient=other_patient,
            episode=other_patient.test_episode,
            answers={published_version_a.questions.first().pk: True},
        )

        client.force_login(admin_user)
        assert client.get(self.url(patient_a)).context['responses'] == []

    def test_empty_state(self, client, admin_user, patient_a):
        client.force_login(admin_user)
        html = client.get(self.url(patient_a)).content.decode()
        assert 'Aún no hay anamnesis registrada' in html

    def test_another_clinic_is_a_404(self, client, admin_user, patient_b):
        client.force_login(admin_user)
        assert client.get(self.url(patient_b)).status_code == 404


# ---------------------------------------------------------------------------
# Procedimientos
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestProceduresPanel:
    def url(self, patient):
        return reverse('patients:tab-procedures', kwargs={'id': patient.pk})

    def test_shows_the_frozen_price_and_not_the_catalogue_one(
        self, client, admin_user, patient_a, visit_a, service_a
    ):
        """Sube la tarifa después: el procedimiento sigue costando lo que costó."""
        procedure = PerformedProcedure.objects.create(visit=visit_a, service=service_a)
        assert procedure.frozen_price == Decimal('50.00')

        service_a.name = 'Consulta (tarifa nueva)'
        service_a.price = Decimal('100.00')
        service_a.save(update_fields=['name', 'price'])

        client.force_login(admin_user)
        html = client.get(self.url(patient_a)).content.decode()

        assert '50,00' in html
        assert '100,00' not in html
        assert 'Consultation' in html                 # nombre congelado
        assert 'Consulta (tarifa nueva)' not in html  # nombre actual del catálogo

    def test_total_is_the_sum_of_the_frozen_prices(
        self, client, admin_user, patient_a, visit_a, service_a
    ):
        PerformedProcedure.objects.create(visit=visit_a, service=service_a)
        PerformedProcedure.objects.create(
            visit=visit_a, service=service_a, frozen_price=Decimal('45.00'),
        )
        service_a.price = Decimal('100.00')
        service_a.save(update_fields=['price'])

        client.force_login(admin_user)
        response = client.get(self.url(patient_a))

        assert response.context['procedures_total'] == Decimal('95.00')
        assert '95,00' in response.content.decode()

    def test_shows_zone_and_laterality(
        self, client, admin_user, patient_a, visit_a, service_a
    ):
        from clinical.models import Lesion

        PerformedProcedure.objects.create(
            visit=visit_a, service=service_a,
            affected_zone=Lesion.AnatomicalZone.HEEL,
            laterality=Lesion.Laterality.RIGHT,
        )
        client.force_login(admin_user)
        html = client.get(self.url(patient_a)).content.decode()

        assert 'Talón' in html
        assert 'Pie derecho' in html

    def test_does_not_leak_another_patients_procedures(
        self, client, admin_user, patient_a, other_patient, service_a
    ):
        PerformedProcedure.objects.create(
            visit=other_patient.test_visit, service=service_a,
        )
        client.force_login(admin_user)
        response = client.get(self.url(patient_a))

        assert response.context['procedures'] == []
        assert response.context['procedures_total'] == Decimal('0.00')
        assert 'Aún no hay procedimientos registrados' in response.content.decode()

    def test_another_clinic_is_a_404(self, client, admin_user, patient_b):
        client.force_login(admin_user)
        assert client.get(self.url(patient_b)).status_code == 404


# ---------------------------------------------------------------------------
# Consentimientos
# ---------------------------------------------------------------------------

@pytest.fixture
def consent_version_a(db, clinic_a):
    template = ConsentTemplate.objects.create(
        clinic=clinic_a, name='Consentimiento de cirugía ungueal',
    )
    version = ConsentVersion.objects.create(template=template, text=CONSENT_V1_TEXT)
    version.publish()
    return version


@pytest.fixture
def signed_consent_a(db, consent_version_a, patient_a, episode_a):
    return SignedConsent.sign(
        version=consent_version_a, patient=patient_a, episode=episode_a,
        signature_image=signature_file(),
    )


@pytest.mark.django_db
class TestConsentsPanel:
    def url(self, patient):
        return reverse('patients:tab-consents', kwargs={'id': patient.pk})

    def test_shows_the_signed_text_and_the_protected_signature_link(
        self, client, admin_user, patient_a, signed_consent_a
    ):
        client.force_login(admin_user)
        html = client.get(self.url(patient_a)).content.decode()

        assert 'Consentimiento de cirugía ungueal' in html
        assert 'v1' in html
        assert CONSENT_V1_TEXT in html
        # La firma, solo por la vista protegida: ni una URL del bucket.
        assert reverse(
            'clinical:consent-signature',
            kwargs={'public_id': signed_consent_a.public_id},
        ) in html
        assert 'X-Amz-Signature' not in html
        assert '/test-clinical-media/' not in html

    def test_shows_the_text_signed_not_the_current_one(
        self, client, admin_user, patient_a, consent_version_a, signed_consent_a
    ):
        """Se publica una versión nueva: lo firmado no se mueve."""
        v2 = consent_version_a.template.new_draft_version(text=CONSENT_V2_TEXT)
        v2.publish()

        client.force_login(admin_user)
        html = client.get(self.url(patient_a)).content.decode()

        assert CONSENT_V1_TEXT in html
        assert 'Autorizo tambien el registro fotografico' not in html

    def test_does_not_leak_another_patients_consent(
        self, client, admin_user, patient_a, other_patient, consent_version_a
    ):
        SignedConsent.sign(
            version=consent_version_a, patient=other_patient,
            episode=other_patient.test_episode, signature_image=signature_file(),
        )
        client.force_login(admin_user)
        response = client.get(self.url(patient_a))

        assert response.context['consents'] == []
        assert 'Aún no hay consentimientos firmados' in response.content.decode()

    def test_another_clinic_is_a_404(self, client, admin_user, patient_b):
        client.force_login(admin_user)
        assert client.get(self.url(patient_b)).status_code == 404
