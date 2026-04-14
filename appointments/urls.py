from django.urls import path

from appointments.views import (
    AppointmentCalendarView,
    AppointmentListView,
    ProfessionalCreateView,
    ProfessionalListView,
    ProfessionalUpdateView,
)

app_name = 'appointments'

urlpatterns = [
    path('', AppointmentCalendarView.as_view(), name='calendar'),
    path('list/', AppointmentListView.as_view(), name='list'),
    path('professionals/', ProfessionalListView.as_view(), name='professionals-list'),
    path('professionals/crear/', ProfessionalCreateView.as_view(), name='professionals-create'),
    path('professionals/<int:pk>/editar/', ProfessionalUpdateView.as_view(), name='professionals-edit'),
]
