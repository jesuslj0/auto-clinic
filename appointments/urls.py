from django.urls import path

from appointments.views import AppointmentCalendarView, AppointmentListView

app_name = 'appointments'

urlpatterns = [
    path('', AppointmentCalendarView.as_view(), name='calendar'),
    path('list/', AppointmentListView.as_view(), name='list'),
]
