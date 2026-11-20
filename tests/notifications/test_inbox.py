"""`notify()`: destinatarios, preferencias, agrupación y enganches."""
import pytest

from agent.models import ChatMessage, ConversationSession
from agent.services import apply_delivery_status, record_message
from appointments.models import AppointmentStatusHistory
from appointments.services import cancel_appointment, notify_staff
from core.models import User
from notifications.inbox import notify
from notifications.models import Notification, NotificationPreference

Kind = Notification.Kind


def _run(callbacks, fn):
    with callbacks(execute=True):
        fn()


@pytest.fixture
def staff_b(db, clinic_b):
    return User.objects.create_user(
        email='staff@beta.test', password='x', clinic=clinic_b, role=User.Role.STAFF
    )


def test_notify_creates_one_row_per_user_of_the_clinic_only(
    admin_user, staff_user, staff_b, clinic_a, django_capture_on_commit_callbacks
):
    _run(django_capture_on_commit_callbacks, lambda: notify(clinic_a, Kind.CHAT_NEW, url='/chats/'))
    assert set(Notification.objects.values_list('user_id', flat=True)) == {admin_user.pk, staff_user.pk}
    assert not Notification.objects.filter(user=staff_b).exists()


def test_inactive_users_and_excluded_user_get_nothing(
    admin_user, staff_user, clinic_a, django_capture_on_commit_callbacks
):
    staff_user.is_active = False
    staff_user.save()
    _run(
        django_capture_on_commit_callbacks,
        lambda: notify(clinic_a, Kind.CHAT_NEW, url='/x/', exclude_user=admin_user),
    )
    assert Notification.objects.count() == 0


def test_in_app_preference_is_respected(admin_user, staff_user, clinic_a, django_capture_on_commit_callbacks):
    NotificationPreference.objects.create(user=staff_user, kind=Kind.CHAT_NEW, in_app=False)
    _run(django_capture_on_commit_callbacks, lambda: notify(clinic_a, Kind.CHAT_NEW, url='/x/'))
    assert list(Notification.objects.values_list('user_id', flat=True)) == [admin_user.pk]


def test_template_status_is_for_admins_only(admin_user, staff_user, clinic_a, django_capture_on_commit_callbacks):
    _run(django_capture_on_commit_callbacks, lambda: notify(clinic_a, Kind.TEMPLATE_STATUS, url='/x/'))
    assert list(Notification.objects.values_list('user_id', flat=True)) == [admin_user.pk]


def test_unread_same_object_is_grouped(admin_user, clinic_a, django_capture_on_commit_callbacks):
    for _ in range(3):
        _run(
            django_capture_on_commit_callbacks,
            lambda: notify(clinic_a, Kind.CHAT_MESSAGE, url='/c/', object_id='abc'),
        )
    row = Notification.objects.get()
    assert row.count == 3
    # Tras leerla, el siguiente aviso abre una fila nueva.
    from django.utils import timezone

    Notification.objects.update(read_at=timezone.now())
    _run(
        django_capture_on_commit_callbacks,
        lambda: notify(clinic_a, Kind.CHAT_MESSAGE, url='/c/', object_id='abc'),
    )
    assert Notification.objects.count() == 2


def test_texts_never_carry_patient_data(admin_user, clinic_a, patient_a, django_capture_on_commit_callbacks):
    _run(django_capture_on_commit_callbacks, lambda: notify(clinic_a, Kind.APPOINTMENT_PENDING, url='/x/'))
    row = Notification.objects.get()
    text = f'{row.title} {row.body}'
    assert patient_a.first_name not in text and patient_a.phone not in text


class TestHooks:
    def test_inbound_message_notifies_and_new_session_is_chat_new(
        self, admin_user, clinic_a, django_capture_on_commit_callbacks
    ):
        def go():
            record_message(
                clinic=clinic_a, phone='+34600111222', direction=ChatMessage.Direction.INBOUND,
                sender=ChatMessage.Sender.PATIENT, body='Hola, me duele mucho', wa_message_id='wamid.1',
            )

        _run(django_capture_on_commit_callbacks, go)
        row = Notification.objects.get()
        assert row.kind == Kind.CHAT_NEW
        assert 'duele' not in row.title + row.body

        session = ConversationSession.objects.get()
        _run(
            django_capture_on_commit_callbacks,
            lambda: record_message(
                clinic=clinic_a, session=session, direction=ChatMessage.Direction.INBOUND,
                sender=ChatMessage.Sender.PATIENT, body='otra', wa_message_id='wamid.2',
            ),
        )
        assert set(Notification.objects.values_list('kind', flat=True)) == {Kind.CHAT_NEW, Kind.CHAT_MESSAGE}

    def test_duplicate_wa_message_id_does_not_notify_twice(
        self, admin_user, clinic_a, django_capture_on_commit_callbacks
    ):
        def go():
            for _ in range(2):
                record_message(
                    clinic=clinic_a, phone='+34600111222', direction=ChatMessage.Direction.INBOUND,
                    sender=ChatMessage.Sender.PATIENT, body='x', wa_message_id='wamid.dup',
                )

        _run(django_capture_on_commit_callbacks, go)
        assert Notification.objects.get().count == 1

    def test_test_session_and_outbound_do_not_notify(self, admin_user, clinic_a, django_capture_on_commit_callbacks):
        session = ConversationSession.objects.create(clinic=clinic_a, phone='panel-test', is_test=True)

        def go():
            record_message(
                clinic=clinic_a, session=session, direction=ChatMessage.Direction.INBOUND,
                sender=ChatMessage.Sender.PATIENT, body='x',
            )
            record_message(
                clinic=clinic_a, phone='+34600999888', direction=ChatMessage.Direction.OUTBOUND,
                sender=ChatMessage.Sender.STAFF, body='x',
            )

        _run(django_capture_on_commit_callbacks, go)
        assert Notification.objects.count() == 0

    def test_failed_delivery_notifies(self, admin_user, clinic_a, django_capture_on_commit_callbacks):
        session = ConversationSession.objects.create(clinic=clinic_a, phone='+34600111222')
        record_message(
            clinic=clinic_a, session=session, direction=ChatMessage.Direction.OUTBOUND,
            sender=ChatMessage.Sender.STAFF, body='x', status=ChatMessage.Status.SENT, wa_message_id='wamid.O',
        )
        _run(
            django_capture_on_commit_callbacks,
            lambda: apply_delivery_status(
                clinic=clinic_a, wa_message_id='wamid.O', status=ChatMessage.Status.FAILED, error='boom'
            ),
        )
        assert Notification.objects.get().kind == Kind.MESSAGE_FAILED

    @pytest.mark.parametrize(
        'actor,expected',
        [
            (AppointmentStatusHistory.Actor.AGENT, 1),
            (AppointmentStatusHistory.Actor.PATIENT, 1),
            (AppointmentStatusHistory.Actor.STAFF, 0),
            (AppointmentStatusHistory.Actor.SYSTEM, 0),
        ],
    )
    def test_cancellation_notifies_only_for_agent_or_patient(
        self, admin_user, appointment_a, actor, expected, django_capture_on_commit_callbacks
    ):
        _run(
            django_capture_on_commit_callbacks,
            lambda: cancel_appointment(appointment_a, actor=actor, notify=False),
        )
        assert Notification.objects.filter(kind=Kind.APPOINTMENT_CANCELLED).count() == expected

    def test_notify_staff_links_to_the_appointment(self, admin_user, appointment_a, django_capture_on_commit_callbacks):
        _run(django_capture_on_commit_callbacks, lambda: notify_staff(appointment_a, 'appointment_pending'))
        assert str(appointment_a.pk) in Notification.objects.get().url


class TestNamedDetail:
    """El nombre va en `detail` (solo panel); título/cuerpo —lo que viaja en el push— siguen genéricos."""

    def test_cancellation_by_the_agent_names_the_patient(
        self, admin_user, appointment_a, django_capture_on_commit_callbacks
    ):
        _run(
            django_capture_on_commit_callbacks,
            lambda: cancel_appointment(
                appointment_a, actor=AppointmentStatusHistory.Actor.AGENT, notify=False
            ),
        )
        row = Notification.objects.get(kind=Kind.APPOINTMENT_CANCELLED)
        assert row.detail == f'{appointment_a.patient} ha cancelado su cita por el agente'
        assert appointment_a.patient.first_name not in f'{row.title} {row.body}'

    def test_inbound_message_names_the_sender_but_not_the_content(
        self, admin_user, clinic_a, patient_a, django_capture_on_commit_callbacks
    ):
        session = ConversationSession.objects.create(clinic=clinic_a, phone=patient_a.phone, patient=patient_a)
        _run(
            django_capture_on_commit_callbacks,
            lambda: record_message(
                clinic=clinic_a, session=session, direction=ChatMessage.Direction.INBOUND,
                sender=ChatMessage.Sender.PATIENT, body='me duele el pie', wa_message_id='wamid.N',
            ),
        )
        row = Notification.objects.get()
        assert row.detail == f'{patient_a} te ha enviado un mensaje'
        assert 'duele' not in row.detail

    def test_push_payload_does_not_carry_the_name(self, admin_user, clinic_a, django_capture_on_commit_callbacks):
        from notifications.push import build_payload

        _run(
            django_capture_on_commit_callbacks,
            lambda: notify(clinic_a, Kind.CHAT_NEW, url='/x/', detail='María ha escrito por primera vez'),
        )
        row = Notification.objects.get()
        assert row.detail == 'María ha escrito por primera vez'
        assert 'María' not in str(build_payload(row))

    def test_grouped_row_keeps_the_latest_detail(self, admin_user, clinic_a, django_capture_on_commit_callbacks):
        for who in ('Ana', 'Ana'):
            _run(
                django_capture_on_commit_callbacks,
                lambda: notify(clinic_a, Kind.CHAT_MESSAGE, url='/c/', object_id='s', detail=f'{who} te ha enviado un mensaje'),
            )
        row = Notification.objects.get()
        assert row.count == 2 and row.detail == 'Ana te ha enviado un mensaje'
