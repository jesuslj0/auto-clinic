"""Alertas clínicas a mano desde la ficha (`patients:alert-create` y compañía).

Defiende que:

1. Se dan de alta **manuales** (nunca derivadas), firmadas por el profesional que las
   registra, sin pasar por la anamnesis.
2. **Una alerta no se borra, se desactiva**: no hay ruta de borrado, y desactivar
   conserva la fila y se puede reactivar.
3. Solo se editan las manuales, y el tipo no cambia.
4. No se duplica un aviso vigente del mismo tipo, y «Otra» ya no se ofrece (el motor de
   derivación sigue pudiendo levantarla; las ya guardadas se siguen editando).
5. El motor de derivación sigue sin tocar las manuales.
6. Aislamiento (404 entre clínicas y entre pacientes), `AccessLog` en la lectura y
   la `note` nunca en el `ChangeLog`.
"""
import pytest
from django.urls import NoReverseMatch, reverse

from audit.models import AccessLog, ChangeLog
from clinical.models import ClinicalAlert, QuestionnaireResponse
from core.models import User
from patients.models import Patient

Type = ClinicalAlert.AlertType
Severity = ClinicalAlert.Severity


def url(name, patient, **kwargs):
    return reverse(f'patients:{name}', kwargs={'id': patient.pk, **kwargs})


@pytest.fixture
def logged(client, admin_user):
    client.force_login(admin_user)
    return client


@pytest.fixture
def alert_a(db, patient_a, professional_a):
    return ClinicalAlert.objects.create(
        patient=patient_a, alert_type=Type.DIABETES, severity=Severity.CRITICAL,
        note='Diabetes tipo II.', created_by=professional_a,
    )


@pytest.fixture
def other_alert(db, clinic_a):
    patient = Patient.objects.create(clinic=clinic_a, first_name='Otra', last_name='X', phone='+34600777666')
    return ClinicalAlert.objects.create(patient=patient, alert_type=Type.ALLERGY_LATEX, severity=Severity.CRITICAL)


@pytest.mark.django_db
class TestCreate:
    def test_the_tab_links_to_the_form(self, logged, patient_a):
        html = logged.get(url('tab-alerts', patient_a)).content.decode()

        assert url('alert-create', patient_a) in html

    def test_creates_a_manual_alert_signed_by_the_professional(self, logged, admin_user, patient_a):
        response = logged.post(url('alert-create', patient_a), {
            'alert_type': Type.ALLERGY_LATEX, 'severity': Severity.CRITICAL, 'note': 'Lo dice por teléfono.',
        })

        alert = ClinicalAlert.objects.get(patient=patient_a)
        assert alert.source == ClinicalAlert.Source.MANUAL
        assert alert.source_response is None
        assert alert.created_by == admin_user.professional_profile
        assert alert.is_active and alert.note == 'Lo dice por teléfono.'
        assert response['Location'] == url('tab-alerts', patient_a)

    def test_it_shows_in_the_tab_and_the_banner(self, logged, patient_a):
        logged.post(url('alert-create', patient_a), {'alert_type': Type.ANTICOAGULANTS, 'severity': Severity.CRITICAL})

        tab = logged.get(url('tab-alerts', patient_a)).content.decode()
        general = logged.get(reverse('patients:detail', kwargs={'id': patient_a.pk})).content.decode()

        assert 'Tratamiento anticoagulante' in tab
        assert 'Alertas clínicas activas' in general

    def test_the_type_is_required(self, logged, patient_a):
        response = logged.post(url('alert-create', patient_a), {'alert_type': '', 'severity': Severity.INFO})

        assert response.status_code == 200
        assert not ClinicalAlert.objects.exists()

    def test_the_form_does_not_offer_other_but_offers_the_new_types(self, logged, patient_a):
        html = logged.get(url('alert-create', patient_a)).content.decode()
        select = html[html.index('name="alert_type"'):]
        select = select[:select.index('</select>')]

        assert 'value="other"' not in select
        for value in ('diabetes', 'pacemaker', 'immunosuppression', 'bleeding_disorder',
                      'allergy_adhesives', 'allergy_medication', 'high_risk_foot',
                      'previous_amputation', 'bloodborne_infection', 'pregnancy'):
            assert f'value="{value}"' in select
        assert 'Selecciona el tipo' in select

    def test_other_cannot_be_forced_by_post(self, logged, patient_a):
        response = logged.post(url('alert-create', patient_a), {
            'alert_type': Type.OTHER, 'severity': Severity.INFO, 'note': 'Algo',
        })

        assert response.status_code == 200
        assert not ClinicalAlert.objects.exists()

    def test_a_new_type_is_saved(self, logged, patient_a):
        logged.post(url('alert-create', patient_a), {'alert_type': Type.PACEMAKER, 'severity': Severity.CRITICAL})

        assert ClinicalAlert.objects.get(patient=patient_a).get_alert_type_display() == 'Marcapasos / dispositivo implantado'

    def test_every_type_fits_the_column(self):
        longest = max(len(value) for value, _ in Type.choices)
        assert longest <= ClinicalAlert._meta.get_field('alert_type').max_length

    def test_an_old_alert_of_type_other_is_still_editable(self, logged, patient_a):
        old = ClinicalAlert.objects.create(patient=patient_a, alert_type=Type.OTHER, severity=Severity.INFO, note='Antigua')
        response = logged.post(url('alert-edit', patient_a, alert_id=old.pk), {'severity': Severity.WARNING, 'note': 'Retocada'})

        old.refresh_from_db()
        assert response.status_code == 302
        assert old.alert_type == Type.OTHER and old.note == 'Retocada'

    def test_a_current_alert_of_the_same_type_is_not_duplicated(self, logged, patient_a, alert_a):
        response = logged.post(url('alert-create', patient_a), {'alert_type': Type.DIABETES, 'severity': Severity.CRITICAL})

        assert response.status_code == 200
        assert 'ya tiene una alerta vigente' in response.content.decode()
        assert ClinicalAlert.objects.filter(patient=patient_a).count() == 1

    def test_but_a_deactivated_one_does_not_block_a_new_one(self, logged, patient_a, alert_a):
        alert_a.deactivate()
        logged.post(url('alert-create', patient_a), {'alert_type': Type.DIABETES, 'severity': Severity.CRITICAL})

        assert ClinicalAlert.objects.filter(patient=patient_a, is_active=True).count() == 1
        assert ClinicalAlert.objects.filter(patient=patient_a).count() == 2

    def test_a_forged_source_or_author_is_ignored(self, logged, admin_user, patient_a, published_version_a, episode_a):
        response = QuestionnaireResponse.record(version=published_version_a, patient=patient_a, episode=episode_a, answers={})
        logged.post(url('alert-create', patient_a), {
            'alert_type': Type.NEUROPATHY, 'severity': Severity.WARNING,
            'source': 'derived', 'source_response': response.pk, 'created_by': 999,
        })

        alert = ClinicalAlert.objects.get(patient=patient_a, alert_type=Type.NEUROPATHY)
        assert alert.source == 'manual' and alert.source_response is None
        assert alert.created_by == admin_user.professional_profile

    def test_the_form_read_is_logged(self, logged, patient_a):
        logged.get(url('alert-create', patient_a))

        assert AccessLog.objects.filter(patient=patient_a).exists()

    def test_other_clinic_patient_is_404(self, logged, patient_b):
        assert logged.get(url('alert-create', patient_b)).status_code == 404
        assert logged.post(url('alert-create', patient_b), {'alert_type': Type.DIABETES, 'severity': 'info'}).status_code == 404
        assert not ClinicalAlert.objects.exists()


@pytest.mark.django_db
class TestEdit:
    def test_edit_severity_and_note_but_not_the_type(self, logged, patient_a, alert_a):
        logged.post(url('alert-edit', patient_a, alert_id=alert_a.pk), {
            'alert_type': Type.NEUROPATHY, 'severity': Severity.WARNING, 'note': 'Controlada.',
        })

        alert_a.refresh_from_db()
        assert alert_a.alert_type == Type.DIABETES
        assert alert_a.severity == Severity.WARNING and alert_a.note == 'Controlada.'

    def test_the_edit_read_is_logged_against_the_alert(self, logged, patient_a, alert_a):
        logged.get(url('alert-edit', patient_a, alert_id=alert_a.pk))

        entry = AccessLog.objects.get(patient=patient_a)
        assert entry.object_id == str(alert_a.pk)

    def test_a_derived_alert_is_not_editable(self, logged, patient_a, published_version_a, episode_a):
        response = QuestionnaireResponse.record(version=published_version_a, patient=patient_a, episode=episode_a, answers={})
        derived = ClinicalAlert.objects.create(
            patient=patient_a, alert_type=Type.DIABETES, severity=Severity.CRITICAL,
            source=ClinicalAlert.Source.DERIVED, source_response=response, note='De la anamnesis',
        )
        target = url('alert-edit', patient_a, alert_id=derived.pk)

        assert logged.get(target).status_code == 302
        logged.post(target, {'severity': Severity.INFO, 'note': 'cambiada'})
        derived.refresh_from_db()
        assert derived.note == 'De la anamnesis' and derived.severity == Severity.CRITICAL

    def test_another_patients_alert_is_404(self, logged, patient_a, other_alert):
        target = url('alert-edit', patient_a, alert_id=other_alert.pk)

        assert logged.get(target).status_code == 404
        assert logged.post(target, {'severity': 'info'}).status_code == 404


@pytest.mark.django_db
class TestDeactivateAndReactivate:
    def test_deactivate_keeps_the_row_and_leaves_the_banner(self, logged, patient_a, alert_a):
        logged.post(url('alert-deactivate', patient_a, alert_id=alert_a.pk))

        alert_a.refresh_from_db()
        assert alert_a.is_active is False
        assert ClinicalAlert.all_objects.filter(pk=alert_a.pk).exists()
        general = logged.get(reverse('patients:detail', kwargs={'id': patient_a.pk})).content.decode()
        assert 'Alertas clínicas activas' not in general

    def test_deactivated_alerts_are_listed_and_can_be_reactivated(self, logged, patient_a, alert_a):
        alert_a.deactivate()
        html = logged.get(url('tab-alerts', patient_a)).content.decode()

        assert 'Desactivadas (1)' in html
        assert url('alert-reactivate', patient_a, alert_id=alert_a.pk) in html

        logged.post(url('alert-reactivate', patient_a, alert_id=alert_a.pk))
        alert_a.refresh_from_db()
        assert alert_a.is_active

    def test_there_is_no_way_to_delete_one(self, logged, patient_a, alert_a):
        with pytest.raises(NoReverseMatch):
            reverse('patients:alert-delete', kwargs={'id': patient_a.pk, 'alert_id': alert_a.pk})
        assert logged.post(f'/pacientes/{patient_a.pk}/alertas/{alert_a.pk}/eliminar/').status_code == 404
        assert ClinicalAlert.objects.filter(pk=alert_a.pk).exists()

    def test_actions_are_post_only(self, logged, patient_a, alert_a):
        assert logged.get(url('alert-deactivate', patient_a, alert_id=alert_a.pk)).status_code == 405
        assert logged.get(url('alert-reactivate', patient_a, alert_id=alert_a.pk)).status_code == 405

    def test_another_patients_alert_cannot_be_deactivated(self, logged, patient_a, other_alert):
        response = logged.post(url('alert-deactivate', patient_a, alert_id=other_alert.pk))

        assert response.status_code == 404
        other_alert.refresh_from_db()
        assert other_alert.is_active

    def test_other_clinics_alert_is_404(self, logged, patient_b):
        alert = ClinicalAlert.objects.create(patient=patient_b, alert_type=Type.DIABETES, severity=Severity.CRITICAL)

        assert logged.post(url('alert-deactivate', patient_b, alert_id=alert.pk)).status_code == 404
        alert.refresh_from_db()
        assert alert.is_active

    def test_the_change_is_in_the_changelog(self, logged, patient_a, alert_a):
        logged.post(url('alert-deactivate', patient_a, alert_id=alert_a.pk))

        assert ChangeLog.objects.filter(
            patient=patient_a, action=ChangeLog.Action.UPDATE, model_label__icontains='clinicalalert'
        ).exists()


@pytest.mark.django_db
class TestAuditAndEngine:
    def test_the_note_never_reaches_the_changelog(self, logged, patient_a):
        logged.post(url('alert-create', patient_a), {
            'alert_type': Type.OTHER, 'severity': Severity.WARNING, 'note': 'TEXTO-CLINICO-RESERVADO',
        })

        dump = ' '.join(str(e.changes) for e in ChangeLog.objects.filter(patient=patient_a))
        assert 'TEXTO-CLINICO-RESERVADO' not in dump

    def test_the_engine_does_not_touch_a_manual_alert(self, logged, patient_a, episode_a, published_version_a):
        logged.post(url('alert-create', patient_a), {'alert_type': Type.DIABETES, 'severity': Severity.CRITICAL, 'note': 'Manual'})
        questions = list(published_version_a.questions.order_by('order'))

        QuestionnaireResponse.record(
            version=published_version_a, patient=patient_a, episode=episode_a,
            answers={questions[0].pk: True},
        )
        QuestionnaireResponse.record(
            version=published_version_a, patient=patient_a, episode=episode_a,
            answers={questions[0].pk: False},
        )

        manual = ClinicalAlert.objects.get(patient=patient_a, source='manual')
        assert manual.is_active and manual.note == 'Manual'

    def test_anonymous_goes_to_login(self, client, patient_a):
        response = client.get(url('alert-create', patient_a))

        assert response.status_code == 302 and 'login' in response['Location']
