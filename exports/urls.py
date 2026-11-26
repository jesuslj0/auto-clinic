from django.urls import path

from exports import views

app_name = 'exports'

urlpatterns = [
    path('', views.ExportListView.as_view(), name='list'),
    path('clinica/crear/', views.ClinicExportCreateView.as_view(), name='clinic-create'),
    path('paciente/<int:patient_id>/crear/', views.PatientExportCreateView.as_view(), name='patient-create'),
    path('<uuid:pk>/descargar/', views.ExportDownloadView.as_view(), name='download'),
]
