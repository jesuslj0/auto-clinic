import django_filters

from agent.models import ConversationSession
from patients.filters import PhoneExactFilter


class ConversationSessionFilter(django_filters.FilterSet):
    phone = PhoneExactFilter(field_name='phone', lookup_expr='exact')

    class Meta:
        model = ConversationSession
        fields = ['clinic', 'phone', 'agent_paused']
