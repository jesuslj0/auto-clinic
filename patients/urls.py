from django.urls import path

from patients.views import (
    PatientAlertsTabView,
    PatientAnamnesisCreateView,
    PatientAnamnesisTabView,
    PatientConsentsTabView,
    PatientCreateView,
    PatientDetailView,
    PatientEditView,
    PatientLesionCompareView,
    PatientLesionCreateView,
    PatientLesionDetailView,
    PatientLesionEvolutionView,
    PatientLesionMapView,
    PatientLesionObservationCreateView,
    PatientLesionResolveView,
    PatientLesionsTabView,
    PatientListView,
    PatientProceduresTabView,
)

app_name = 'patients'

urlpatterns = [
    path('', PatientListView.as_view(), name='list'),
    path('crear/', PatientCreateView.as_view(), name='create'),
    # Ficha del paciente. Cada pestaña tiene su URL real (no un querystring):
    # se puede compartir, marcar como favorita y recargar. HTMX las usa tal cual
    # con `hx-push-url`, y sin JavaScript siguen siendo enlaces normales.
    path('<int:id>/', PatientDetailView.as_view(), name='detail'),
    path('<int:id>/anamnesis/', PatientAnamnesisTabView.as_view(), name='tab-anamnesis'),
    # Rellenar una anamnesis. Página propia y no un modal: es un formulario
    # largo, y su alta congela un documento clínico.
    path(
        '<int:id>/anamnesis/nueva/',
        PatientAnamnesisCreateView.as_view(),
        name='anamnesis-create',
    ),
    path('<int:id>/alertas/', PatientAlertsTabView.as_view(), name='tab-alerts'),
    path('<int:id>/lesiones/', PatientLesionsTabView.as_view(), name='tab-lesions'),
    # Registrar una lesión marcada sobre el mapa. Con htmx devuelve solo la
    # región del mapa (formulario primero, marcadores después); sin htmx es una
    # página normal, así que el alta también funciona sin JavaScript.
    path(
        '<int:id>/lesiones/nueva/',
        PatientLesionCreateView.as_view(),
        name='lesion-create',
    ),
    # El mapa a secas: lo que se pide al cancelar el alta para volver a pintar la
    # región sin el formulario.
    path('<int:id>/lesiones/mapa/', PatientLesionMapView.as_view(), name='lesion-map'),
    # Detalle de una lesión y su seguimiento. Son URLs de verdad, no fragmentos
    # de adorno: cada una es invocable directamente, comprueba que la lesión sea
    # de ESE paciente y registra su `AccessLog`.
    path(
        '<int:id>/lesiones/<int:lesion_id>/',
        PatientLesionDetailView.as_view(),
        name='lesion-detail',
    ),
    path(
        '<int:id>/lesiones/<int:lesion_id>/observaciones/nueva/',
        PatientLesionObservationCreateView.as_view(),
        name='observation-create',
    ),
    path(
        '<int:id>/lesiones/<int:lesion_id>/resolver/',
        PatientLesionResolveView.as_view(),
        name='lesion-resolve',
    ),
    # La evolución es una página propia y no un panel: comparar dos fotos de una
    # úlcera necesita el ancho entero. El comparador de dos observaciones sí es
    # un fragmento, y lo sirve el servidor (los datos no viven en el navegador).
    path(
        '<int:id>/lesiones/<int:lesion_id>/evolucion/',
        PatientLesionEvolutionView.as_view(),
        name='lesion-evolution',
    ),
    path(
        '<int:id>/lesiones/<int:lesion_id>/evolucion/comparar/',
        PatientLesionCompareView.as_view(),
        name='lesion-compare',
    ),
    path('<int:id>/consentimientos/', PatientConsentsTabView.as_view(), name='tab-consents'),
    path('<int:id>/procedimientos/', PatientProceduresTabView.as_view(), name='tab-procedures'),
    path('<int:id>/editar/', PatientEditView.as_view(), name='edit'),
]
