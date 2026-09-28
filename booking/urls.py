from django.urls import path

from booking.views import (
    BookingConfirmView,
    BookingDateTimeView,
    BookingServiceListView,
    BookingSuccessView,
)

app_name = 'booking'

urlpatterns = [
    path('', BookingServiceListView.as_view(), name='service_list'),
    path('fecha/', BookingDateTimeView.as_view(), name='datetime'),
    path('confirmar/', BookingConfirmView.as_view(), name='confirm'),
    path('confirmada/', BookingSuccessView.as_view(), name='success'),
]
