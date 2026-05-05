from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from core.models import Clinic


class ClinicAgent:
    is_authenticated = True
    is_superuser = False
    is_active = True
    is_staff = False
    role = 'agent'

    def __init__(self, clinic):
        self.clinic = clinic

    @property
    def clinic_id(self):
        return self.clinic.clinic_id

    def __str__(self):
        return f"ClinicAgent({self.clinic.clinic_id})"


class AgentClinicKeyAuthentication(BaseAuthentication):
    def authenticate(self, request):
        auth = request.META.get('HTTP_AUTHORIZATION', '').strip()
        parts = auth.split()
        if len(parts) != 2 or parts[0] != 'Api-Key':
            return None
        uuid_value = parts[1]
        try:
            clinic = Clinic.objects.get(agent_api_key=uuid_value)
        except Clinic.DoesNotExist:
            raise AuthenticationFailed('API key de clínica no válida.')
        except (ValueError, Exception):
            raise AuthenticationFailed('Formato de API key inválido.')
        return (ClinicAgent(clinic), clinic)
