from django.urls import path

from appointments.views import (
    ProfessionalCreateView,
    ProfessionalListView,
    ProfessionalUpdateView,
)

# Los profesionales cuelgan de la raíz (`/profesionales/`) y no de `/citas/`:
# son personal de la clínica, no una parte de la agenda. Las vistas siguen en
# `appointments` porque el modelo vive ahí.
app_name = 'professionals'

urlpatterns = [
    path('', ProfessionalListView.as_view(), name='list'),
    path('crear/', ProfessionalCreateView.as_view(), name='create'),
    path('<int:pk>/editar/', ProfessionalUpdateView.as_view(), name='edit'),
]
