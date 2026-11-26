"""Exportación de la ficha de un paciente: qué lleva y qué no."""
import hashlib
import json
import subprocess
import sys

import pytest
from django.core.files.storage import storages
from django.core.files.base import ContentFile

from clinical.hashing import verify_note_hash
from clinical.models import ClinicalNote
from exports.builder import VERIFY_SCRIPT
from exports.schema import Scope
from patients.models import Patient

from tests.exports.helpers import build, manifest, rows

pytestmark = pytest.mark.django_db


@pytest.fixture
def archive(file_a, clinic_a, patient_a):
    archive, result = build(Scope(clinic=clinic_a, patient=patient_a))
    archive.result = result
    return archive


def all_text(archive):
    """Todo el texto de los ficheros de datos y del manifest, para buscar dentro."""
    parts = []
    for name in archive.namelist():
        if name.startswith('datos/') or name.endswith(('.json', '.jsonl', '.txt')):
            parts.append(archive.read(name).decode('utf-8'))
    return '\n'.join(parts)


class TestContent:
    def test_it_carries_the_whole_clinical_file(self, archive, file_a, patient_a):
        counts = archive.result.counts
        for name in (
            'patients', 'medical_histories', 'episodes', 'visits', 'clinical_notes', 'addenda',
            'questionnaire_responses', 'clinical_alerts', 'lesion_observations',
            'lesion_attachments', 'performed_procedures', 'signed_consents', 'appointments',
            'invoices', 'payments', 'conversations', 'chat_messages', 'chat_attachments',
        ):
            assert counts[name] >= 1, name
        assert rows(archive, 'patients')[0]['id'] == patient_a.pk

    def test_soft_deleted_rows_are_included_with_their_deletion_date(self, archive, file_a):
        lesions = {row['id']: row for row in rows(archive, 'lesions')}
        assert lesions[file_a.lesion.pk]['deleted_at'] is None
        assert lesions[file_a.gone_lesion.pk]['deleted_at'] is not None
        observations = {row['id']: row for row in rows(archive, 'lesion_observations')}
        assert file_a.gone_observation.pk in observations

    def test_frozen_values_come_out_as_stored(self, archive, file_a):
        invoice = rows(archive, 'invoices')[0]
        assert invoice['total'] == '50.00'
        assert invoice['lines'], 'las líneas se congelan al emitir'
        procedure = rows(archive, 'performed_procedures')[0]
        assert procedure['frozen_price'] == '50.00'
        consent = rows(archive, 'signed_consents')[0]
        assert consent['text_copy'] == 'Texto consentimiento ALFA'
        response = rows(archive, 'questionnaire_responses')[0]
        assert 'Respuesta ALFA' in json.dumps(response['snapshot'])

    def test_lesion_zone_and_drawing_coordinates_are_separate(self, archive, file_a):
        lesion = next(r for r in rows(archive, 'lesions') if r['id'] == file_a.lesion.pk)
        assert lesion['anatomical_zone'] and lesion['x'] == 0.42 and lesion['y'] == 0.63
        assert manifest(archive)['foot_map']['width'] > 0

    def test_a_guardian_only_shows_who_they_are(self, archive, file_a):
        guardian = rows(archive, 'guardians')[0]
        assert set(guardian) == {'id', 'first_name', 'last_name'}
        assert 'tutor-ALFA@example.com' not in all_text(archive)
        assert file_a.guardian.phone not in all_text(archive)

    def test_only_professionals_of_this_file_and_without_contact_data(self, archive, file_a):
        professionals = rows(archive, 'professionals')
        assert [p['id'] for p in professionals] == [file_a.professional.pk]
        assert 'contact_phone' not in professionals[0]
        assert 'user_id' not in professionals[0]

    def test_raw_whatsapp_payload_stays_out_of_a_patients_copy(self, archive):
        assert 'raw' not in rows(archive, 'chat_messages')[0]

    def test_internal_agent_state_stays_out(self, archive):
        conversation = rows(archive, 'conversations')[0]
        assert 'session_data' not in conversation and 'appointment_context' not in conversation

    def test_the_clinic_only_entities_are_absent(self, archive):
        names = archive.namelist()
        assert 'datos/users.jsonl' not in names
        assert 'datos/services.jsonl' not in names
        assert 'datos/clinic.jsonl' not in names


class TestIsolation:
    def test_nothing_of_another_clinic_or_patient_gets_in(self, file_a, file_b, clinic_a, patient_a):
        archive, _ = build(Scope(clinic=clinic_a, patient=patient_a))
        text = all_text(archive)
        assert 'ALFA' in text
        assert 'BETA' not in text
        assert 'Clinic Beta' not in text

    def test_another_patient_of_the_same_clinic_does_not_get_in(
        self, file_a, clinic_a, patient_a, admin_user, service_a,
    ):
        from tests.exports.factory import build_file

        other = Patient.objects.create(clinic=clinic_a, first_name='Otra', last_name='Persona')
        build_file(clinic_a, admin_user, other, service_a, 'OTRA')

        archive, _ = build(Scope(clinic=clinic_a, patient=patient_a))
        assert 'OTRA' not in all_text(archive)
        archive, _ = build(Scope(clinic=clinic_a, patient=other))
        assert 'OTRA' in all_text(archive)
        assert 'ALFA' not in all_text(archive)


class TestSecrets:
    def test_no_secret_leaves(self, archive, file_a):
        """Ni el valor ni la columna: el manifest solo *nombra* lo excluido, en `excluded_fields`."""
        data = '\n'.join(
            archive.read(name).decode('utf-8') for name in archive.namelist() if name.startswith('datos/')
        )
        assert str(file_a.appointment.confirmation_token) not in data
        assert 'confirmation_token' not in data
        assert 'token_hash' not in data

    def test_the_manifest_says_what_was_left_out_and_why(self, archive):
        info = manifest(archive)
        assert 'confirmation_token' in info['excluded_fields']['Appointment']
        assert 'core.TOTPDevice' in info['excluded_models']


class TestIntegrity:
    def test_the_manifest_matches_the_bytes_of_every_data_file(self, archive):
        for name, info in manifest(archive)['datos'].items():
            payload = archive.read(info['path'])
            assert hashlib.sha256(payload).hexdigest() == info['sha256'], name
            assert len(payload) == info['bytes']
            assert payload.count(b'\n') == info['rows']

    def test_every_attachment_is_in_the_zip_with_its_stored_checksum(self, archive):
        listing = [json.loads(l) for l in archive.read('manifest_ficheros.jsonl').decode().splitlines()]
        assert len(listing) == 3
        for record in listing:
            assert record['status'] == 'ok'
            payload = archive.read(record['path'])
            assert 'sha256:' + hashlib.sha256(payload).hexdigest() == record['sha256']
            assert record['stored_checksum'] == record['sha256']

    def test_the_attachment_rows_point_at_their_file(self, archive):
        row = rows(archive, 'lesion_attachments')[0]
        assert row['zip_path'] in archive.namelist()
        assert row['file_status'] == 'ok'

    def test_a_missing_object_is_noted_not_fatal(self, file_a, clinic_a, patient_a):
        storages['clinical_media'].delete(file_a.attachment.file.name)

        archive, result = build(Scope(clinic=clinic_a, patient=patient_a))

        assert result.files['missing'] == 1 and result.has_warnings
        row = rows(archive, 'lesion_attachments')[0]
        assert row['file_status'] == 'missing' and row['zip_path'] is None
        assert 'adjuntos/lesion-attachments/' not in ' '.join(archive.namelist())

    def test_a_changed_object_is_flagged(self, file_a, clinic_a, patient_a):
        name = file_a.attachment.file.name
        storages['clinical_media'].delete(name)
        storages['clinical_media'].save(name, ContentFile(b'otra cosa'))

        archive, result = build(Scope(clinic=clinic_a, patient=patient_a))

        assert result.files['checksum_mismatch'] == 1
        assert rows(archive, 'lesion_attachments')[0]['file_status'] == 'checksum_mismatch'

    def test_orphan_rows_of_a_deleted_patient_are_still_reachable_by_patient_id(
        self, file_a, clinic_a, patient_a,
    ):
        """Los registros clínicos sobreviven al paciente (FK sin constraint)."""
        history = patient_a.medical_history
        Patient.objects.filter(pk=patient_a.pk).delete()
        scope = Scope(clinic=clinic_a, patient=Patient(pk=patient_a.pk, clinic=clinic_a,
                                                       first_name='John', last_name='Doe'))

        archive, _ = build(scope)

        assert rows(archive, 'medical_histories')[0]['id'] == history.pk
        assert rows(archive, 'clinical_notes')


class TestVerificationScript:
    @pytest.fixture
    def folder(self, archive, tmp_path):
        archive.extractall(tmp_path)
        return tmp_path

    def run(self, folder):
        return subprocess.run(
            [sys.executable, '-I', str(folder / 'verificar_firmas.py'), str(folder)],
            capture_output=True, text=True,
        )

    def test_an_untouched_export_verifies(self, folder):
        outcome = self.run(folder)
        assert outcome.returncode == 0, outcome.stdout
        assert 'Todo correcto' in outcome.stdout
        assert 'Notas clínicas firmadas: 1 (sello correcto: 1)' in outcome.stdout

    def test_the_shipped_script_is_the_one_in_the_repo(self, archive):
        assert archive.read('verificar_firmas.py') == VERIFY_SCRIPT.read_bytes()

    def test_a_tampered_note_is_caught_by_its_seal(self, folder):
        path = folder / 'datos' / 'clinical_notes.jsonl'
        note = json.loads(path.read_text(encoding='utf-8'))
        note['plan'] = 'Plan distinto'
        path.write_text(json.dumps(note, ensure_ascii=False) + '\n', encoding='utf-8')

        outcome = self.run(folder)

        assert outcome.returncode == 1
        assert 'sello de la nota clínica' in outcome.stdout
        assert 'no coincide con el manifest' in outcome.stdout

    def test_a_tampered_attachment_is_caught(self, folder):
        photo = next((folder / 'adjuntos' / 'lesion-attachments').iterdir())
        photo.write_bytes(b'x')
        outcome = self.run(folder)
        assert outcome.returncode == 1 and 'adjunto' in outcome.stdout.lower()

    def test_the_script_computes_the_same_seal_as_the_application(self, archive, file_a):
        """El cálculo duplicado fuera de la app no puede divergir del de dentro."""
        import importlib.util

        spec = importlib.util.spec_from_file_location('verificar_firmas', VERIFY_SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Una nota con campos «posteriores» y otra sin ninguno (firmada como antes).
        old_style = ClinicalNote.objects.create(
            visit=file_a.visit, subjective='a', objective='b', assessment='c', plan='d',
        )
        old_style.sign(file_a.professional)
        from exports.schema import Scope
        archive, _ = build(Scope(clinic=file_a.visit.episode.history.clinic,
                                 patient=file_a.visit.episode.history.patient))

        exported = {row['id']: row for row in rows(archive, 'clinical_notes')}
        for note in (file_a.note, old_style):
            fresh = ClinicalNote.objects.get(pk=note.pk)
            assert verify_note_hash(fresh)
            assert module.note_hash(exported[note.pk]) == fresh.content_hash


class TestPaths:
    def test_a_path_segment_never_climbs_out_of_its_folder(self):
        from exports.builder import _safe_segment

        for hostile in ('../../etc/passwd', 'a/b', '..', '...', 'clínica con espacios', ''):
            cleaned = _safe_segment(hostile)
            assert '/' not in cleaned and '\\' not in cleaned and not cleaned.startswith('.')
            assert cleaned, hostile
        assert _safe_segment('clinic-alpha') == 'clinic-alpha'
        assert _safe_segment(42) == '42'

    def test_a_clinic_id_with_separators_cannot_escape_in_the_zip(self, db):
        """El `clinic_id` es texto libre y el logotipo se guarda bajo él."""
        from django.core.files.base import ContentFile

        from core.models import Clinic
        from tests.clinical.test_lesion_attachments import image_bytes

        clinic = Clinic.objects.create(clinic_id='../escape', name='Hostil')
        clinic.logo.save('logo.png', ContentFile(image_bytes('PNG')), save=True)

        archive, _ = build(Scope(clinic=clinic))

        logo = [name for name in archive.namelist() if name.startswith('adjuntos/clinic-logo/')]
        assert logo, 'el logotipo debe salir'
        assert all('..' not in name and not name.startswith('/') for name in archive.namelist())
        assert logo[0].startswith('adjuntos/clinic-logo/') and logo[0].count('/') == 2
