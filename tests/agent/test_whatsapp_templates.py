"""Plantillas de WhatsApp: reglas del texto, pestaña `/agente/plantillas/`,
envío a revisión y sincronización de estados con Meta (la Graph API se simula).
"""
import pytest
from django.urls import reverse

from agent import whatsapp
from agent.models import WhatsAppTemplate
from audit.models import ChangeLog

Status = WhatsAppTemplate.Status


def _payload(**overrides):
    data = {
        'name': 'Recordatorio de cita',
        'language': 'es',
        'category': 'UTILITY',
        'header_text': '',
        'body': 'Hola {{1}}, te recordamos tu cita del {{2}}. Responde si necesitas cambiarla.',
        'footer': '',
        'examples': ['Ana', '12 de mayo'],
    }
    data.update(overrides)
    return data


@pytest.fixture
def connected(clinic_a):
    clinic_a.whatsapp_phone_number_id = '111'
    clinic_a.whatsapp_token = 'EAAG-secreto'
    clinic_a.whatsapp_business_account_id = '999'
    clinic_a.save()
    return clinic_a


@pytest.fixture
def template_a(clinic_a):
    return WhatsAppTemplate.objects.create(
        clinic=clinic_a,
        name='recordatorio',
        body='Hola {{1}}, mañana tienes cita en la clínica.',
        body_examples=['Ana'],
    )


# ---------------------------------------------------------------------------
# Modelo
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestModel:
    def test_variables_are_detected_once_and_sorted(self):
        assert WhatsAppTemplate.variables_in('{{2}} y {{ 1 }} y otra vez {{2}}') == [1, 2]

    def test_preview_fills_examples(self, template_a):
        assert template_a.preview_body() == 'Hola Ana, mañana tienes cita en la clínica.'

    def test_only_draft_and_rejected_are_editable(self, template_a):
        for status, editable in [
            (Status.DRAFT, True), (Status.REJECTED, True), (Status.PENDING, False),
            (Status.APPROVED, False), (Status.PAUSED, False), (Status.DISABLED, False),
        ]:
            template_a.status = status
            assert template_a.is_editable is editable

    def test_imported_with_unsupported_components_is_read_only(self, template_a):
        template_a.has_unsupported_components = True
        assert not template_a.is_editable

    def test_only_unsent_drafts_can_be_deleted(self, template_a):
        assert template_a.is_deletable
        template_a.meta_template_id = '55'
        assert not template_a.is_deletable

    def test_changes_are_audited(self, template_a):
        assert ChangeLog.objects.filter(object_id=str(template_a.pk)).exists()


# ---------------------------------------------------------------------------
# Formulario
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCreate:
    url_name = 'agent_settings:template-create'

    def test_creates_a_draft_with_normalized_name(self, client, admin_user, clinic_a):
        client.force_login(admin_user)

        response = client.post(reverse(self.url_name), _payload(name='Recordatorio de Cítá'))

        template = WhatsAppTemplate.objects.get()
        assert response.status_code == 302
        assert template.name == 'recordatorio_de_cita'
        assert template.clinic == clinic_a
        assert template.status == Status.DRAFT
        assert template.body_examples == ['Ana', '12 de mayo']
        assert template.updated_by == admin_user

    @pytest.mark.parametrize('body, message', [
        ('Hola {{2}}, aquí tienes tu cita completa.', 'seguidas desde'),
        ('{{1}} te esperamos en la clínica mañana.', 'empezar ni acabar'),
        ('Te esperamos mañana en la clínica, {{1}}', 'empezar ni acabar'),
    ])
    def test_rejects_bodies_meta_would_refuse(self, client, admin_user, body, message):
        client.force_login(admin_user)

        response = client.post(reverse(self.url_name), _payload(body=body, examples=['a', 'b']))

        assert response.status_code == 200
        assert message in ' '.join(response.context['form'].errors['body'])
        assert not WhatsAppTemplate.objects.exists()

    def test_every_variable_needs_an_example(self, client, admin_user):
        client.force_login(admin_user)

        response = client.post(reverse(self.url_name), _payload(examples=['Ana', '  ']))

        assert response.status_code == 200
        assert 'ejemplo para cada variable' in ' '.join(response.context['form'].non_field_errors())

    def test_header_and_footer_do_not_take_variables(self, client, admin_user):
        client.force_login(admin_user)

        response = client.post(reverse(self.url_name), _payload(header_text='Hola {{1}}', footer='{{1}}'))

        assert 'header_text' in response.context['form'].errors
        assert 'footer' in response.context['form'].errors

    def test_name_is_unique_per_language(self, client, admin_user, template_a):
        client.force_login(admin_user)

        same = client.post(reverse(self.url_name), _payload(name='recordatorio'))
        other_language = client.post(reverse(self.url_name), _payload(name='recordatorio', language='ca'))

        assert 'name' in same.context['form'].errors
        assert other_language.status_code == 302

    def test_no_examples_needed_without_variables(self, client, admin_user):
        client.force_login(admin_user)

        response = client.post(
            reverse(self.url_name),
            _payload(body='Tu cita está confirmada. Te esperamos en la clínica.', examples=[]),
        )

        assert response.status_code == 302
        assert WhatsAppTemplate.objects.get().body_examples == []


@pytest.mark.django_db
class TestEdit:
    def test_edit_saves_changes(self, client, admin_user, template_a):
        client.force_login(admin_user)

        client.post(
            reverse('agent_settings:template-edit', args=[template_a.pk]),
            _payload(name='recordatorio', body='Hola {{1}}, tu cita es mañana en la clínica.', examples=['Luis']),
        )

        template_a.refresh_from_db()
        assert template_a.body == 'Hola {{1}}, tu cita es mañana en la clínica.'
        assert template_a.body_examples == ['Luis']

    def test_fixing_a_rejected_template_makes_it_a_draft_again(self, client, admin_user, template_a):
        template_a.status = Status.REJECTED
        template_a.rejection_reason = 'Contenido promocional'
        template_a.meta_template_id = '55'
        template_a.save()
        client.force_login(admin_user)

        client.post(
            reverse('agent_settings:template-edit', args=[template_a.pk]),
            # Nombre e idioma ya son identidad en Meta: lo que se mande se ignora.
            _payload(name='otro_nombre', language='ca', body='Hola {{1}}, tu cita es mañana en la clínica.', examples=['Luis']),
        )

        template_a.refresh_from_db()
        assert template_a.status == Status.DRAFT
        assert template_a.rejection_reason == ''
        assert (template_a.name, template_a.language) == ('recordatorio', 'es')

    @pytest.mark.parametrize('status', [Status.PENDING, Status.APPROVED])
    def test_locked_template_cannot_be_modified(self, client, admin_user, template_a, status):
        template_a.status = status
        template_a.save()
        client.force_login(admin_user)
        url = reverse('agent_settings:template-edit', args=[template_a.pk])

        page = client.get(url)
        client.post(url, _payload(name='recordatorio', body='Otro texto distinto para la clínica.', examples=[]))

        assert page.context['readonly'] is True
        template_a.refresh_from_db()
        assert template_a.body.startswith('Hola')

    def test_other_clinics_template_is_not_reachable(self, client, admin_user_b, template_a):
        client.force_login(admin_user_b)

        assert client.get(reverse('agent_settings:template-edit', args=[template_a.pk])).status_code == 404

    def test_duplicate_makes_an_independent_draft(self, client, admin_user, template_a):
        template_a.status = Status.APPROVED
        template_a.meta_template_id = '55'
        template_a.save()
        client.force_login(admin_user)

        client.post(reverse('agent_settings:template-duplicate', args=[template_a.pk]))
        client.post(reverse('agent_settings:template-duplicate', args=[template_a.pk]))

        copies = WhatsAppTemplate.objects.exclude(pk=template_a.pk).order_by('name')
        assert [c.name for c in copies] == ['recordatorio_copia', 'recordatorio_copia2']
        assert all(c.status == Status.DRAFT and not c.meta_template_id for c in copies)


# ---------------------------------------------------------------------------
# Listado y permisos
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestList:
    url = '/agente/plantillas/'

    def test_lists_only_own_clinic_with_counts(self, client, admin_user, template_a, clinic_b):
        WhatsAppTemplate.objects.create(clinic=clinic_b, name='ajena', body='Texto de otra clínica distinta.')
        WhatsAppTemplate.objects.create(
            clinic=template_a.clinic, name='aprobada', body='Texto aprobado para la clínica.', status=Status.APPROVED
        )
        client.force_login(admin_user)

        response = client.get(self.url)

        assert response.status_code == 200
        assert sorted(t.name for t in response.context['templates']) == ['aprobada', 'recordatorio']
        counts = {f['value']: f['count'] for f in response.context['filters']}
        assert counts == {'': 2, 'draft': 1, 'pending': 0, 'approved': 1, 'rejected': 0}
        assert b'ajena' not in response.content

    def test_filters_by_status(self, client, admin_user, template_a):
        WhatsAppTemplate.objects.create(
            clinic=template_a.clinic, name='aprobada', body='Texto aprobado para la clínica.', status=Status.APPROVED
        )
        client.force_login(admin_user)

        response = client.get(self.url, {'estado': 'approved'})

        assert [t.name for t in response.context['templates']] == ['aprobada']

    def test_staff_is_denied_everywhere(self, client, staff_user, template_a):
        client.force_login(staff_user)
        for name, args in [
            ('templates', []), ('template-create', []), ('template-edit', [template_a.pk]),
        ]:
            assert client.get(reverse(f'agent_settings:{name}', args=args)).status_code == 403
        for name, args in [
            ('template-sync', []), ('template-submit', [template_a.pk]),
            ('template-delete', [template_a.pk]), ('template-duplicate', [template_a.pk]),
        ]:
            assert client.post(reverse(f'agent_settings:{name}', args=args)).status_code == 403

    def test_actions_reject_get(self, client, admin_user, template_a):
        client.force_login(admin_user)
        assert client.get(reverse('agent_settings:template-delete', args=[template_a.pk])).status_code == 405

    def test_delete_only_unsent_drafts(self, client, admin_user, template_a):
        client.force_login(admin_user)
        sent = WhatsAppTemplate.objects.create(
            clinic=template_a.clinic, name='enviada', body='Texto ya enviado a Meta para la clínica.',
            status=Status.PENDING, meta_template_id='7',
        )

        client.post(reverse('agent_settings:template-delete', args=[sent.pk]))
        client.post(reverse('agent_settings:template-delete', args=[template_a.pk]))

        assert list(WhatsAppTemplate.objects.values_list('name', flat=True)) == ['enviada']


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSubmit:
    def test_creates_the_template_in_meta_and_goes_pending(self, client, admin_user, connected, template_a, monkeypatch):
        sent = {}

        def fake_create(clinic, payload):
            sent['payload'] = payload
            return {'id': '777', 'status': 'PENDING', 'category': 'UTILITY'}

        monkeypatch.setattr(whatsapp, 'create_template', fake_create)
        client.force_login(admin_user)

        client.post(reverse('agent_settings:template-submit', args=[template_a.pk]))

        template_a.refresh_from_db()
        assert (template_a.status, template_a.meta_template_id) == (Status.PENDING, '777')
        assert template_a.submitted_at is not None
        assert sent['payload']['name'] == 'recordatorio'
        assert sent['payload']['components'] == [
            {'type': 'BODY', 'text': template_a.body, 'example': {'body_text': [['Ana']]}},
        ]

    def test_resubmitting_a_rejected_one_updates_the_same_template(self, client, admin_user, connected, template_a, monkeypatch):
        template_a.meta_template_id = '55'
        template_a.status = Status.REJECTED
        template_a.save()
        calls = []
        monkeypatch.setattr(whatsapp, 'update_template', lambda c, tid, p: calls.append(tid) or {'success': True})
        monkeypatch.setattr(whatsapp, 'create_template', lambda *a: pytest.fail('no debe crear otra'))
        client.force_login(admin_user)

        client.post(reverse('agent_settings:template-submit', args=[template_a.pk]))

        template_a.refresh_from_db()
        assert calls == ['55']
        assert template_a.status == Status.PENDING

    def test_meta_refusal_changes_nothing_and_shows_the_reason(self, client, admin_user, connected, template_a, monkeypatch):
        def refuse(clinic, payload):
            raise whatsapp.WhatsAppError('WhatsApp devolvió un error: nombre repetido')

        monkeypatch.setattr(whatsapp, 'create_template', refuse)
        client.force_login(admin_user)

        response = client.post(reverse('agent_settings:template-submit', args=[template_a.pk]), follow=True)

        template_a.refresh_from_db()
        assert template_a.status == Status.DRAFT
        assert 'nombre repetido' in ' '.join(str(m) for m in response.context['messages'])

    def test_cannot_submit_an_approved_template(self, client, admin_user, connected, template_a, monkeypatch):
        template_a.status = Status.APPROVED
        template_a.save()
        monkeypatch.setattr(whatsapp, 'create_template', lambda *a: pytest.fail('no debe llamar a Meta'))
        client.force_login(admin_user)

        client.post(reverse('agent_settings:template-submit', args=[template_a.pk]))

        template_a.refresh_from_db()
        assert template_a.status == Status.APPROVED

    def test_without_waba_id_the_error_is_explained(self, clinic_a):
        clinic_a.whatsapp_token = 'x'
        clinic_a.whatsapp_phone_number_id = '1'
        with pytest.raises(whatsapp.WhatsAppError, match='cuenta de WhatsApp Business'):
            whatsapp.create_template(clinic_a, {})


@pytest.mark.django_db
class TestSync:
    def _remote(self, **overrides):
        item = {
            'id': '55', 'name': 'recordatorio', 'language': 'es', 'status': 'APPROVED',
            'category': 'UTILITY', 'components': [{'type': 'BODY', 'text': 'x'}],
        }
        item.update(overrides)
        return item

    def test_updates_status_and_rejection_reason(self, client, admin_user, connected, template_a, monkeypatch):
        template_a.status = Status.PENDING
        template_a.meta_template_id = '55'
        template_a.save()
        monkeypatch.setattr(
            whatsapp, 'list_templates',
            lambda clinic: [self._remote(status='REJECTED', rejected_reason='INVALID_FORMAT')],
        )
        client.force_login(admin_user)

        client.post(reverse('agent_settings:template-sync'))

        template_a.refresh_from_db()
        assert template_a.status == Status.REJECTED
        assert template_a.rejection_reason == 'INVALID_FORMAT'
        assert template_a.status_synced_at is not None

    def test_a_local_draft_is_never_overwritten(self, connected, template_a, monkeypatch):
        from agent import wa_templates
        template_a.meta_template_id = '55'  # rechazada y corregida: borrador con id de Meta
        template_a.save()
        monkeypatch.setattr(whatsapp, 'list_templates', lambda c: [self._remote(status='REJECTED')])

        assert wa_templates.sync_templates(connected) == (0, 0)
        template_a.refresh_from_db()
        assert template_a.status == Status.DRAFT

    def test_imports_templates_that_only_exist_in_meta(self, connected, monkeypatch):
        from agent import wa_templates
        remote = [
            self._remote(
                id='1', name='con_botones', components=[
                    {'type': 'BODY', 'text': 'Hola {{1}}, ¿vienes?', 'example': {'body_text': [['Ana']]}},
                    {'type': 'BUTTONS', 'buttons': [{'type': 'QUICK_REPLY', 'text': 'Sí'}]},
                ],
            ),
            self._remote(
                id='2', name='simple', status='PENDING', components=[
                    {'type': 'HEADER', 'format': 'TEXT', 'text': 'Cita'},
                    {'type': 'BODY', 'text': 'Cuerpo'},
                    {'type': 'FOOTER', 'text': 'Pie'},
                ],
            ),
            self._remote(id='3', name='otro_idioma', language='fr'),
            self._remote(id='4', name='raro', status='ALGO_NUEVO'),
        ]
        monkeypatch.setattr(whatsapp, 'list_templates', lambda c: remote)

        assert wa_templates.sync_templates(connected) == (0, 2)

        buttons, simple = WhatsAppTemplate.objects.order_by('name')
        assert buttons.has_unsupported_components and not buttons.is_editable
        assert buttons.body_examples == ['Ana']
        assert (simple.header_text, simple.body, simple.footer) == ('Cita', 'Cuerpo', 'Pie')
        assert simple.status == Status.PENDING and simple.is_editable is False

    def test_meta_error_is_shown_not_raised(self, client, admin_user, connected, monkeypatch):
        def down(clinic):
            raise whatsapp.WhatsAppError('No se pudo contactar con WhatsApp.')

        monkeypatch.setattr(whatsapp, 'list_templates', down)
        client.force_login(admin_user)

        response = client.post(reverse('agent_settings:template-sync'), follow=True)

        assert 'No se pudo contactar' in ' '.join(str(m) for m in response.context['messages'])


@pytest.mark.django_db
class TestWabaField:
    def test_config_form_saves_it_and_rejects_non_digits(self, client, admin_user, clinic_a):
        client.force_login(admin_user)
        url = reverse('agent_settings:config')

        bad = client.post(url, {'form': 'meta', 'whatsapp_business_account_id': '12/../x'})
        good = client.post(url, {'form': 'meta', 'whatsapp_business_account_id': ' 4455 '})

        assert bad.status_code == 200
        assert good.status_code == 302
        clinic_a.refresh_from_db()
        assert clinic_a.whatsapp_business_account_id == '4455'


# ---------------------------------------------------------------------------
# Enviar una plantilla a un paciente (ventana de 24 h cerrada)
# ---------------------------------------------------------------------------

from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

from agent.models import ChatMessage, ConversationSession
from agent.services import record_message, sendable_templates, send_staff_template


@pytest.fixture
def approved(clinic_a):
    return WhatsAppTemplate.objects.create(
        clinic=clinic_a,
        name='recordatorio_cita',
        header_text='Tu cita',
        body='Hola {{1}}, te esperamos el {{2}} en la clínica.',
        footer='Clinic Alpha',
        body_examples=['Ana', 'lunes'],
        status=Status.APPROVED,
        meta_template_id='55',
    )


def _session(clinic, patient, *, open_window):
    session = ConversationSession.objects.create(clinic=clinic, phone=patient.phone, patient=patient)
    record_message(
        clinic=clinic, session=session,
        direction=ChatMessage.Direction.INBOUND, sender=ChatMessage.Sender.PATIENT, body='Hola',
    )
    if not open_window:
        session.last_interaction = timezone.now() - timedelta(hours=30)
        session.save(update_fields=['last_interaction'])
    session.refresh_from_db()
    return session


@pytest.fixture
def closed_session(clinic_a, patient_a):
    return _session(clinic_a, patient_a, open_window=False)


@pytest.mark.django_db
class TestSendStaffTemplate:
    def test_sends_and_records_the_filled_text(self, closed_session, approved):
        with patch('agent.services.send_template', return_value='wamid.T1') as send:
            message = send_staff_template(session=closed_session, template=approved, params=['Ana', 'el lunes'])

        send.assert_called_once_with(closed_session.clinic, closed_session.phone, 'recordatorio_cita', 'es', ['Ana', 'el lunes'])
        assert message.message_type == ChatMessage.MessageType.TEMPLATE
        assert message.sender == ChatMessage.Sender.STAFF
        assert (message.status, message.wa_message_id) == (ChatMessage.Status.SENT, 'wamid.T1')
        assert message.body == 'Tu cita\n\nHola Ana, te esperamos el el lunes en la clínica.\n\nClinic Alpha'
        assert message.raw == {'template': 'recordatorio_cita', 'language': 'es', 'params': ['Ana', 'el lunes']}
        closed_session.refresh_from_db()
        assert closed_session.last_staff_message_at is not None  # aparta al agente, como el texto

    def test_values_are_flattened_to_one_line(self, closed_session, approved):
        """Meta rechaza saltos de línea y espacios repetidos dentro de una variable."""
        with patch('agent.services.send_template', return_value='w') as send:
            send_staff_template(session=closed_session, template=approved, params=['Ana\n  María', 'lunes'])

        assert send.call_args.args[-1] == ['Ana María', 'lunes']

    def test_meta_failure_leaves_a_failed_message(self, closed_session, approved):
        with patch('agent.services.send_template', side_effect=whatsapp.WhatsAppError('plantilla pausada')):
            with pytest.raises(whatsapp.WhatsAppError):
                send_staff_template(session=closed_session, template=approved, params=['Ana', 'lunes'])

        message = closed_session.messages.get(message_type='template')
        assert message.status == ChatMessage.Status.FAILED
        assert message.error_message == 'plantilla pausada'

    @pytest.mark.parametrize('params', [[], ['Ana'], ['Ana', '  '], ['Ana', 'x' * 201]])
    def test_every_variable_needs_a_reasonable_value(self, closed_session, approved, params):
        with patch('agent.services.send_template') as send:
            with pytest.raises(ValueError):
                send_staff_template(session=closed_session, template=approved, params=params)

        send.assert_not_called()
        assert not closed_session.messages.filter(message_type='template').exists()

    @pytest.mark.parametrize('status', [Status.DRAFT, Status.PENDING, Status.REJECTED, Status.PAUSED, Status.DISABLED])
    def test_only_approved_templates_go_out(self, closed_session, approved, status):
        approved.status = status
        approved.save()

        with patch('agent.services.send_template') as send:
            with pytest.raises(ValueError, match='aprobadas'):
                send_staff_template(session=closed_session, template=approved, params=['Ana', 'lunes'])
        send.assert_not_called()

    def test_template_with_unsupported_components_is_not_sent(self, closed_session, approved):
        approved.has_unsupported_components = True
        approved.save()

        with pytest.raises(ValueError):
            send_staff_template(session=closed_session, template=approved, params=['Ana', 'lunes'])

    def test_another_clinics_template_is_refused(self, closed_session, clinic_b):
        foreign = WhatsAppTemplate.objects.create(
            clinic=clinic_b, name='ajena', body='Texto aprobado de otra clínica distinta.', status=Status.APPROVED,
        )
        with pytest.raises(ValueError, match='no pertenece'):
            send_staff_template(session=closed_session, template=foreign, params=[])


@pytest.mark.django_db
class TestCloudApiPayload:
    def test_template_payload(self, clinic_a):
        with patch('agent.whatsapp._post_message', return_value='{"messages": [{"id": "wamid.X"}]}') as post:
            wamid = whatsapp.send_template(clinic_a, '34600111222', 'recordatorio_cita', 'es', ['Ana', 'lunes'])

        assert wamid == 'wamid.X'
        assert post.call_args.args[1] == {
            'messaging_product': 'whatsapp',
            'recipient_type': 'individual',
            'to': '34600111222',
            'type': 'template',
            'template': {
                'name': 'recordatorio_cita',
                'language': {'code': 'es'},
                'components': [{'type': 'body', 'parameters': [
                    {'type': 'text', 'text': 'Ana'}, {'type': 'text', 'text': 'lunes'},
                ]}],
            },
        }

    def test_no_components_without_variables(self, clinic_a):
        with patch('agent.whatsapp._post_message', return_value='{}') as post:
            whatsapp.send_template(clinic_a, '34600111222', 'sin_variables', 'es')

        assert 'components' not in post.call_args.args[1]['template']


@pytest.mark.django_db
class TestComposer:
    def _thread(self, session):
        return reverse('agent:chat-thread', args=[session.id])

    def test_closed_window_offers_only_approved_templates_of_the_clinic(
        self, client, admin_user, closed_session, approved, clinic_b
    ):
        WhatsAppTemplate.objects.create(
            clinic=approved.clinic, name='borrador', body='Todavía sin enviar a Meta ni nada.',
        )
        WhatsAppTemplate.objects.create(
            clinic=clinic_b, name='ajena', body='Aprobada pero de otra clínica distinta.', status=Status.APPROVED,
        )
        client.force_login(admin_user)

        response = client.get(self._thread(closed_session))
        html = response.content.decode()

        assert [t['name'] for t in response.context['send_templates']] == ['recordatorio_cita']
        assert 'Puedes enviar:' in html and 'No puedes enviar:' in html
        assert 'Enviar plantilla' in html
        assert reverse('agent:chat-send-template', args=[closed_session.id]) in html
        assert 'name="body"' not in html  # sin cuadro de texto libre

    def test_open_window_shows_the_text_composer_and_what_is_allowed(
        self, client, admin_user, clinic_a, patient_a, approved
    ):
        session = _session(clinic_a, patient_a, open_window=True)
        client.force_login(admin_user)

        response = client.get(self._thread(session))
        html = response.content.decode()

        assert response.context['send_templates'] == []
        assert 'name="body"' in html
        assert 'JPEG o PNG' in html and 'solo podrás enviar plantillas aprobadas' in html

    def test_without_approved_templates_an_admin_is_pointed_to_the_tab(self, client, admin_user, closed_session):
        client.force_login(admin_user)

        html = client.get(self._thread(closed_session)).content.decode()

        assert 'ninguna plantilla aprobada' in html
        assert reverse('agent_settings:templates') in html

    def test_staff_is_told_to_ask_an_admin(self, client, staff_user, closed_session):
        client.force_login(staff_user)

        html = client.get(self._thread(closed_session)).content.decode()

        assert 'Pide a un administrador' in html
        assert reverse('agent_settings:templates') not in html

    def test_post_sends_the_template(self, client, staff_user, closed_session, approved):
        client.force_login(staff_user)
        url = reverse('agent:chat-send-template', args=[closed_session.id])

        with patch('agent.services.send_template', return_value='wamid.V') as send:
            response = client.post(url, {'template': approved.pk, 'params': ['Ana', 'lunes']}, follow=True)

        assert response.redirect_chain[-1][0] == self._thread(closed_session)
        send.assert_called_once()
        assert closed_session.messages.filter(message_type='template', status='sent').count() == 1
        assert 'Plantilla enviada' in ' '.join(str(m) for m in response.context['messages'])

    def test_post_errors_come_back_as_messages(self, client, staff_user, closed_session, approved):
        client.force_login(staff_user)
        url = reverse('agent:chat-send-template', args=[closed_session.id])

        with patch('agent.services.send_template') as send:
            missing = client.post(url, {'template': approved.pk, 'params': ['Ana']}, follow=True)
            nothing = client.post(url, {'params': ['Ana']}, follow=True)
            garbage = client.post(url, {'template': 'abc'}, follow=True)

        send.assert_not_called()
        assert 'Rellena todos los campos' in ' '.join(str(m) for m in missing.context['messages'])
        assert 'Elige una plantilla' in ' '.join(str(m) for m in nothing.context['messages'])
        assert 'Elige una plantilla' in ' '.join(str(m) for m in garbage.context['messages'])

    def test_cannot_use_a_template_of_another_clinic(self, client, admin_user_b, closed_session, approved):
        client.force_login(admin_user_b)

        # El hilo es de la clínica A: para B ni siquiera existe.
        response = client.post(
            reverse('agent:chat-send-template', args=[closed_session.id]),
            {'template': approved.pk, 'params': ['Ana', 'lunes']},
        )

        assert response.status_code == 404

    def test_sendable_templates_is_empty_with_open_window(self, clinic_a, patient_a, approved):
        assert sendable_templates(_session(clinic_a, patient_a, open_window=True)) == []
        assert sendable_templates(None) == []
