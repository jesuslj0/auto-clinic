from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from appointments.views import AppointmentActionByTokenAPIView, AppointmentViewSet
from billing.views import SubscriptionViewSet
from core.views import ClinicViewSet, UserViewSet
from notifications.views import ReminderViewSet
from patients.views import PatientViewSet
from services.views import ServiceViewSet

router = DefaultRouter()
router.register('clinics', ClinicViewSet, basename='clinic')
router.register('users', UserViewSet, basename='user')
router.register('patients', PatientViewSet, basename='patient')
router.register('services', ServiceViewSet, basename='service')
router.register('appointments', AppointmentViewSet, basename='appointment')
router.register('reminders', ReminderViewSet, basename='reminder')
router.register('subscriptions', SubscriptionViewSet, basename='subscription')

urlpatterns = [
    path('', include('core.urls')),
    path('appointments/', include('appointments.urls')),
    path('patients/', include('patients.urls')),
    path('booking/', include('booking.urls')),
    path('portal/', include('portal.urls')),
    path('admin/', admin.site.urls),
    path('api/', include(router.urls)),
    path(
        'api/public/appointments/<uuid:token>/<str:action>/',
        AppointmentActionByTokenAPIView.as_view(),
        name='appointment-token-action',
    ),
]
