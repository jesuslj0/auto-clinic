"""Exportación de la clínica entera: todo lo suyo y nada de otra."""
import hashlib
import json
from datetime import timedelta

import pytest
from django.utils import timezone

from agent.models import ChatMessage, ConversationSession
from agent.services import record_message
from appointments.models import Appointment
from exports.schema import Scope
from patients.models import Patient

from tests.exports.helpers import build, manifest, rows

pytestmark = pytest.mark.django_db


@pytest.fixture
def clinic_secrets(clinic_a):
    clinic_a.whatsapp_token = 'SECRETO-WHATSAPP-TOKEN'
    clinic_a.whatsapp_verify_token = 'SECRETO-VERIFY'
    clinic_a.api_key = 'SECRETO-API-KEY'
    clinic_a.calendly_token = 'SECRETO-CALENDLY'
    clinic_a.save()
    return clinic_a


@pytest.fixture
def archive(file_a, file_b, clinic_secrets):
    archive, result = build(Scope(clinic=clinic_secrets))
    archive.result = result
    return archive


def all_data(archive):
    return '\n'.join(
        archive.read(name).decode('utf-8') for name in archive.namelist() if name.startswith('datos/')
    )


class TestContent:
    def test_it_has_everything_a_patient_copy_has_for_every_patient(self, file_a, clinic_a, admin_user, service_a):
        from tests.exports.factory import build_file

        other = Patient.objects.create(clinic=clinic_a, first_name='Otra', last_name='Persona')
        build_file(clinic_a, admin_user, other, service_a, 'OTRA')

        archive, _ = build(Scope(clinic=clinic_a))

        assert len(rows(archive, 'patients')) == 2
        text = all_data(archive)
        assert 'ALFA' in text and 'OTRA' in text
        assert len(rows(archive, 'clinical_notes')) == 2

    def test_it_adds_the_clinics_own_catalogues_and_setup(self, archive, clinic_a, service_a):
        for name in ('clinic', 'users', 'services', 'professionals', 'professional_schedules',
                     'consent_templates', 'consent_versions', 'questionnaire_templates',
                     'template_versions', 'questions', 'invoice_sequences', 'receipt_sequences',
                     'history_sequences', 'change_log', 'access_log'):
            assert f'datos/{name}.jsonl' in archive.namelist(), name
        assert [r['clinic_id'] for r in rows(archive, 'clinic')] == [clinic_a.pk]
        assert rows(archive, 'services')[0]['name'] == 'Consultation'

    def test_a_guardian_comes_out_whole_here(self, archive, file_a):
        guardian = rows(archive, 'guardians')[0]
        assert guardian['phone'] == file_a.guardian.phone and guardian['email']

    def test_professionals_carry_their_contact_data_and_login_link(self, archive, admin_user):
        professional = rows(archive, 'professionals')[0]
        assert professional['user_id'] == admin_user.pk
        assert 'contact_phone' in professional and 'user__first_name' in professional

    def test_the_raw_whatsapp_payload_is_kept_for_the_clinic(self, archive):
        assert 'raw' in rows(archive, 'chat_messages')[0]

    def test_soft_deleted_rows_are_included(self, archive, file_a):
        ids = {row['id'] for row in rows(archive, 'lesions') if row['deleted_at']}
        assert file_a.gone_lesion.pk in ids

    def test_no_pdf_in_the_clinic_zip(self, archive):
        assert 'paciente.pdf' not in archive.namelist()
        assert 'paciente.pdf' not in manifest(archive)['otros']

    def test_appointments_without_a_patient_file_are_in(self, file_a, clinic_a, service_a):
        when = timezone.now() + timedelta(days=3)
        Appointment.objects.create(
            clinic=clinic_a, patient=None, patient_name='Primera Visita', patient_phone='+34600777666',
            service=service_a, scheduled_at=when, end_at=when + timedelta(minutes=30),
            status=Appointment.Status.PENDING, booked_for_other=True, contact_name='La hija',
        )
        archive, _ = build(Scope(clinic=clinic_a))
        assert any(r['patient_name'] == 'Primera Visita' for r in rows(archive, 'appointments'))
        patient_archive, _ = build(Scope(clinic=clinic_a, patient=file_a.guardian.clinic.patients.first()))
        assert 'Primera Visita' not in all_data(patient_archive)

    def test_conversations_without_a_patient_are_in_and_test_ones_are_not(self, file_a, clinic_a):
        stranger = ConversationSession.objects.create(clinic=clinic_a, phone='+34600555444')
        record_message(
            clinic=clinic_a, session=stranger, direction=ChatMessage.Direction.INBOUND,
            sender=ChatMessage.Sender.PATIENT, body='Hola, soy nuevo',
        )
        test_session = ConversationSession.objects.create(clinic=clinic_a, phone='+34600333222', is_test=True)
        record_message(
            clinic=clinic_a, session=test_session, direction=ChatMessage.Direction.INBOUND,
            sender=ChatMessage.Sender.PATIENT, body='mensaje de prueba',
        )

        archive, _ = build(Scope(clinic=clinic_a))

        text = all_data(archive)
        assert 'Hola, soy nuevo' in text
        assert 'mensaje de prueba' not in text

    def test_rows_of_a_deleted_patient_are_still_exported(self, file_a, clinic_a, patient_a):
        Patient.objects.filter(pk=patient_a.pk).delete()

        archive, _ = build(Scope(clinic=clinic_a))

        assert rows(archive, 'patients') == []
        assert rows(archive, 'clinical_notes'), 'la historia sobrevive al paciente'
        assert rows(archive, 'clinical_alerts'), 'también las alertas, que no llevan clínica'
        assert rows(archive, 'signed_consents')


class TestIsolation:
    def test_nothing_of_another_clinic_gets_in(self, archive):
        text = all_data(archive) + archive.read('manifest.json').decode()
        assert 'ALFA' in text
        for leaked in ('BETA', 'Clinic Beta', 'clinic-beta', 'Jane', 'Smith'):
            assert leaked not in text, leaked

    def test_users_of_another_clinic_are_not_listed(self, archive, admin_user_b):
        emails = {row['email'] for row in rows(archive, 'users')}
        assert admin_user_b.email not in emails

    def test_the_other_clinic_gets_only_its_own(self, file_a, file_b, clinic_b):
        archive, _ = build(Scope(clinic=clinic_b))
        text = all_data(archive)
        assert 'BETA' in text and 'ALFA' not in text and 'Clinic Alpha' not in text

    def test_audit_logs_are_only_those_of_the_clinics_people_and_patients(self, file_a, file_b, clinic_a):
        archive, _ = build(Scope(clinic=clinic_a))
        patient_ids = {p['id'] for p in rows(archive, 'patients')}
        user_ids = {u['id'] for u in rows(archive, 'users')}
        for name in ('change_log', 'access_log'):
            for row in rows(archive, name):
                assert row['patient_id'] in patient_ids or row['user_id'] in user_ids, (name, row)
        assert rows(archive, 'change_log')


class TestSecrets:
    def test_no_credential_leaves(self, archive, admin_user, clinic_secrets):
        text = all_data(archive) + archive.read('manifest.json').decode().split('"excluded_models"')[0]
        for secret in ('SECRETO-WHATSAPP-TOKEN', 'SECRETO-VERIFY', 'SECRETO-API-KEY', 'SECRETO-CALENDLY',
                       str(clinic_secrets.agent_api_key), admin_user.password, 'pbkdf2_'):
            assert secret not in text, secret

    def test_no_two_factor_material(self, archive, admin_user):
        from core import two_factor

        device = two_factor.start_enrollment(admin_user)
        archive, _ = build(Scope(clinic=admin_user.clinic))
        assert device.secret not in all_data(archive)
        assert not any('totp' in name.lower() or 'recovery' in name.lower() for name in archive.namelist())

    def test_users_come_out_without_passwords_or_permissions(self, archive):
        user = rows(archive, 'users')[0]
        assert not {'password', 'is_superuser', 'is_staff', 'username', 'last_login'} & set(user)


class TestIntegrity:
    def test_the_manifest_matches_every_file_and_the_verifier_agrees(self, archive, tmp_path):
        import subprocess
        import sys

        for name, info in manifest(archive)['datos'].items():
            assert hashlib.sha256(archive.read(info['path'])).hexdigest() == info['sha256'], name
        archive.extractall(tmp_path)

        outcome = subprocess.run(
            [sys.executable, '-I', str(tmp_path / 'verificar_firmas.py'), str(tmp_path)],
            capture_output=True, text=True,
        )

        assert outcome.returncode == 0, outcome.stdout
        assert 'Notas clínicas firmadas: 1 (sello correcto: 1)' in outcome.stdout

    def test_every_attachment_of_the_clinic_is_listed_and_ok(self, archive):
        listing = [json.loads(l) for l in archive.read('manifest_ficheros.jsonl').decode().splitlines()]
        entities = {record['entity'] for record in listing}
        assert {'lesion_attachments', 'signed_consents', 'chat_attachments'} <= entities
        assert all(record['status'] == 'ok' for record in listing)

    def test_the_readme_lists_the_audit_files_once(self, archive):
        readme = archive.read('LEEME.txt').decode()
        assert readme.count('datos/change_log.jsonl') == 1
        assert readme.count('datos/access_log.jsonl') == 1
        assert 'clínica entera' in readme
