from rest_framework import viewsets

from core.models import Clinic, User
from core.permissions import IsClinicAdminOrReadOnly, IsStaffOrAdmin
from core.serializers import ClinicSerializer, UserSerializer


class ClinicViewSet(viewsets.ModelViewSet):
    queryset = Clinic.objects.all()
    serializer_class = ClinicSerializer
    permission_classes = [IsClinicAdminOrReadOnly]
    search_fields = ['name', 'slug']


class UserViewSet(viewsets.ModelViewSet):
    serializer_class = UserSerializer
    permission_classes = [IsClinicAdminOrReadOnly]
    search_fields = ['email', 'first_name', 'last_name']

    def get_queryset(self):
        user = self.request.user
        queryset = User.objects.select_related('clinic')
        if user.is_superuser or not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)
