from django.conf import settings
from rest_framework.permissions import BasePermission, SAFE_METHODS


class IsClinicAdminOrReadOnly(BasePermission):
    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return request.user and request.user.is_authenticated
        return request.user and request.user.is_authenticated and request.user.role == 'admin'


class IsStaffOrAdmin(BasePermission):
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated and request.user.role in {'admin', 'staff'}


class IsAgentClinicKey(BasePermission):
    """
    Concede acceso cuando request.user es una instancia de ClinicAgent.
    DELETE no está permitido: el agente sólo necesita GET, POST y PATCH.
    """

    def has_permission(self, request, view):
        from core.authentication import ClinicAgent
        return isinstance(request.user, ClinicAgent) and request.method != 'DELETE'


class IsAgentMasterKey(BasePermission):
    """
    Permite acceso GET cuando el header Authorization contiene
    'Api-Key <AGENT_MASTER_API_KEY>'. Cualquier otra combinación → 403.
    """

    def has_permission(self, request, view):
        if request.method not in SAFE_METHODS:
            return False
        master_key = getattr(settings, 'AGENT_MASTER_API_KEY', '').strip()
        if not master_key:
            return False
        auth = request.META.get('HTTP_AUTHORIZATION', '').strip()
        parts = auth.split()
        if len(parts) != 2 or parts[0] != 'Api-Key':
            return False
        return parts[1].strip() == master_key
