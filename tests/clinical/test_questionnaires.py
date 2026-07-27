"""Anamnesis: versionado del cuestionario y congelado literal de la respuesta.

Lo que se defiende aquí es una sola idea: **lo que el paciente contestó tiene que
poder leerse dentro de diez años tal cual se contestó**, aunque el cuestionario
haya cambiado cinco veces por el camino.
"""
import copy

import pytest
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction

from clinical.exceptions import (
    ProtectedClinicalRecord,
    TemplateVersionNotPublished,
    TemplateVersionPublished,
)
from clinical.models import (
    Question,
    QuestionnaireResponse,
    QuestionnaireTemplate,
    TemplateVersion,
)
from core.managers import ProtectedRecordError


def questions_of(version):
    return list(Question.objects.filter(version=version).order_by('order', 'id'))


@pytest.fixture
def answers_a(published_version_a):
    q1, q2, q3 = questions_of(published_version_a)
    return {
        q1.pk: True,
        q2.pk: "Metformina 850 mg",
        q3.pk: "Sanitario",
    }


@pytest.fixture
def response_a(db, published_version_a, patient_a, episode_a, professional_a, answers_a):
    return QuestionnaireResponse.record(
        version=published_version_a,
        patient=patient_a,
        episode=episode_a,
        answers=answers_a,
        source=QuestionnaireResponse.Source.PROFESSIONAL,
        created_by=professional_a,
    )


# ---------------------------------------------------------------------------
# Versionado
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestVersioning:
    def test_version_number_is_assigned_automatically(self, questionnaire_template_a):
        v1 = TemplateVersion.objects.create(template=questionnaire_template_a)
        v2 = TemplateVersion.objects.create(template=questionnaire_template_a)
        assert (v1.number, v2.number) == (1, 2)

    def test_publishing_makes_the_version_current(self, published_version_a, questionnaire_template_a):
        assert published_version_a.is_published is True
        assert published_version_a.published_at is not None
        assert questionnaire_template_a.current_version == published_version_a

    def test_publishing_an_empty_version_is_rejected(self, questionnaire_template_a):
        empty = TemplateVersion.objects.create(template=questionnaire_template_a)
        with pytest.raises(ValidationError):
            empty.publish()
        empty.refresh_from_db()
        assert empty.is_published is False

    def test_republishing_is_rejected(self, published_version_a):
        with pytest.raises(TemplateVersionPublished):
            published_version_a.publish()

    def test_draft_version_cannot_be_made_current(self, questionnaire_template_a, published_version_a):
        draft = questionnaire_template_a.new_draft_version()
        with pytest.raises(TemplateVersionNotPublished):
            draft.make_current()

    def test_new_draft_version_clones_the_current_questions(self, questionnaire_template_a, published_version_a):
        v2 = questionnaire_template_a.new_draft_version()

        original = questions_of(published_version_a)
        clones = questions_of(v2)
        assert [q.text for q in clones] == [q.text for q in original]
        assert [q.answer_type for q in clones] == [q.answer_type for q in original]
        # Clones de verdad: filas nuevas colgando de la versión nueva.
        assert {q.pk for q in clones}.isdisjoint({q.pk for q in original})
        assert v2.is_published is False

    def test_new_draft_version_can_start_empty(self, questionnaire_template_a, published_version_a):
        v2 = questionnaire_template_a.new_draft_version(copy_questions_from=False)
        assert questions_of(v2) == []


@pytest.mark.django_db
class TestOnlyOneCurrentVersion:
    def test_publishing_a_second_version_demotes_the_first(self, questionnaire_template_a, published_version_a):
        v2 = questionnaire_template_a.new_draft_version()
        v2.publish()

        published_version_a.refresh_from_db()
        assert published_version_a.is_current is False
        assert v2.is_current is True
        assert questionnaire_template_a.current_version == v2
        assert questionnaire_template_a.versions.filter(is_current=True).count() == 1

    def test_make_current_can_go_back_to_a_previous_version(self, questionnaire_template_a, published_version_a):
        v2 = questionnaire_template_a.new_draft_version()
        v2.publish()

        published_version_a.refresh_from_db()
        published_version_a.make_current()

        v2.refresh_from_db()
        assert v2.is_current is False
        assert questionnaire_template_a.current_version == published_version_a
        assert questionnaire_template_a.versions.filter(is_current=True).count() == 1

    def test_two_current_versions_are_rejected_by_the_database(self, questionnaire_template_a, published_version_a):
        """La garantía última no es `make_current()`, es el índice único."""
        v2 = questionnaire_template_a.new_draft_version()
        v2.publish(make_current=False)
        assert v2.is_current is False

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                # Se salta el ORM a propósito: es la barrera de la base de datos
                # la que se está probando.
                TemplateVersion.all_objects.filter(pk=v2.pk).update(is_current=True)

    def test_unpublishing_leaves_the_template_without_current_version(self, questionnaire_template_a, published_version_a):
        published_version_a.unpublish()

        published_version_a.refresh_from_db()
        assert published_version_a.is_published is False
        assert published_version_a.is_current is False
        assert questionnaire_template_a.current_version is None


# ---------------------------------------------------------------------------
# Congelado de la versión publicada
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestPublishedVersionIsFrozen:
    def test_question_text_cannot_be_edited(self, published_version_a):
        question = questions_of(published_version_a)[0]
        question.text = "¿Padece usted diabetes mellitus?"
        with pytest.raises(TemplateVersionPublished):
            question.save()

        question.refresh_from_db()
        assert question.text == "¿Es usted diabético?"

    def test_no_new_question_can_be_added(self, published_version_a):
        with pytest.raises(TemplateVersionPublished):
            Question.objects.create(
                version=published_version_a,
                text="¿Fuma?",
                answer_type=Question.AnswerType.BOOLEAN,
                order=4,
            )
        assert len(questions_of(published_version_a)) == 3

    def test_question_cannot_be_deleted(self, published_version_a):
        question = questions_of(published_version_a)[0]
        assert question.can_be_deleted() is False
        with pytest.raises(ProtectedRecordError):
            question.delete()

    def test_version_content_fields_are_frozen(self, published_version_a):
        published_version_a.number = 99
        with pytest.raises(TemplateVersionPublished):
            published_version_a.save()

    def test_lifecycle_fields_are_still_editable(self, published_version_a):
        """`is_current`/`is_published` son ciclo de vida, no contenido."""
        published_version_a.unpublish()
        published_version_a.refresh_from_db()
        assert published_version_a.is_published is False

    def test_draft_version_is_freely_editable(self, draft_version_a):
        question = questions_of(draft_version_a)[0]
        question.text = "¿Padece usted diabetes mellitus?"
        question.save()

        question.refresh_from_db()
        assert question.text == "¿Padece usted diabetes mellitus?"

    def test_editing_a_question_needs_the_version_unpublished(self, published_version_a):
        published_version_a.unpublish()

        question = Question.objects.get(pk=questions_of(published_version_a)[0].pk)
        question.text = "¿Padece usted diabetes mellitus?"
        question.save()

        question.refresh_from_db()
        assert question.text == "¿Padece usted diabetes mellitus?"


@pytest.mark.django_db
class TestPublishedVersionTriggers:
    """La barrera dura: SQL crudo, saltándose Django, choca con el trigger."""

    def test_raw_update_on_a_published_question_raises(self, published_version_a):
        question = questions_of(published_version_a)[0]
        with pytest.raises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute(
                        "UPDATE clinical_question SET text = 'manipulado' WHERE id = %s",
                        [question.pk],
                    )
        question.refresh_from_db()
        assert question.text == "¿Es usted diabético?"

    def test_raw_insert_into_a_published_version_raises(self, published_version_a):
        with pytest.raises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute(
                        "INSERT INTO clinical_question "
                        "(version_id, text, answer_type, \"order\", is_required, options, "
                        " created_at, updated_at) "
                        "VALUES (%s, 'colada', 'text', 9, false, '[]'::jsonb, NOW(), NOW())",
                        [published_version_a.pk],
                    )
        assert len(questions_of(published_version_a)) == 3

    def test_raw_delete_of_a_published_version_raises(self, published_version_a):
        with pytest.raises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute(
                        "DELETE FROM clinical_template_version WHERE id = %s",
                        [published_version_a.pk],
                    )
        assert TemplateVersion.all_objects.filter(pk=published_version_a.pk).exists()


# ---------------------------------------------------------------------------
# Snapshot literal
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSnapshot:
    def test_snapshot_keeps_the_literal_question_text(self, response_a):
        assert [entry['text'] for entry in response_a.snapshot] == [
            "¿Es usted diabético?",
            "¿Qué medicación toma actualmente?",
            "¿Qué calzado usa habitualmente?",
        ]

    def test_snapshot_keeps_types_options_and_answers(self, response_a, answers_a):
        first, second, third = response_a.snapshot

        assert first['answer_type'] == Question.AnswerType.BOOLEAN
        assert first['required'] is True
        assert first['answer'] is True

        assert second['answer'] == "Metformina 850 mg"

        assert third['options'] == ["Deportivo", "De vestir", "Sanitario"]
        assert third['answer'] == "Sanitario"

        assert {entry['question_id'] for entry in response_a.snapshot} == set(answers_a)

    def test_unanswered_questions_are_kept_with_a_null_answer(
        self, published_version_a, patient_a, episode_a
    ):
        q1 = questions_of(published_version_a)[0]
        response = QuestionnaireResponse.record(
            version=published_version_a,
            patient=patient_a,
            episode=episode_a,
            answers={q1.pk: False},
        )

        assert len(response.snapshot) == 3
        assert response.snapshot[0]['answer'] is False  # «no» es una respuesta
        assert response.snapshot[1]['answer'] is None
        assert response.is_complete is True  # solo la primera era obligatoria

    def test_answers_for_another_questionnaire_are_rejected(
        self, published_version_a, patient_a, episode_a
    ):
        with pytest.raises(ValidationError):
            QuestionnaireResponse.record(
                version=published_version_a,
                patient=patient_a,
                episode=episode_a,
                answers={999999: "de otro cuestionario"},
            )

    def test_answer_for_reads_from_the_snapshot(self, response_a, answers_a):
        q1 = questions_of(response_a.version)[0]
        assert response_a.answer_for(q1.pk) is True
        assert response_a.answer_for(-1) is None

    def test_response_on_a_draft_version_is_rejected(self, draft_version_a, patient_a, episode_a):
        with pytest.raises(TemplateVersionNotPublished):
            QuestionnaireResponse.record(
                version=draft_version_a, patient=patient_a, episode=episode_a,
            )


@pytest.mark.django_db
class TestSnapshotSurvivesVersionChanges:
    """El requisito de fondo: tocar la versión NO puede alterar lo ya guardado."""

    def test_editing_a_question_does_not_change_saved_responses(self, response_a, published_version_a):
        original = copy.deepcopy(response_a.snapshot)

        published_version_a.unpublish()
        question = Question.objects.get(pk=questions_of(published_version_a)[0].pk)
        question.text = "¿Padece usted diabetes mellitus tipo II?"
        question.save()

        response_a.refresh_from_db()
        assert response_a.snapshot == original
        assert response_a.snapshot[0]['text'] == "¿Es usted diabético?"

    def test_deleting_a_question_does_not_change_saved_responses(self, response_a, published_version_a):
        original = copy.deepcopy(response_a.snapshot)

        published_version_a.unpublish()
        question = Question.objects.get(pk=questions_of(published_version_a)[1].pk)
        question.delete()

        response_a.refresh_from_db()
        assert response_a.snapshot == original
        assert len(response_a.snapshot) == 3

    def test_unpublishing_the_version_does_not_change_saved_responses(self, response_a, published_version_a):
        original = copy.deepcopy(response_a.snapshot)

        published_version_a.unpublish()

        response_a.refresh_from_db()
        assert response_a.snapshot == original

    def test_publishing_a_new_version_does_not_touch_historical_responses(
        self, response_a, questionnaire_template_a, published_version_a
    ):
        original = copy.deepcopy(response_a.snapshot)

        v2 = questionnaire_template_a.new_draft_version()
        first = questions_of(v2)[0]
        first.text = "¿Padece usted diabetes mellitus tipo II?"
        first.save()
        Question.objects.create(
            version=v2, text="¿Fuma?", answer_type=Question.AnswerType.BOOLEAN, order=4,
        )
        v2.publish()

        response_a.refresh_from_db()
        assert response_a.snapshot == original
        assert len(response_a.snapshot) == 3
        assert response_a.version_id == published_version_a.pk

    def test_soft_deleting_the_version_keeps_the_response_readable(self, response_a, published_version_a):
        original = copy.deepcopy(response_a.snapshot)

        published_version_a.unpublish()
        published_version_a.delete()

        response_a.refresh_from_db()
        assert response_a.snapshot == original
        assert response_a.snapshot[0]['text'] == "¿Es usted diabético?"


# ---------------------------------------------------------------------------
# Canales de entrada
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestResponseSources:
    def test_whatsapp_response_needs_no_professional(
        self, published_version_a, patient_a, episode_a, answers_a
    ):
        response = QuestionnaireResponse.record(
            version=published_version_a,
            patient=patient_a,
            episode=episode_a,
            answers=answers_a,
            source=QuestionnaireResponse.Source.PATIENT_WHATSAPP,
        )

        assert response.pk is not None
        assert response.created_by is None
        assert response.source == QuestionnaireResponse.Source.PATIENT_WHATSAPP
        assert response.snapshot[0]['answer'] is True

        stored = QuestionnaireResponse.objects.get(pk=response.pk)
        assert stored.created_by_id is None

    def test_web_response_needs_no_professional(
        self, published_version_a, patient_a, episode_a
    ):
        response = QuestionnaireResponse.record(
            version=published_version_a,
            patient=patient_a,
            episode=episode_a,
            source=QuestionnaireResponse.Source.PATIENT_WEB,
        )
        assert response.created_by is None
        assert response.is_complete is False  # la obligatoria quedó sin contestar

    def test_professional_response_records_its_author(self, response_a, professional_a):
        assert response_a.source == QuestionnaireResponse.Source.PROFESSIONAL
        assert response_a.created_by == professional_a


# ---------------------------------------------------------------------------
# Inmutabilidad de la respuesta
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestResponseImmutability:
    def test_snapshot_cannot_be_rewritten(self, response_a):
        stored = QuestionnaireResponse.objects.get(pk=response_a.pk)
        stored.snapshot[0]['answer'] = False
        with pytest.raises(ProtectedClinicalRecord):
            stored.save()

        fresh = QuestionnaireResponse.objects.get(pk=response_a.pk)
        assert fresh.snapshot[0]['answer'] is True

    def test_source_cannot_be_rewritten(self, response_a):
        stored = QuestionnaireResponse.objects.get(pk=response_a.pk)
        stored.source = QuestionnaireResponse.Source.PATIENT_WHATSAPP
        with pytest.raises(ProtectedClinicalRecord):
            stored.save()

    def test_soft_delete_is_allowed(self, response_a):
        response_a.delete()

        assert not QuestionnaireResponse.objects.filter(pk=response_a.pk).exists()
        assert QuestionnaireResponse.all_objects.get(pk=response_a.pk).deleted_at is not None

    def test_raw_update_of_the_snapshot_raises(self, response_a):
        with pytest.raises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute(
                        "UPDATE clinical_questionnaire_response "
                        "SET snapshot = '[]'::jsonb WHERE id = %s",
                        [response_a.pk],
                    )
        response_a.refresh_from_db()
        assert len(response_a.snapshot) == 3

    def test_raw_delete_raises(self, response_a):
        with pytest.raises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute(
                        "DELETE FROM clinical_questionnaire_response WHERE id = %s",
                        [response_a.pk],
                    )
        assert QuestionnaireResponse.all_objects.filter(pk=response_a.pk).exists()

    def test_a_version_with_responses_cannot_be_hard_deleted(self, response_a, published_version_a):
        """La FK es PROTECT: la respuesta señala siempre a su versión de origen."""
        with pytest.raises(Exception):
            with transaction.atomic():
                TemplateVersion.all_objects.filter(pk=published_version_a.pk).hard_delete()


# ---------------------------------------------------------------------------
# Validación
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestQuestionValidation:
    def test_choice_question_needs_options(self, draft_version_a):
        with pytest.raises(ValidationError):
            Question.objects.create(
                version=draft_version_a,
                text="¿Qué calzado prefiere?",
                answer_type=Question.AnswerType.SINGLE_CHOICE,
                order=9,
            )

    def test_non_choice_question_rejects_options(self, draft_version_a):
        with pytest.raises(ValidationError):
            Question.objects.create(
                version=draft_version_a,
                text="¿Cuántos años lleva con el dolor?",
                answer_type=Question.AnswerType.NUMBER,
                order=9,
                options=["1", "2"],
            )


@pytest.mark.django_db
class TestResponseValidation:
    def test_questionnaire_of_another_clinic_is_rejected(
        self, published_version_a, patient_b, episode_a
    ):
        response = QuestionnaireResponse(
            version=published_version_a, patient=patient_b, episode=episode_a,
        )
        with pytest.raises(ValidationError):
            response.clean()

    def test_episode_of_another_patient_is_rejected(
        self, published_version_a, patient_a, clinic_a, episode_a
    ):
        from patients.models import Patient

        other = Patient.objects.create(
            clinic=clinic_a, first_name="Otro", last_name="Paciente", phone="600000999",
        )
        response = QuestionnaireResponse(
            version=published_version_a, patient=other, episode=episode_a,
        )
        with pytest.raises(ValidationError):
            response.clean()


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAdmin:
    def test_questionnaire_pages_are_reachable(self, admin_site_client, published_version_a, response_a):
        for url in (
            '/admin/clinical/questionnairetemplate/',
            '/admin/clinical/templateversion/',
            '/admin/clinical/question/',
            '/admin/clinical/questionnaireresponse/',
            f'/admin/clinical/templateversion/{published_version_a.pk}/change/',
            f'/admin/clinical/questionnaireresponse/{response_a.pk}/change/',
        ):
            assert admin_site_client.get(url).status_code == 200, url

    def test_published_version_is_read_only_and_not_deletable(self, admin_site_client, published_version_a):
        from django.contrib.admin.sites import site

        model_admin = site._registry[TemplateVersion]
        readonly = model_admin.get_readonly_fields(request=None, obj=published_version_a)

        assert {'template', 'number', 'published_at'} <= set(readonly)
        assert model_admin.has_delete_permission(request=None, obj=published_version_a) is False

    def test_adding_a_question_to_a_published_version_is_a_validation_error(
        self, admin_site_client, published_version_a
    ):
        response = admin_site_client.post(
            '/admin/clinical/question/add/',
            {
                'version': published_version_a.pk,
                'text': '¿Fuma?',
                'answer_type': Question.AnswerType.BOOLEAN,
                'order': 4,
                'options': '[]',
            },
        )
        # Sin redirección: el formulario vuelve con el error, y nada se ha creado.
        assert response.status_code == 200
        assert len(questions_of(published_version_a)) == 3

    def test_responses_cannot_be_created_or_edited_from_the_admin(self, response_a):
        from django.contrib.admin.sites import site

        model_admin = site._registry[QuestionnaireResponse]
        assert model_admin.has_add_permission(request=None) is False
        assert model_admin.has_change_permission(request=None, obj=response_a) is False
        assert model_admin.has_delete_permission(request=None, obj=response_a) is False

    def test_reading_a_response_leaves_an_access_log(self, admin_site_client, response_a):
        from audit.models import AccessLog

        admin_site_client.get(f'/admin/clinical/questionnaireresponse/{response_a.pk}/change/')

        log = AccessLog.objects.filter(
            action=AccessLog.Action.VIEW, object_id=str(response_a.pk)
        ).first()
        assert log is not None
        assert log.patient_id == response_a.patient_id

    def test_publish_action_publishes_the_selected_version(self, admin_site_client, draft_version_a):
        response = admin_site_client.post(
            '/admin/clinical/templateversion/',
            {
                'action': 'publish_versions',
                '_selected_action': [str(draft_version_a.pk)],
            },
            follow=True,
        )
        assert response.status_code == 200

        draft_version_a.refresh_from_db()
        assert draft_version_a.is_published is True
        assert draft_version_a.is_current is True


@pytest.mark.django_db
class TestMultiTenancy:
    def test_template_name_is_unique_per_clinic_not_globally(self, questionnaire_template_a, clinic_b):
        twin = QuestionnaireTemplate.objects.create(
            clinic=clinic_b, name=questionnaire_template_a.name,
        )
        assert twin.pk != questionnaire_template_a.pk

    def test_version_numbering_is_independent_per_template(self, questionnaire_template_a, clinic_b):
        other = QuestionnaireTemplate.objects.create(clinic=clinic_b, name="Anamnesis general")
        TemplateVersion.objects.create(template=questionnaire_template_a)
        first_of_other = TemplateVersion.objects.create(template=other)
        assert first_of_other.number == 1
