from rest_framework import status, viewsets
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from appointments.models import Appointment
from appointments.serializers import AppointmentSerializer
from core.permissions import IsStaffOrAdmin


class AppointmentViewSet(viewsets.ModelViewSet):
    serializer_class = AppointmentSerializer
    permission_classes = [IsStaffOrAdmin]
    search_fields = ['patient__first_name', 'patient__last_name', 'service__name', 'status']
    filterset_fields = ['clinic', 'status', 'service', 'patient']

    def get_queryset(self):
        queryset = Appointment.objects.select_related('clinic', 'patient', 'service', 'assigned_to')
        user = self.request.user
        if user.is_superuser or not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)


class AppointmentActionByTokenAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, token, action):
        try:
            appointment = Appointment.objects.get(confirmation_token=token)
        except Appointment.DoesNotExist:
            return Response({'detail': 'Invalid token.'}, status=status.HTTP_404_NOT_FOUND)

        if action == 'confirm':
            appointment.status = Appointment.Status.CONFIRMED
        elif action == 'cancel':
            appointment.status = Appointment.Status.CANCELLED
        else:
            return Response({'detail': 'Unsupported action.'}, status=status.HTTP_400_BAD_REQUEST)

        appointment.save(update_fields=['status', 'updated_at'])
        return Response(AppointmentSerializer(appointment).data, status=status.HTTP_200_OK)
