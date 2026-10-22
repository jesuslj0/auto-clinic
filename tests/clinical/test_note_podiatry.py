"""La nota con subcampos de podología: Exploración, Tratamiento y plan, Antecedentes.

El modelo sigue siendo SOAP. Lo que se defiende aquí:

1. **El hash de una nota firmada antes de los subcampos no cambia**: los campos
   nuevos entran en el hash solo si traen texto. Si no, todas las notas ya firmadas
   dejarían de verificar de golpe.
2. **Y los subcampos sí cuentan**: tocarlos por debajo del ORM rompe el sello.
3. **Antecedentes solo en la primera nota del paciente**, no en cada revisión.
4. **El texto anterior** (`objective`, `plan`) se sigue leyendo y editando, pero las
   notas nuevas no lo ofrecen.
5. La letra SOAP es una referencia discreta; el nombre es el de podología.
6. Los subcampos se auditan como sensibles y un apartado sin contenido no ensucia la nota.
"""
import hashlib
import json

import pytest
from django.db import DatabaseError, connection, transaction
from django.urls import reverse

from audit.models import ChangeLog
from clinical.hashing import compute_note_hash, verify_note_hash
from clinical.models import ClinicalNote, Episode, Visit
from clinical.soap import is_blank, preview_for, sections_for
from patients.models import Patient


def url(name, patient, **kwargs):
    return reverse(f'patients:{name}', kwargs={'id': patient.pk, **kwargs})


@pytest.fixture
def logged(client, admin_user):
    client.force_login(admin_user)
    return client


def legacy_hash(note):
    """El hash tal y como se calculaba ANTES de existir los subcampos."""
    payload = {
        'subjective': note.subjective,
        'objective': note.objective,
        'assessment': note.assessment,
        'plan': note.plan,
        'note_id': note.pk,
        'signed_by_id': note.signed_by_id,
        'signed_at': note.signed_at.isoformat(),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return 'sha256:' + hashlib.sha256(raw.encode('utf-8')).hexdigest()


@pytest.mark.django_db
class TestHash:
    def test_a_note_signed_before_the_subfields_keeps_its_hash(self, draft_note_a, professional_a):
        """Subcampos vacíos → el mismo JSON de siempre → el sello antiguo sigue cuadrando."""
        draft_note_a.sign(professional_a)

        assert draft_note_a.content_hash == legacy_hash(draft_note_a)
        assert verify_note_hash(draft_note_a)

    def test_subfields_are_part_of_the_seal(self, visit_a, professional_a):
        note = ClinicalNote.objects.create(visit=visit_a, inspection='Callosidad', follow_up='Revisión')
        note.sign(professional_a)

        assert verify_note_hash(note)
        assert note.content_hash != legacy_hash(note)

    def test_changing_a_subfield_below_the_orm_breaks_the_seal(self, visit_a, professional_a):
        note = ClinicalNote.objects.create(visit=visit_a, palpation='Dolor en el talón')
        note.sign(professional_a)
        note.palpation = 'Sin dolor'            # en memoria: simula una alteración

        assert not verify_note_hash(note)

    def test_adding_text_to_an_empty_subfield_breaks_an_old_seal(self, draft_note_a, professional_a):
        draft_note_a.sign(professional_a)
        draft_note_a.treatment_done = 'Cosa añadida por debajo'

        assert not verify_note_hash(draft_note_a)
        assert compute_note_hash(draft_note_a) != draft_note_a.content_hash

    def test_the_trigger_still_protects_the_new_columns(self, visit_a, professional_a):
        note = ClinicalNote.objects.create(visit=visit_a, inspection='Uñas sanas')
        note.sign(professional_a)

        with pytest.raises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE clinical_note SET inspection = 'Reescrito' WHERE id = %s", [note.pk]
                )


@pytest.mark.django_db
class TestSections:
    def test_only_filled_subfields_are_listed(self, visit_a):
        note = ClinicalNote.objects.create(visit=visit_a, inspection='Callosidad', follow_up='4 semanas')

        sections = {s['key']: s for s in sections_for(note)}

        assert [f['label'] for f in sections['exam']['fields']] == ['Inspección']
        assert [f['label'] for f in sections['treatment']['fields']] == ['Indicaciones y seguimiento']
        assert not sections['reason']['filled']
        assert 'background' not in sections          # sin antecedentes, ni aparece

    def test_legacy_text_is_still_read(self, visit_a):
        note = ClinicalNote.objects.create(visit=visit_a, objective='Texto viejo', plan='Plan viejo')

        sections = {s['key']: s for s in sections_for(note)}

        assert sections['exam']['fields'][0]['value'] == 'Texto viejo'
        assert 'anterior' in sections['exam']['fields'][0]['label']
        assert sections['treatment']['fields'][0]['value'] == 'Plan viejo'

    def test_blank_and_preview_consider_every_field(self, visit_a):
        assert is_blank(ClinicalNote(visit=visit_a))
        assert not is_blank(ClinicalNote(visit=visit_a, biomechanics='Pie cavo'))
        assert preview_for(ClinicalNote(visit=visit_a, biomechanics='Pie cavo')) == 'Pie cavo'
        assert preview_for(ClinicalNote(visit=visit_a, assessment='Dx', subjective='Mc')) == 'Dx'

    def test_the_letter_is_a_small_reference_next_to_the_name(self, logged, patient_a, visit_a):
        html = logged.get(url('note-create', patient_a, visit_id=visit_a.pk)).content.decode()

        assert 'title="S de SOAP: Subjetivo"' in html
        assert 'title="O de SOAP: Objetivo"' in html
        assert 'title="A de SOAP: Análisis (Assessment)"' in html
        assert 'title="P de SOAP: Plan"' in html
        # Los nombres visibles son los de podología, no los SOAP.
        assert '>Subjetivo<' not in html and '>Objetivo<' not in html


@pytest.mark.django_db
class TestBackground:
    def test_offered_on_the_patients_first_note(self, logged, patient_a, visit_a):
        html = logged.get(url('note-create', patient_a, visit_id=visit_a.pk)).content.decode()

        assert 'Antecedentes' in html
        assert 'name="background"' in html

    def test_not_repeated_on_later_notes(self, logged, patient_a, visit_a, draft_note_a, professional_a):
        draft_note_a.sign(professional_a)
        second = Visit.objects.create(episode=visit_a.episode, professional=professional_a)

        html = logged.get(url('note-create', patient_a, visit_id=second.pk)).content.decode()

        assert 'name="background"' not in html

    def test_counts_per_patient_not_per_episode(
        self, logged, patient_a, draft_note_a, professional_a
    ):
        """Un episodio nuevo del mismo paciente no vuelve a pedir antecedentes."""
        draft_note_a.sign(professional_a)
        episode = Episode.objects.create(history=patient_a.medical_history, reason='Otro problema')
        visit = Visit.objects.create(episode=episode, professional=professional_a)

        html = logged.get(url('note-create', patient_a, visit_id=visit.pk)).content.decode()

        assert 'name="background"' not in html

    def test_another_patients_notes_do_not_hide_it(self, logged, patient_a, visit_a, clinic_a, professional_a):
        other = Patient.objects.create(clinic=clinic_a, first_name='Otra', last_name='X', phone='+34600999888')
        other_visit = Visit.objects.create(
            episode=Episode.objects.create(history=other.medical_history, reason='x'),
            professional=professional_a,
        )
        ClinicalNote.objects.create(visit=other_visit, subjective='Algo')

        html = logged.get(url('note-create', patient_a, visit_id=visit_a.pk)).content.decode()

        assert 'name="background"' in html

    def test_it_is_saved_and_shown_in_the_note(self, logged, patient_a, visit_a):
        logged.post(url('note-create', patient_a, visit_id=visit_a.pk), {
            'background': 'Calzado deportivo. Diabetes tipo 2.', 'subjective': 'Dolor',
        })
        note = ClinicalNote.objects.get(visit=visit_a)

        assert note.background == 'Calzado deportivo. Diabetes tipo 2.'
        html = logged.get(url('note-detail', patient_a, note_id=note.pk)).content.decode()
        assert 'Antecedentes' in html and 'Diabetes tipo 2' in html

    def test_a_draft_that_has_it_keeps_the_field_when_edited_later(
        self, logged, patient_a, visit_a, professional_a
    ):
        first = ClinicalNote.objects.create(visit=visit_a, background='Diabetes', subjective='Dolor')
        ClinicalNote.objects.create(
            visit=Visit.objects.create(episode=visit_a.episode, professional=professional_a),
            subjective='Revisión',
        )

        html = logged.get(url('note-edit', patient_a, note_id=first.pk)).content.decode()

        assert 'name="background"' in html and 'Diabetes' in html


@pytest.mark.django_db
class TestLegacyText:
    def test_a_draft_with_legacy_text_can_still_edit_it(self, logged, patient_a, visit_a):
        note = ClinicalNote.objects.create(visit=visit_a, objective='Exploración vieja', plan='Plan viejo')

        html = logged.get(url('note-edit', patient_a, note_id=note.pk)).content.decode()

        assert 'Exploración (texto anterior)' in html
        assert 'Tratamiento y plan (texto anterior)' in html
        assert 'Exploración vieja' in html

    def test_new_notes_do_not_offer_the_legacy_fields(self, logged, patient_a, visit_a):
        html = logged.get(url('note-create', patient_a, visit_id=visit_a.pk)).content.decode()

        assert 'name="objective"' not in html and 'name="plan"' not in html

    def test_posting_legacy_fields_to_a_new_note_is_ignored(self, logged, patient_a, visit_a):
        logged.post(url('note-create', patient_a, visit_id=visit_a.pk), {
            'subjective': 'Dolor', 'objective': 'colado', 'plan': 'colado',
        })

        note = ClinicalNote.objects.get(visit=visit_a)
        assert note.objective == '' and note.plan == ''


@pytest.mark.django_db
class TestAudit:
    def test_the_subfields_are_sensitive_in_the_changelog(self, logged, patient_a, visit_a):
        logged.post(url('note-create', patient_a, visit_id=visit_a.pk), {
            'inspection': 'TEXTO-CLINICO-INSPECCION', 'follow_up': 'TEXTO-CLINICO-SEGUIMIENTO',
            'background': 'TEXTO-CLINICO-ANTECEDENTES',
        })

        dump = ' '.join(str(e.changes) for e in ChangeLog.objects.filter(patient=patient_a))
        assert 'TEXTO-CLINICO' not in dump
        assert ChangeLog.objects.filter(patient=patient_a, model_label__icontains='clinicalnote').exists()
