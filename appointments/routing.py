from django.urls import re_path

from appointments.consumers import AppointmentConsumer

websocket_urlpatterns = [
    re_path(r'ws/appointments/(?P<clinic_id>\d+)/$', AppointmentConsumer.as_asgi()),
]
