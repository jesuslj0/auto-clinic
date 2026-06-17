from django.urls import path

from appointments.views import (
    AppointmentCalendarView,
    AppointmentCreateView,
    AppointmentListView,
    ProfessionalCreateView,
    ProfessionalListView,
    ProfessionalProfileView,
    ProfessionalUpdateView,
)

app_name = 'appointments'

urlpatterns = [
    path('', AppointmentCalendarView.as_view(), name='calendar'),
    path('crear/', AppointmentCreateView.as_view(), name='create'),
    path('list/', AppointmentListView.as_view(), name='list'),
    path('mi-perfil/', ProfessionalProfileView.as_view(), name='profile'),
    path('professionals/', ProfessionalListView.as_view(), name='professionals-list'),
    path('professionals/crear/', ProfessionalCreateView.as_view(), name='professionals-create'),
    path('professionals/<int:pk>/editar/', ProfessionalUpdateView.as_view(), name='professionals-edit'),
]
