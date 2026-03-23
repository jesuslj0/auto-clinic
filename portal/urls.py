from django.urls import path

from portal.views import (
    PortalAppointmentCancelView,
    PortalAppointmentConfirmView,
    PortalAppointmentDetailView,
)

app_name = 'portal'

urlpatterns = [
    path('<uuid:token>/', PortalAppointmentDetailView.as_view(), name='detail'),
    path('<uuid:token>/confirm/', PortalAppointmentConfirmView.as_view(), name='confirm'),
    path('<uuid:token>/cancel/', PortalAppointmentCancelView.as_view(), name='cancel'),
]
