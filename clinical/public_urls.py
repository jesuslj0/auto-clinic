"""Rutas públicas de la ficha del paciente (`/ficha/…`), sin sesión.

Aparte de `clinical/urls.py` a propósito: aquella es la capa clínica detrás de
login y esta es la única parte que no lo tiene. Mezclarlas en el mismo fichero
sería cuestión de tiempo hasta que una vista pública heredara algo que no debe.
"""
from django.urls import path

from clinical.public_views import IntakeFillView, intake_done

app_name = 'intake'

urlpatterns = [
    # Antes que el token: `gracias` no puede leerse como uno.
    path('gracias/', intake_done, name='done'),
    path('<str:token>/', IntakeFillView.as_view(), name='fill'),
]
