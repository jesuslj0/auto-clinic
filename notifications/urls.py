from django.urls import path

from notifications import panel_views as v

app_name = 'notifications'

urlpatterns = [
    path('', v.NotificationListView.as_view(), name='list'),
    path('contador/', v.NotificationCountView.as_view(), name='count'),
    path('recientes/', v.NotificationRecentView.as_view(), name='recent'),
    path('leer-todas/', v.NotificationReadAllView.as_view(), name='read-all'),
    path('preferencias/', v.NotificationPreferencesView.as_view(), name='preferences'),
    path('push/suscribir/', v.PushSubscribeView.as_view(), name='push-subscribe'),
    path('push/baja/', v.PushUnsubscribeView.as_view(), name='push-unsubscribe'),
    path('<int:pk>/abrir/', v.NotificationOpenView.as_view(), name='open'),
    path('<int:pk>/leer/', v.NotificationReadView.as_view(), name='read'),
    path('<int:pk>/descartar/', v.NotificationDismissView.as_view(), name='dismiss'),
]
