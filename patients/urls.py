from django.urls import path

from patients.views import PatientDetailView, PatientListView

app_name = 'patients'

urlpatterns = [
    path('', PatientListView.as_view(), name='list'),
    path('<int:id>/', PatientDetailView.as_view(), name='detail'),
]
