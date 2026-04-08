from django.urls import path

from knowledge.views import (
    KnowledgeBaseCreateView,
    KnowledgeBaseDeleteView,
    KnowledgeBaseEditView,
    KnowledgeBaseListView,
)

app_name = 'knowledge'

urlpatterns = [
    path('', KnowledgeBaseListView.as_view(), name='list'),
    path('crear/', KnowledgeBaseCreateView.as_view(), name='create'),
    path('<uuid:pk>/editar/', KnowledgeBaseEditView.as_view(), name='edit'),
    path('<uuid:pk>/eliminar/', KnowledgeBaseDeleteView.as_view(), name='delete'),
]
