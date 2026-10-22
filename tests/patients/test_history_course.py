"""El curso clínico en el panel: episodio → visita → nota SOAP → firma → adenda.

Lo que se defiende aquí no es que las pantallas salgan, sino las reglas que no
pueden fallar en silencio:

1. **Una nota firmada no se cambia por ninguna vista**: ni editar, ni borrar, ni
   volver a firmar, ni siquiera forzando el POST a mano. Solo admite adendas.
2. **Firma solo un profesional**, y a su nombre (el firmante no viene del
   formulario).
3. **Aislamiento**: un paciente de otra clínica es 404; y la nota o el episodio de
   OTRO paciente de la misma clínica no se abre colándolo en la URL.
4. **Toda lectura deja `AccessLog`**, apuntando al paciente.
5. Un episodio cerrado no admite visitas ni notas nuevas hasta reabrirse.
6. Las etiquetas son las de podología (SOAP explicado), no las del campo del modelo.
"""
import pytest
from django.urls import reverse
from django.utils import timezone

from appointments.models import Professional
from audit.models import AccessLog, ChangeLog
from clinical.models import Addendum, ClinicalNote, Episode, Visit
from core.models import User
from patients.models import Patient

NOTE = {
    'subjective': 'Dolor en el talón derecho al levantarse desde hace 3 semanas.',
    'inspection': 'Pie pronado sin lesiones cutáneas.',
    'palpation': 'Dolor a la palpación en la inserción de la fascia plantar.',
    'assessment': 'Fascitis plantar derecha.',
    'treatment_done': 'Vendaje funcional.',
    'follow_up': 'Estiramientos diarios. Revisión en 4 semanas.',
}


def url(name, patient, **kwargs):
    return reverse(f'patients:{name}', kwargs={'id': patient.pk, **kwargs})


@pytest.fixture
def logged(client, admin_user):
    client.force_login(admin_user)
    return client


@pytest.fixture
def staff_without_profile(client, clinic_a):
    """Un usuario de la clínica sin perfil de profesional (puede preparar borradores).

    Sin `role` la señal de `appointments` no le crea perfil. (Borrárselo a un
    usuario con rol no sirve: `force_login` lo guarda y la señal lo recrea.)
    """
    user = User.objects.create_user(
        email='recepcion@alpha.test', password='testpass123', clinic=clinic_a, role='',
    )
    assert not Professional.objects.filter(user=user).exists()
    client.force_login(user)
    return client


@pytest.fixture
def signed_note_a(draft_note_a, professional_a):
    draft_note_a.sign(professional_a)
    return draft_note_a


@pytest.fixture
def other_patient(db, clinic_a, professional_a):
    """Otro paciente de la MISMA clínica con su episodio, visita y nota."""
    patient = Patient.objects.create(
        clinic=clinic_a, first_name='Otra', last_name='Persona',
        email='otra@alpha.test', phone='+34600111222',
    )
    episode = Episode.objects.create(
        history=patient.medical_history, reason='Otra cosa', responsible_professional=professional_a,
    )
    visit = Visit.objects.create(episode=episode, professional=professional_a)
    patient.test_episode, patient.test_visit = episode, visit
    patient.test_note = ClinicalNote.objects.create(visit=visit, subjective='Cosa privada de otra persona')
    return patient


# ---------------------------------------------------------------------------
# Pestaña «Historia»
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestHistoryTab:
    def test_lists_episodes_visits_and_notes(self, logged, patient_a, episode_a, visit_a, draft_note_a):
        html = logged.get(url('tab-history', patient_a)).content.decode()

        assert 'Dolor en el talón derecho' in html     # motivo del episodio
        assert 'Callosidad plantar' in html            # vista previa: el análisis
        assert 'Borrador' in html
        assert url('note-detail', patient_a, note_id=draft_note_a.pk) in html

    def test_only_a_preview_line_of_the_note_is_listed(self, logged, patient_a, draft_note_a):
        """El texto entero se lee en la nota, que registra su propio acceso."""
        html = logged.get(url('tab-history', patient_a)).content.decode()

        assert 'Refiere dolor al caminar' not in html  # subjetivo: no es la vista previa
        assert 'Deslaminado' not in html               # tratamiento

    def test_empty_state_offers_to_open_an_episode(self, logged, patient_a):
        html = logged.get(url('tab-history', patient_a)).content.decode()

        assert 'Aún no hay episodios' in html
        assert url('episode-create', patient_a) in html

    def test_links_to_the_rest_of_the_record(self, logged, patient_a, episode_a, visit_a):
        html = logged.get(url('tab-history', patient_a)).content.decode()

        for name in ('tab-lesions', 'tab-procedures', 'consent-sign', 'anamnesis-create'):
            assert url(name, patient_a) in html
        # Anamnesis y consentimientos viven en «Documentos», con su ancla.
        assert f"{url('tab-documents', patient_a)}#consentimientos" in html
        assert f"{url('tab-documents', patient_a)}#anamnesis" in html

    def test_the_tab_bar_includes_history(self, logged, patient_a):
        html = logged.get(reverse('patients:detail', kwargs={'id': patient_a.pk})).content.decode()

        assert url('tab-history', patient_a) in html

    def test_other_clinic_is_404_without_access_log(self, logged, patient_b):
        assert logged.get(url('tab-history', patient_b)).status_code == 404
        assert not AccessLog.objects.filter(patient=patient_b).exists()

    def test_read_is_logged(self, logged, patient_a):
        logged.get(url('tab-history', patient_a))

        assert AccessLog.objects.filter(patient=patient_a).exists()

    def test_other_patients_notes_are_not_listed(self, logged, patient_a, other_patient):
        html = logged.get(url('tab-history', patient_a)).content.decode()

        assert 'Otra cosa' not in html


# ---------------------------------------------------------------------------
# Episodios y visitas
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestEpisodes:
    def test_open_an_episode(self, logged, admin_user, patient_a):
        response = logged.post(
            url('episode-create', patient_a),
            {'reason': 'Uña encarnada', 'responsible_professional': admin_user.professional_profile.pk},
        )

        episode = Episode.objects.get(history__patient=patient_a)
        assert episode.reason == 'Uña encarnada'
        assert episode.status == Episode.Status.OPEN
        assert response.status_code == 302
        assert f'#episodio-{episode.pk}' in response['Location']

    def test_the_reason_is_required(self, logged, patient_a):
        response = logged.post(url('episode-create', patient_a), {'reason': '   '})

        assert response.status_code == 200
        assert not Episode.objects.filter(history__patient=patient_a).exists()

    def test_edit_the_reason_leaves_a_masked_changelog(self, logged, patient_a, episode_a):
        logged.post(url('episode-edit', patient_a, episode_id=episode_a.pk), {'reason': 'Dolor en el talón izquierdo'})

        episode_a.refresh_from_db()
        assert episode_a.reason == 'Dolor en el talón izquierdo'
        assert ChangeLog.objects.filter(patient=patient_a, action=ChangeLog.Action.UPDATE).exists()

    def test_close_and_reopen(self, logged, patient_a, episode_a):
        logged.post(url('episode-close', patient_a, episode_id=episode_a.pk))
        episode_a.refresh_from_db()
        assert episode_a.status == Episode.Status.CLOSED
        assert episode_a.discharged_at is not None

        logged.post(url('episode-reopen', patient_a, episode_id=episode_a.pk))
        episode_a.refresh_from_db()
        assert episode_a.status == Episode.Status.OPEN
        assert episode_a.discharged_at is None

    def test_close_is_post_only(self, logged, patient_a, episode_a):
        response = logged.get(url('episode-close', patient_a, episode_id=episode_a.pk))

        assert response.status_code == 405

    def test_another_patients_episode_is_404(self, logged, patient_a, other_patient):
        target = url('episode-edit', patient_a, episode_id=other_patient.test_episode.pk)

        assert logged.get(target).status_code == 404
        assert logged.post(target, {'reason': 'x'}).status_code == 404
        assert logged.post(
            url('episode-close', patient_a, episode_id=other_patient.test_episode.pk)
        ).status_code == 404
        other_patient.test_episode.refresh_from_db()
        assert other_patient.test_episode.status == Episode.Status.OPEN

    def test_edit_page_read_is_logged(self, logged, patient_a, episode_a):
        logged.get(url('episode-edit', patient_a, episode_id=episode_a.pk))

        assert AccessLog.objects.filter(patient=patient_a).exists()


@pytest.mark.django_db
class TestVisits:
    def test_register_a_visit_and_go_straight_to_the_note(
        self, logged, admin_user, patient_a, episode_a
    ):
        response = logged.post(
            url('visit-create', patient_a, episode_id=episode_a.pk),
            {
                'professional': admin_user.professional_profile.pk,
                'occurred_at': '2026-10-01T10:30',
                'then': 'note',
            },
        )

        visit = Visit.objects.get(episode=episode_a)
        assert visit.occurred_at.year == 2026
        assert response['Location'] == url('note-create', patient_a, visit_id=visit.pk)

    def test_a_visit_cannot_be_in_the_future(self, logged, admin_user, patient_a, episode_a):
        future = (timezone.localtime() + timezone.timedelta(days=2)).strftime('%Y-%m-%dT%H:%M')
        response = logged.post(
            url('visit-create', patient_a, episode_id=episode_a.pk),
            {'professional': admin_user.professional_profile.pk, 'occurred_at': future},
        )

        assert response.status_code == 200
        assert not Visit.objects.filter(episode=episode_a).exists()

    def test_closed_episode_takes_no_new_visits(
        self, logged, admin_user, patient_a, episode_a
    ):
        episode_a.close()
        target = url('visit-create', patient_a, episode_id=episode_a.pk)

        assert logged.get(target).status_code == 302
        logged.post(target, {
            'professional': admin_user.professional_profile.pk, 'occurred_at': '2026-10-01T10:30',
        })
        assert not Visit.objects.filter(episode=episode_a).exists()

    def test_edit_a_visit(self, logged, admin_user, patient_a, visit_a):
        logged.post(url('visit-edit', patient_a, visit_id=visit_a.pk), {
            'professional': admin_user.professional_profile.pk, 'occurred_at': '2026-09-15T09:00',
        })

        visit_a.refresh_from_db()
        assert timezone.localtime(visit_a.occurred_at).strftime('%Y-%m-%d %H:%M') == '2026-09-15 09:00'

    def test_a_visit_with_a_signed_note_is_not_editable(
        self, logged, admin_user, patient_a, visit_a, signed_note_a
    ):
        before = visit_a.occurred_at
        target = url('visit-edit', patient_a, visit_id=visit_a.pk)

        assert logged.get(target).status_code == 302
        logged.post(target, {
            'professional': admin_user.professional_profile.pk, 'occurred_at': '2020-01-01T09:00',
        })
        visit_a.refresh_from_db()
        assert visit_a.occurred_at == before

    def test_another_patients_visit_is_404(self, logged, patient_a, other_patient):
        target = url('visit-edit', patient_a, visit_id=other_patient.test_visit.pk)

        assert logged.get(target).status_code == 404


# ---------------------------------------------------------------------------
# Notas: borrador → firma
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestNoteDraft:
    def test_the_form_uses_podiatry_labels_and_examples(self, logged, patient_a, visit_a):
        html = logged.get(url('note-create', patient_a, visit_id=visit_a.pk)).content.decode()

        for title in ('Motivo de consulta', 'Exploración', 'Diagnóstico', 'Tratamiento y plan'):
            assert title in html
        for label in ('Inspección', 'Palpación', 'Biomecánica y pisada', 'Pruebas complementarias',
                      'Tratamiento realizado hoy', 'Indicaciones y seguimiento'):
            assert label in html
        assert 'Dolor en el talón derecho al levantarse por la mañana' in html   # ejemplo
        assert 'Revisión en 4 semanas' in html

    def test_create_a_draft(self, logged, patient_a, visit_a):
        response = logged.post(url('note-create', patient_a, visit_id=visit_a.pk), NOTE)

        note = ClinicalNote.objects.get(visit=visit_a)
        assert note.status == ClinicalNote.Status.DRAFT
        assert note.subjective == NOTE['subjective']
        assert note.signed_by is None
        assert response['Location'] == url('note-detail', patient_a, note_id=note.pk)

    def test_who_wrote_the_draft_is_in_the_changelog(self, logged, admin_user, patient_a, visit_a):
        logged.post(url('note-create', patient_a, visit_id=visit_a.pk), NOTE)

        entry = ChangeLog.objects.filter(patient=patient_a, action=ChangeLog.Action.CREATE,
                                         model_label__icontains='clinicalnote').first()
        assert entry is not None
        assert entry.user == admin_user

    def test_the_changelog_never_stores_the_clinical_text(self, logged, patient_a, visit_a):
        logged.post(url('note-create', patient_a, visit_id=visit_a.pk), NOTE)

        dump = ' '.join(str(entry.changes) for entry in ChangeLog.objects.filter(patient=patient_a))
        assert 'Fascitis plantar' not in dump

    def test_an_empty_note_is_not_saved(self, logged, patient_a, visit_a):
        response = logged.post(url('note-create', patient_a, visit_id=visit_a.pk), {
            'subjective': '  ', 'inspection': '', 'assessment': '', 'follow_up': '',
        })

        assert response.status_code == 200
        assert not ClinicalNote.objects.filter(visit=visit_a).exists()

    def test_a_pending_draft_is_resumed_instead_of_opening_another(
        self, logged, patient_a, visit_a, draft_note_a
    ):
        response = logged.get(url('note-create', patient_a, visit_id=visit_a.pk))

        assert response['Location'] == url('note-edit', patient_a, note_id=draft_note_a.pk)

    def test_edit_a_draft(self, logged, patient_a, draft_note_a):
        logged.post(url('note-edit', patient_a, note_id=draft_note_a.pk), NOTE)

        draft_note_a.refresh_from_db()
        assert draft_note_a.assessment == 'Fascitis plantar derecha.'

    def test_discard_a_draft_is_a_soft_delete(self, logged, patient_a, draft_note_a):
        logged.post(url('note-delete', patient_a, note_id=draft_note_a.pk))

        assert not ClinicalNote.objects.filter(pk=draft_note_a.pk).exists()
        assert ClinicalNote.all_objects.filter(pk=draft_note_a.pk).exists()

    def test_closed_episode_takes_no_new_notes(self, logged, patient_a, episode_a, visit_a):
        episode_a.close()
        target = url('note-create', patient_a, visit_id=visit_a.pk)

        assert logged.get(target).status_code == 302
        logged.post(target, NOTE)
        assert not ClinicalNote.objects.filter(visit=visit_a).exists()


@pytest.mark.django_db
class TestSigning:
    def test_a_professional_signs_in_their_own_name(
        self, logged, admin_user, patient_a, draft_note_a
    ):
        logged.post(url('note-sign', patient_a, note_id=draft_note_a.pk))

        draft_note_a.refresh_from_db()
        assert draft_note_a.status == ClinicalNote.Status.SIGNED
        assert draft_note_a.signed_by == admin_user.professional_profile
        assert draft_note_a.content_hash.startswith('sha256:')

    def test_the_signer_cannot_be_chosen_from_the_form(
        self, logged, admin_user, patient_a, draft_note_a, clinic_a, staff_user
    ):
        other = staff_user.professional_profile
        logged.post(url('note-sign', patient_a, note_id=draft_note_a.pk), {'signed_by': other.pk})

        draft_note_a.refresh_from_db()
        assert draft_note_a.signed_by == admin_user.professional_profile

    def test_a_user_without_a_professional_profile_cannot_sign(
        self, staff_without_profile, patient_a, draft_note_a
    ):
        staff_without_profile.post(url('note-sign', patient_a, note_id=draft_note_a.pk))

        draft_note_a.refresh_from_db()
        assert draft_note_a.status == ClinicalNote.Status.DRAFT

    def test_but_they_can_prepare_a_draft(self, staff_without_profile, patient_a, visit_a):
        staff_without_profile.post(url('note-create', patient_a, visit_id=visit_a.pk), NOTE)

        assert ClinicalNote.objects.filter(visit=visit_a).exists()

    def test_an_empty_draft_is_not_signed(self, logged, patient_a, visit_a):
        note = ClinicalNote.objects.create(visit=visit_a)
        logged.post(url('note-sign', patient_a, note_id=note.pk))

        note.refresh_from_db()
        assert note.status == ClinicalNote.Status.DRAFT

    def test_the_detail_warns_that_it_cannot_be_edited_afterwards(self, logged, patient_a, draft_note_a):
        html = logged.get(url('note-detail', patient_a, note_id=draft_note_a.pk)).content.decode()

        assert 'no se podrá editar' in html.lower() or 'No se podrá editar' in html
        assert url('note-sign', patient_a, note_id=draft_note_a.pk) in html


@pytest.mark.django_db
class TestSignedNoteIsImmutable:
    """Ninguna vista acepta tocar una nota firmada, ni forzando el POST."""

    def test_edit_is_refused(self, logged, patient_a, signed_note_a):
        target = url('note-edit', patient_a, note_id=signed_note_a.pk)

        assert logged.get(target).status_code == 302
        logged.post(target, {**NOTE, 'assessment': 'Reescrito'})
        signed_note_a.refresh_from_db()
        assert signed_note_a.assessment == 'Callosidad plantar'

    def test_delete_is_refused(self, logged, patient_a, signed_note_a):
        logged.post(url('note-delete', patient_a, note_id=signed_note_a.pk))

        assert ClinicalNote.objects.filter(pk=signed_note_a.pk).exists()

    def test_signing_again_changes_nothing(self, logged, patient_a, signed_note_a):
        before = (signed_note_a.signed_at, signed_note_a.content_hash)
        logged.post(url('note-sign', patient_a, note_id=signed_note_a.pk))

        signed_note_a.refresh_from_db()
        assert (signed_note_a.signed_at, signed_note_a.content_hash) == before

    def test_the_signed_detail_offers_no_edit_sign_or_delete(self, logged, patient_a, signed_note_a):
        html = logged.get(url('note-detail', patient_a, note_id=signed_note_a.pk)).content.decode()

        for name in ('note-edit', 'note-sign', 'note-delete'):
            assert url(name, patient_a, note_id=signed_note_a.pk) not in html
        assert 'Firmada' in html

    def test_integrity_seal_is_checked(self, logged, patient_a, signed_note_a):
        html = logged.get(url('note-detail', patient_a, note_id=signed_note_a.pk)).content.decode()

        assert 'sello de integridad no cuadra' not in html


@pytest.mark.django_db
class TestAddenda:
    def test_a_professional_adds_an_addendum(self, logged, admin_user, patient_a, signed_note_a):
        logged.post(url('addendum-create', patient_a, note_id=signed_note_a.pk), {'text': 'Mejoría a los dos días.'})

        addendum = Addendum.objects.get(note=signed_note_a)
        assert addendum.text == 'Mejoría a los dos días.'
        assert addendum.author == admin_user.professional_profile
        signed_note_a.refresh_from_db()
        assert signed_note_a.assessment == 'Callosidad plantar'

    def test_it_shows_in_the_note(self, logged, patient_a, signed_note_a, professional_a):
        Addendum.objects.create(note=signed_note_a, author=professional_a, text='Texto de la adenda')
        html = logged.get(url('note-detail', patient_a, note_id=signed_note_a.pk)).content.decode()

        assert 'Texto de la adenda' in html

    def test_no_addenda_on_a_draft(self, logged, patient_a, draft_note_a):
        logged.post(url('addendum-create', patient_a, note_id=draft_note_a.pk), {'text': 'Cosa'})

        assert not Addendum.objects.filter(note=draft_note_a).exists()

    def test_a_user_without_profile_cannot_add_one(self, staff_without_profile, patient_a, signed_note_a):
        staff_without_profile.post(url('addendum-create', patient_a, note_id=signed_note_a.pk), {'text': 'Cosa'})

        assert not Addendum.objects.filter(note=signed_note_a).exists()

    def test_an_empty_addendum_is_rejected_and_the_rerender_is_logged(
        self, logged, patient_a, signed_note_a
    ):
        response = logged.post(url('addendum-create', patient_a, note_id=signed_note_a.pk), {'text': '  '})

        assert response.status_code == 200
        assert not Addendum.objects.filter(note=signed_note_a).exists()
        # Se vuelve a pintar la nota entera: es una lectura de texto clínico.
        assert AccessLog.objects.filter(patient=patient_a).exists()


# ---------------------------------------------------------------------------
# Aislamiento y lecturas
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestIsolationAndAudit:
    NOTE_VIEWS = ('note-detail', 'note-edit')
    NOTE_POSTS = ('note-sign', 'note-delete', 'addendum-create')

    def test_another_clinics_patient_is_404_everywhere(self, logged, patient_b):
        for name in ('tab-history', 'episode-create'):
            assert logged.get(url(name, patient_b)).status_code == 404
        assert not AccessLog.objects.filter(patient=patient_b).exists()

    @pytest.mark.parametrize('name', NOTE_VIEWS)
    def test_another_patients_note_is_404_on_get(self, logged, patient_a, other_patient, name):
        response = logged.get(url(name, patient_a, note_id=other_patient.test_note.pk))

        assert response.status_code == 404

    @pytest.mark.parametrize('name', NOTE_POSTS)
    def test_another_patients_note_is_404_on_post(self, logged, patient_a, other_patient, name):
        response = logged.post(url(name, patient_a, note_id=other_patient.test_note.pk), {'text': 'x'})

        assert response.status_code == 404
        other_patient.test_note.refresh_from_db()
        assert other_patient.test_note.status == ClinicalNote.Status.DRAFT

    def test_another_patients_visit_cannot_get_a_note(self, logged, patient_a, other_patient):
        response = logged.post(
            url('note-create', patient_a, visit_id=other_patient.test_visit.pk), NOTE
        )

        assert response.status_code == 404
        assert ClinicalNote.objects.filter(visit=other_patient.test_visit).count() == 1

    def test_anonymous_is_sent_to_login(self, client, patient_a, draft_note_a):
        response = client.get(url('note-detail', patient_a, note_id=draft_note_a.pk))

        assert response.status_code == 302 and 'login' in response['Location']

    @pytest.mark.parametrize('name', NOTE_VIEWS)
    def test_every_note_read_is_logged_against_the_note(
        self, logged, patient_a, draft_note_a, name
    ):
        response = logged.get(url(name, patient_a, note_id=draft_note_a.pk))

        assert response.status_code == 200
        entry = AccessLog.objects.get(patient=patient_a)
        assert entry.object_id == str(draft_note_a.pk)

    def test_the_agent_api_key_cannot_reach_these_views(self, api_client, patient_a, draft_note_a):
        """Sin API: una petición con `Api-Key` (sin sesión) va al login."""
        api_client.credentials(HTTP_AUTHORIZATION='Api-Key whatever')
        response = api_client.get(url('note-detail', patient_a, note_id=draft_note_a.pk))

        assert response.status_code in (302, 401, 403)
