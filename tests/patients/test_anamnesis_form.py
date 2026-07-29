"""Alta de una anamnesis desde el panel (`patients:anamnesis-create`).

Lo que se defiende aquí no es que la página pinte un formulario, sino las cuatro
cosas que la hacen correcta y que fallarían en silencio:

1. Se contesta **la versión vigente**, nunca una antigua ni un borrador.
2. El alta va por `QuestionnaireResponse.record()`, así que **congela el
   snapshot y dispara el motor de alertas**: «sí» a una pregunta con código
   crítico levanta su aviso; «no», no.
3. Una pregunta booleana **obligatoria acepta «No»** — la respuesta clínica más
   frecuente, y la que un `BooleanField(required=True)` haría imposible.
4. El aislamiento: paciente de otra clínica es 404, y el episodio de otro
   paciente no se cuela ni mandándolo por POST a mano.
"""
import pytest
from django.urls import reverse

from clinical.models import (
    ClinicalAlert,
    Episode,
    Question,
    QuestionnaireResponse,
    QuestionnaireTemplate,
    TemplateVersion,
)
from patients.models import Patient


def url_for(patient):
    return reverse('patients:anamnesis-create', kwargs={'id': patient.pk})


def field_name(question):
    return f'q_{question.pk}'


def add_question(version, **kwargs):
    kwargs.setdefault('answer_type', Question.AnswerType.BOOLEAN)
    return Question.objects.create(version=version, **kwargs)


@pytest.fixture
def coded_template(db, clinic_a):
    """Cuestionario propio de estos tests, con preguntas CODIFICADAS.

    El de `conftest` no tiene `code` en ninguna pregunta, así que no alimenta al
    motor de alertas: para probar la derivación hace falta uno que sí.
    """
    return QuestionnaireTemplate.objects.create(
        clinic=clinic_a, name='Anamnesis podológica codificada', specialty='podología',
    )


@pytest.fixture
def coded_version(db, coded_template):
    """v1 publicada y vigente, con un campo de cada tipo."""
    version = TemplateVersion.objects.create(template=coded_template)
    add_question(version, code='has_diabetes', text='¿Es usted diabético?', order=1, is_required=True)
    add_question(version, code='takes_anticoagulants', text='¿Toma anticoagulantes?', order=2)
    add_question(
        version, text='¿Qué medicación toma actualmente?',
        answer_type=Question.AnswerType.TEXT, order=3,
    )
    add_question(
        version, text='¿Qué calzado usa habitualmente?',
        answer_type=Question.AnswerType.SINGLE_CHOICE, order=4,
        options=['Deportivo', 'De vestir'],
    )
    add_question(
        version, text='¿Qué molestias nota?',
        answer_type=Question.AnswerType.MULTIPLE_CHOICE, order=5,
        options=['Dolor', 'Picor'],
    )
    add_question(
        version, text='¿Cuántas cirugías previas ha tenido?',
        answer_type=Question.AnswerType.NUMBER, order=6,
    )
    version.publish()
    return version


@pytest.fixture
def questions(coded_version):
    """Las preguntas indexadas por código, y las sueltas por su orden."""
    by_order = {q.order: q for q in coded_version.questions.all()}
    return {
        'diabetes': by_order[1],
        'anticoagulantes': by_order[2],
        'medicacion': by_order[3],
        'calzado': by_order[4],
        'molestias': by_order[5],
        'cirugias': by_order[6],
    }


@pytest.fixture
def payload(coded_version, questions, episode_a):
    """POST mínimo válido: episodio existente y la obligatoria contestada."""
    return {
        'cuestionario': coded_version.template_id,
        'episode': episode_a.pk,
        field_name(questions['diabetes']): 'false',
    }


@pytest.fixture
def logged_client(client, admin_user):
    client.force_login(admin_user)
    return client


# ---------------------------------------------------------------------------
# Siempre la versión vigente
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestVigentVersion:
    def test_builds_the_form_on_the_current_version(
        self, logged_client, patient_a, coded_version, coded_template
    ):
        """Publicar una v2 cambia el formulario: se contesta la vigente, no la v1."""
        v2 = coded_template.new_draft_version()
        Question.objects.create(
            version=v2, text='¿Es alérgico al látex?', code='allergy_latex',
            answer_type=Question.AnswerType.BOOLEAN, order=7,
        )
        v2.publish()

        response = logged_client.get(
            url_for(patient_a), {'cuestionario': coded_template.pk}
        )
        html = response.content.decode()

        assert response.status_code == 200
        assert response.context['version'] == v2
        assert response.context['version'].number == 2
        assert '¿Es alérgico al látex?' in html   # solo existe en la vigente
        assert 'v2' in html                       # la cabecera dice qué se contesta

    def test_a_draft_only_template_is_a_clear_state_not_a_crash(
        self, logged_client, patient_a, questionnaire_template_a, draft_version_a
    ):
        """Sin versión publicada no hay formulario, pero tampoco un 500."""
        response = logged_client.get(
            url_for(patient_a), {'cuestionario': questionnaire_template_a.pk}
        )

        assert response.status_code == 200
        assert response.context['state'] == 'sin_version'
        assert response.context['form'] is None
        assert 'no tiene versión vigente' in response.content.decode()

    def test_several_active_templates_ask_first(
        self, logged_client, patient_a, coded_version, published_version_a
    ):
        """Dos cuestionarios activos: se elige antes de contestar nada."""
        response = logged_client.get(url_for(patient_a))

        assert response.context['state'] == 'elegir_cuestionario'
        assert response.context['form'] is None
        assert len(response.context['questionnaire_templates']) == 2

    def test_a_single_active_template_is_used_without_asking(
        self, logged_client, patient_a, coded_version
    ):
        response = logged_client.get(url_for(patient_a))

        assert response.context['state'] == 'formulario'
        assert response.context['version'] == coded_version

    def test_templates_of_another_clinic_are_never_offered(
        self, logged_client, patient_a, coded_version, clinic_b
    ):
        QuestionnaireTemplate.objects.create(clinic=clinic_b, name='Anamnesis ajena')

        response = logged_client.get(url_for(patient_a))

        nombres = [t.name for t in response.context['questionnaire_templates']]
        assert nombres == ['Anamnesis podológica codificada']


# ---------------------------------------------------------------------------
# Derivación de alertas
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAlertDerivation:
    def test_answering_yes_raises_the_derived_alert(
        self, logged_client, patient_a, questions, payload
    ):
        payload[field_name(questions['diabetes'])] = 'true'

        response = logged_client.post(url_for(patient_a), payload)

        assert response.status_code == 302
        alerta = ClinicalAlert.objects.get(
            patient=patient_a, alert_type=ClinicalAlert.AlertType.DIABETES
        )
        assert alerta.is_active
        assert alerta.source == ClinicalAlert.Source.DERIVED
        assert alerta.severity == ClinicalAlert.Severity.CRITICAL
        # La procedencia queda atada a la respuesta que la levantó.
        assert alerta.source_response == QuestionnaireResponse.objects.get(patient=patient_a)

    def test_answering_no_raises_nothing(self, logged_client, patient_a, payload):
        """«No» es una respuesta, y la respuesta es que no hay alerta."""
        response = logged_client.post(url_for(patient_a), payload)

        assert response.status_code == 302
        assert QuestionnaireResponse.objects.filter(patient=patient_a).count() == 1
        assert not ClinicalAlert.objects.filter(patient=patient_a).exists()

    def test_the_message_says_how_many_alerts_were_raised(
        self, logged_client, patient_a, questions, payload
    ):
        payload[field_name(questions['diabetes'])] = 'true'
        payload[field_name(questions['anticoagulantes'])] = 'true'

        response = logged_client.post(url_for(patient_a), payload, follow=True)
        textos = [str(m) for m in response.context['messages']]

        assert ClinicalAlert.objects.filter(patient=patient_a).count() == 2
        assert any('2 alertas' in texto for texto in textos)


# ---------------------------------------------------------------------------
# Tipos de campo
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestFieldTypes:
    def test_a_required_boolean_accepts_no(
        self, logged_client, patient_a, questions, payload
    ):
        """La trampa: `BooleanField(required=True)` rechazaría este envío.

        Y el «no» tiene que llegar al snapshot como un booleano de verdad, que es
        lo que sabe leer el motor de reglas.
        """
        assert questions['diabetes'].is_required

        response = logged_client.post(url_for(patient_a), payload)

        assert response.status_code == 302
        respuesta = QuestionnaireResponse.objects.get(patient=patient_a)
        assert respuesta.answer_for(questions['diabetes'].pk) is False

    def test_a_required_boolean_left_blank_is_an_error(
        self, logged_client, patient_a, questions, payload
    ):
        del payload[field_name(questions['diabetes'])]

        response = logged_client.post(url_for(patient_a), payload)

        assert response.status_code == 200
        assert response.context['form'].errors
        assert not QuestionnaireResponse.objects.filter(patient=patient_a).exists()

    def test_every_answer_type_reaches_the_snapshot_with_its_own_type(
        self, logged_client, patient_a, questions, payload
    ):
        payload.update({
            field_name(questions['medicacion']): 'Metformina 850 mg',
            field_name(questions['calzado']): 'Deportivo',
            field_name(questions['molestias']): ['Dolor', 'Picor'],
            field_name(questions['cirugias']): '2',
        })

        logged_client.post(url_for(patient_a), payload)
        respuesta = QuestionnaireResponse.objects.get(patient=patient_a)

        assert respuesta.answer_for(questions['medicacion'].pk) == 'Metformina 850 mg'
        assert respuesta.answer_for(questions['calzado'].pk) == 'Deportivo'
        assert respuesta.answer_for(questions['molestias'].pk) == ['Dolor', 'Picor']
        # Entero y no `Decimal`: el snapshot es JSON y un Decimal no se serializa.
        assert respuesta.answer_for(questions['cirugias'].pk) == 2
        assert isinstance(respuesta.answer_for(questions['cirugias'].pk), int)

    def test_the_snapshot_keeps_the_literal_question_text(
        self, logged_client, patient_a, payload
    ):
        logged_client.post(url_for(patient_a), payload)
        respuesta = QuestionnaireResponse.objects.get(patient=patient_a)

        textos = [entry['text'] for entry in respuesta.snapshot]
        assert '¿Es usted diabético?' in textos
        assert '¿Qué medicación toma actualmente?' in textos
        # Y el código, que es de lo que vive el motor de alertas.
        assert 'has_diabetes' in [entry['code'] for entry in respuesta.snapshot]

    def test_the_options_come_from_the_question(
        self, logged_client, patient_a, questions, payload
    ):
        """Una opción que no está en `options` no se acepta."""
        payload[field_name(questions['calzado'])] = 'Chanclas'

        response = logged_client.post(url_for(patient_a), payload)

        assert response.status_code == 200
        assert field_name(questions['calzado']) in response.context['form'].errors


# ---------------------------------------------------------------------------
# Episodio
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestEpisode:
    def test_the_open_episode_is_preselected(
        self, logged_client, patient_a, coded_version, episode_a
    ):
        response = logged_client.get(url_for(patient_a))
        form = response.context['form']

        assert form.has_open_episodes
        assert form['episode'].value() == episode_a.pk

    def test_a_new_episode_can_be_opened_from_the_form(
        self, logged_client, patient_a, questions, coded_version, episode_a, admin_user
    ):
        datos = {
            'cuestionario': coded_version.template_id,
            'episode': '',
            'episode_reason': 'Dolor en el antepié derecho',
            field_name(questions['diabetes']): 'false',
        }

        response = logged_client.post(url_for(patient_a), datos)

        assert response.status_code == 302
        nuevo = Episode.objects.get(reason='Dolor en el antepié derecho')
        assert nuevo.history.patient_id == patient_a.pk
        assert nuevo.status == Episode.Status.OPEN
        # El profesional que registra queda como responsable del episodio.
        assert nuevo.responsible_professional == admin_user.professional_profile
        assert QuestionnaireResponse.objects.get(patient=patient_a).episode == nuevo

    def test_a_patient_without_open_episodes_is_not_a_dead_end(
        self, logged_client, clinic_a, coded_version, questions
    ):
        """El caso del paciente nuevo: se abre episodio y se registra igual."""
        nuevo_paciente = Patient.objects.create(
            clinic=clinic_a, first_name='Nueva', last_name='Paciente',
            email='nueva@alpha.test', phone='+34600333444',
        )

        pagina = logged_client.get(url_for(nuevo_paciente))
        assert pagina.context['form'].has_open_episodes is False

        response = logged_client.post(url_for(nuevo_paciente), {
            'cuestionario': coded_version.template_id,
            'episode_reason': 'Primera visita',
            field_name(questions['diabetes']): 'false',
        })

        assert response.status_code == 302
        respuesta = QuestionnaireResponse.objects.get(patient=nuevo_paciente)
        assert respuesta.episode.reason == 'Primera visita'

    def test_opening_an_episode_without_reason_is_an_error(
        self, logged_client, patient_a, coded_version, questions, episode_a
    ):
        response = logged_client.post(url_for(patient_a), {
            'cuestionario': coded_version.template_id,
            'episode': '',
            'episode_reason': '   ',
            field_name(questions['diabetes']): 'false',
        })

        assert response.status_code == 200
        assert 'episode_reason' in response.context['form'].errors
        # Ni episodio huérfano ni respuesta: la transacción es atómica.
        assert Episode.objects.filter(history__patient=patient_a).count() == 1
        assert not QuestionnaireResponse.objects.filter(patient=patient_a).exists()

    def test_closed_episodes_are_not_offered(
        self, logged_client, patient_a, coded_version, episode_a
    ):
        episode_a.close()

        form = logged_client.get(url_for(patient_a)).context['form']

        assert form.has_open_episodes is False


# ---------------------------------------------------------------------------
# Aislamiento
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestIsolation:
    def test_a_patient_of_another_clinic_is_a_404(
        self, logged_client, patient_b, coded_version
    ):
        assert logged_client.get(url_for(patient_b)).status_code == 404

    def test_posting_for_a_patient_of_another_clinic_is_a_404(
        self, logged_client, patient_b, coded_version, questions
    ):
        response = logged_client.post(url_for(patient_b), {
            'cuestionario': coded_version.template_id,
            'episode_reason': 'Colado',
            field_name(questions['diabetes']): 'true',
        })

        assert response.status_code == 404
        assert not QuestionnaireResponse.objects.filter(patient=patient_b).exists()

    def test_the_episode_of_another_patient_cannot_be_smuggled_in_by_post(
        self, logged_client, clinic_a, patient_a, coded_version, questions, professional_a
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

        response = logged_client.post(url_for(patient_a), {
            'cuestionario': coded_version.template_id,
            'episode': episodio_ajeno.pk,
            field_name(questions['diabetes']): 'true',
        })

        assert response.status_code == 200
        assert 'episode' in response.context['form'].errors
        assert not QuestionnaireResponse.objects.exists()
        assert not ClinicalAlert.objects.exists()

    def test_the_questionnaire_of_another_clinic_cannot_be_answered(
        self, logged_client, patient_a, coded_version, clinic_b
    ):
        ajeno = QuestionnaireTemplate.objects.create(clinic=clinic_b, name='Anamnesis ajena')
        version_ajena = TemplateVersion.objects.create(template=ajeno)
        add_question(version_ajena, text='¿Pregunta de otra clínica?', order=1)
        version_ajena.publish()

        response = logged_client.get(url_for(patient_a), {'cuestionario': ajeno.pk})

        # Pedirlo por query string no lo resuelve: no está entre los de la
        # clínica del paciente, así que la página se queda en «elige uno».
        assert response.context['state'] == 'elegir_cuestionario'
        assert response.context['questionnaire_template'] is None
        assert ajeno not in response.context['questionnaire_templates']

    def test_posting_a_questionnaire_of_another_clinic_records_nothing(
        self, logged_client, patient_a, coded_version, clinic_b, questions, episode_a
    ):
        ajeno = QuestionnaireTemplate.objects.create(clinic=clinic_b, name='Anamnesis ajena')
        version_ajena = TemplateVersion.objects.create(template=ajeno)
        add_question(version_ajena, text='¿Pregunta de otra clínica?', order=1)
        version_ajena.publish()

        response = logged_client.post(url_for(patient_a), {
            'cuestionario': ajeno.pk,
            'episode': episode_a.pk,
            field_name(questions['diabetes']): 'true',
        })

        assert response.status_code == 302
        assert not QuestionnaireResponse.objects.exists()
        assert not ClinicalAlert.objects.exists()


# ---------------------------------------------------------------------------
# Auditoría
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_the_write_is_audited_and_the_read_is_logged(
    logged_client, patient_a, payload
):
    """La escritura la capta `ChangeLog` por señales; la lectura, `AccessLogMixin`."""
    from audit.models import AccessLog, ChangeLog

    logged_client.get(url_for(patient_a))
    assert AccessLog.objects.filter(patient=patient_a).exists()

    logged_client.post(url_for(patient_a), payload)
    assert ChangeLog.objects.filter(
        content_type__model='questionnaireresponse',
        action=ChangeLog.Action.CREATE,
    ).exists()
