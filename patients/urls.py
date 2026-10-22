from django.urls import path

from patients.alert_views import (
    PatientAlertCreateView,
    PatientAlertDeactivateView,
    PatientAlertEditView,
    PatientAlertReactivateView,
)
from patients.history_views import (
    PatientAddendumCreateView,
    PatientConsentSignView,
    PatientEpisodeCloseView,
    PatientEpisodeCreateView,
    PatientEpisodeEditView,
    PatientEpisodeReopenView,
    PatientHistoryTabView,
    PatientNoteCreateView,
    PatientNoteDeleteView,
    PatientNoteDetailView,
    PatientNoteEditView,
    PatientNoteSignView,
    PatientVisitCreateView,
    PatientVisitEditView,
)
from patients.intake_views import PatientFormLinkCreateView, PatientFormLinkRevokeView
from patients.views import (
    PatientAlertsTabView,
    PatientArchiveView,
    PatientAnamnesisCreateView,
    PatientAnamnesisTabView,
    PatientConsentsTabView,
    PatientDocumentsTabView,
    PatientCreateView,
    PatientDetailView,
    PatientEditView,
    PatientGuardianAddView,
    PatientGuardianCreatePatientView,
    PatientGuardianRemoveView,
    PatientLesionCompareView,
    PatientLesionCreateView,
    PatientLesionDetailView,
    PatientLesionEvolutionView,
    PatientLesionMapView,
    PatientLesionObservationCreateView,
    PatientLesionResolveView,
    PatientLesionsTabView,
    PatientListView,
    PatientPhotoView,
    PatientRestoreView,
    PatientProcedureCreateView,
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
    # Curso clínico: episodios → visitas → notas SOAP (borrador → firmada) →
    # adendas. Sesión y CSRF; la capa clínica no tiene API a propósito.
    path('<int:id>/historia/', PatientHistoryTabView.as_view(), name='tab-history'),
    path(
        '<int:id>/historia/episodios/nuevo/',
        PatientEpisodeCreateView.as_view(),
        name='episode-create',
    ),
    path(
        '<int:id>/historia/episodios/<int:episode_id>/editar/',
        PatientEpisodeEditView.as_view(),
        name='episode-edit',
    ),
    path(
        '<int:id>/historia/episodios/<int:episode_id>/cerrar/',
        PatientEpisodeCloseView.as_view(),
        name='episode-close',
    ),
    path(
        '<int:id>/historia/episodios/<int:episode_id>/reabrir/',
        PatientEpisodeReopenView.as_view(),
        name='episode-reopen',
    ),
    path(
        '<int:id>/historia/episodios/<int:episode_id>/visitas/nueva/',
        PatientVisitCreateView.as_view(),
        name='visit-create',
    ),
    path(
        '<int:id>/historia/visitas/<int:visit_id>/editar/',
        PatientVisitEditView.as_view(),
        name='visit-edit',
    ),
    path(
        '<int:id>/historia/visitas/<int:visit_id>/notas/nueva/',
        PatientNoteCreateView.as_view(),
        name='note-create',
    ),
    path(
        '<int:id>/historia/notas/<int:note_id>/',
        PatientNoteDetailView.as_view(),
        name='note-detail',
    ),
    path(
        '<int:id>/historia/notas/<int:note_id>/editar/',
        PatientNoteEditView.as_view(),
        name='note-edit',
    ),
    path(
        '<int:id>/historia/notas/<int:note_id>/firmar/',
        PatientNoteSignView.as_view(),
        name='note-sign',
    ),
    path(
        '<int:id>/historia/notas/<int:note_id>/eliminar/',
        PatientNoteDeleteView.as_view(),
        name='note-delete',
    ),
    path(
        '<int:id>/historia/notas/<int:note_id>/adenda/',
        PatientAddendumCreateView.as_view(),
        name='addendum-create',
    ),
    # «Documentos» reúne anamnesis y consentimientos en una sola pestaña. Las rutas
    # antiguas (`anamnesis/`, `consentimientos/`) devuelven esta misma pestaña.
    path('<int:id>/documentos/', PatientDocumentsTabView.as_view(), name='tab-documents'),
    path('<int:id>/anamnesis/', PatientAnamnesisTabView.as_view(), name='tab-anamnesis'),
    # Rellenar una anamnesis. Página propia y no un modal: es un formulario
    # largo, y su alta congela un documento clínico.
    path(
        '<int:id>/anamnesis/nueva/',
        PatientAnamnesisCreateView.as_view(),
        name='anamnesis-create',
    ),
    path('<int:id>/alertas/', PatientAlertsTabView.as_view(), name='tab-alerts'),
    # Alertas a mano (la otra vía, además de la anamnesis). Una alerta no se
    # borra: se desactiva, y se puede reactivar.
    path('<int:id>/alertas/nueva/', PatientAlertCreateView.as_view(), name='alert-create'),
    path('<int:id>/alertas/<int:alert_id>/editar/', PatientAlertEditView.as_view(), name='alert-edit'),
    path(
        '<int:id>/alertas/<int:alert_id>/desactivar/',
        PatientAlertDeactivateView.as_view(),
        name='alert-deactivate',
    ),
    path(
        '<int:id>/alertas/<int:alert_id>/reactivar/',
        PatientAlertReactivateView.as_view(),
        name='alert-reactivate',
    ),
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
    # Firmar un consentimiento: página propia (texto íntegro + lienzo de firma),
    # no un modal. Lo firmado se copia literal y la firma va al bucket privado.
    path(
        '<int:id>/consentimientos/firmar/',
        PatientConsentSignView.as_view(),
        name='consent-sign',
    ),
    # Mandar la ficha al móvil del paciente por WhatsApp: enlace de un solo uso con
    # caducidad (`clinical/intake.py`). El que la rellena abre `/ficha/<token>/`.
    path(
        '<int:id>/enviar-ficha/',
        PatientFormLinkCreateView.as_view(),
        name='form-link-create',
    ),
    path(
        '<int:id>/enlaces-ficha/<int:link_id>/anular/',
        PatientFormLinkRevokeView.as_view(),
        name='form-link-revoke',
    ),
    path('<int:id>/procedimientos/', PatientProceduresTabView.as_view(), name='tab-procedures'),
    # Alta sin cita de por medio: urgencia, paciente sin avisar o algo que
    # se apunta después. La puerta normal es `appointments:procedure-create`.
    path(
        '<int:id>/procedimientos/nuevo/',
        PatientProcedureCreateView.as_view(),
        name='procedure-create',
    ),
    path('<int:id>/contactos/nuevo/', PatientGuardianAddView.as_view(), name='guardian-add'),
    path(
        '<int:id>/contactos/<int:link_id>/quitar/',
        PatientGuardianRemoveView.as_view(),
        name='guardian-remove',
    ),
    path(
        '<int:id>/contactos/<int:link_id>/crear-ficha/',
        PatientGuardianCreatePatientView.as_view(),
        name='guardian-create-patient',
    ),
    path('<int:id>/archivar/', PatientArchiveView.as_view(), name='archive'),
    path('<int:id>/restaurar/', PatientRestoreView.as_view(), name='restore'),
    path('<int:id>/editar/', PatientEditView.as_view(), name='edit'),
    # Foto de perfil: redirige a una URL firmada del bucket privado.
    path('<int:id>/foto/', PatientPhotoView.as_view(), name='photo'),
]
