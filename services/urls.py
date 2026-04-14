from django.urls import path

from .views import ServiceCreateView, ServiceListView, ServiceUpdateView

app_name = 'services'

urlpatterns = [
    path('', ServiceListView.as_view(), name='list'),
    path('crear/', ServiceCreateView.as_view(), name='create'),
    path('<int:pk>/editar/', ServiceUpdateView.as_view(), name='edit'),
]
