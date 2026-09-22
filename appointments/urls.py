from django.urls import path
from django.views.generic import RedirectView

from appointments.views import (
    AppointmentCalendarView,
    AppointmentCreateView,
    AppointmentListView,
    AppointmentProcedureCreateView,
    ProfessionalCreateView,
    ProfessionalListView,
    ProfessionalUpdateView,
)

app_name = 'appointments'

urlpatterns = [
    path('', AppointmentCalendarView.as_view(), name='calendar'),
    path('crear/', AppointmentCreateView.as_view(), name='create'),
    path('list/', AppointmentListView.as_view(), name='list'),
    # Puerta principal del alta de procedimientos: aquí la visita queda
    # enganchada a la cita, que es lo que hace visible en el listado qué
    # citas acabaron en algo. Sesión y CSRF, nunca API (capa clínica).
    path(
        '<uuid:appointment_id>/procedimiento/',
        AppointmentProcedureCreateView.as_view(),
        name='procedure-create',
    ),
    # El perfil propio vive ahora en «Mi cuenta» (`core:account`), junto a los
    # datos de acceso y la contraseña, que es donde se va a buscarlo. La ruta se
    # conserva redirigiendo: la tenía el menú, y puede estar en un marcador.
    path(
        'mi-perfil/',
        RedirectView.as_view(pattern_name='core:account-profile', permanent=False),
        name='profile',
    ),
    path('professionals/', ProfessionalListView.as_view(), name='professionals-list'),
    path('professionals/crear/', ProfessionalCreateView.as_view(), name='professionals-create'),
    path('professionals/<int:pk>/editar/', ProfessionalUpdateView.as_view(), name='professionals-edit'),
]
