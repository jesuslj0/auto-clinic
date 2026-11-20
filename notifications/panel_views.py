"""Vistas del panel de notificaciones: cada usuario ve y gestiona solo las suyas."""

import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import TemplateView

from notifications.inbox import push_enabled, unread_count
from notifications.models import Notification, NotificationPreference, PushSubscription

PAGE_SIZE = 20
RECENT_SIZE = 6


class NotificationAccessMixin(LoginRequiredMixin):
    """Solo usuarios con clínica: el equipo de plataforma no recibe avisos."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.clinic_id:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def mine(self):
        return Notification.objects.filter(user=self.request.user, dismissed_at=None)


def _safe_next(request, fallback):
    target = request.POST.get('next') or request.GET.get('next') or ''
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return target
    return fallback


class NotificationListView(NotificationAccessMixin, TemplateView):
    template_name = 'notifications/list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        queryset = self.mine()
        state = self.request.GET.get('estado', '')
        kind = self.request.GET.get('categoria', '')
        if state == 'sin-leer':
            queryset = queryset.filter(read_at=None)
        elif state == 'leidas':
            queryset = queryset.filter(read_at__isnull=False)
        if kind in Notification.Kind.values:
            queryset = queryset.filter(kind=kind)
        else:
            kind = ''
        page = Paginator(queryset, PAGE_SIZE).get_page(self.request.GET.get('page'))
        query = self.request.GET.copy()
        query.pop('page', None)
        # Separadas dentro de la propia página (sin otra consulta): con un filtro
        # de estado ya puesto, una de las dos queda vacía y no se pinta su título.
        unread = [n for n in page.object_list if not n.is_read]
        read = [n for n in page.object_list if n.is_read]
        context.update(
            page_obj=page,
            notifications=page.object_list,
            unread_notifications=unread,
            read_notifications=read,
            state=state,
            kind=kind,
            kinds=Notification.Kind.choices,
            querystring=query.urlencode(),
        )
        return context


class NotificationOpenView(NotificationAccessMixin, View):
    """Abrir = marcar como leída y llevar al destino."""

    def get(self, request, pk):
        notification = get_object_or_404(Notification, pk=pk, user=request.user)
        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=['read_at'])
        target = notification.url
        # El destino lo escribe el servidor, pero se valida igualmente: nunca fuera del sitio.
        if not target or not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
            target = reverse('notifications:list')
        return redirect(target)


class NotificationReadView(NotificationAccessMixin, View):
    def post(self, request, pk):
        notification = get_object_or_404(Notification, pk=pk, user=request.user)
        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=['read_at'])
        return redirect(_safe_next(request, reverse('notifications:list')))


class NotificationDismissView(NotificationAccessMixin, View):
    def post(self, request, pk):
        notification = get_object_or_404(Notification, pk=pk, user=request.user)
        now = timezone.now()
        notification.dismissed_at = now
        if notification.read_at is None:
            notification.read_at = now
        notification.save(update_fields=['dismissed_at', 'read_at'])
        return redirect(_safe_next(request, reverse('notifications:list')))


class NotificationReadAllView(NotificationAccessMixin, View):
    def post(self, request):
        # Modelo no auditado (avisos genéricos): el UPDATE masivo está permitido.
        Notification.objects.filter(user=request.user, read_at=None).update(read_at=timezone.now())
        return redirect(_safe_next(request, reverse('notifications:list')))


class NotificationCountView(NotificationAccessMixin, View):
    """Contador y último aviso sin leer, para el badge y el toast (solo del usuario)."""

    def get(self, request):
        latest = (
            Notification.objects.filter(user=request.user, read_at=None, dismissed_at=None)
            .order_by('-created_at', '-id')
            .first()
        )
        payload = {'unread': unread_count(request.user)}
        if latest is not None:
            payload['latest'] = {
                'id': latest.pk,
                'kind': latest.kind,
                'title': latest.title,
                'body': latest.body,
                'detail': latest.detail,
                'href': reverse('notifications:open', args=[latest.pk]),
                'at': latest.created_at.isoformat(),
            }
        response = JsonResponse(payload)
        response['Cache-Control'] = 'no-store'
        return response


class NotificationRecentView(NotificationAccessMixin, TemplateView):
    """Fragmento del desplegable de la campana."""

    template_name = 'notifications/_recent.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['recent'] = list(self.mine()[:RECENT_SIZE])
        context['unread_total'] = unread_count(self.request.user)
        return context


class NotificationPreferencesView(NotificationAccessMixin, TemplateView):
    template_name = 'notifications/preferences.html'

    def _rows(self):
        from notifications.inbox import ADMIN_ONLY

        saved = {p.kind: p for p in NotificationPreference.objects.filter(user=self.request.user)}
        is_admin = self.request.user.role == 'admin'
        rows = []
        for value, label in Notification.Kind.choices:
            if value in ADMIN_ONLY and not is_admin:
                continue
            pref = saved.get(value)
            rows.append({
                'kind': value,
                'label': label,
                'in_app': pref.in_app if pref else True,
                'push': pref.push if pref else True,
            })
        return rows

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            rows=self._rows(),
            push_available=push_enabled(),
            vapid_public_key=settings.VAPID_PUBLIC_KEY if push_enabled() else '',
            devices=self.request.user.push_subscriptions.count(),
        )
        return context

    def post(self, request):
        for row in self._rows():
            NotificationPreference.objects.update_or_create(
                user=request.user,
                kind=row['kind'],
                defaults={
                    'in_app': f"in_app_{row['kind']}" in request.POST,
                    'push': f"push_{row['kind']}" in request.POST,
                },
            )
        messages.success(request, 'Preferencias de notificaciones guardadas.')
        return redirect('notifications:preferences')


def _json_body(request):
    try:
        return json.loads(request.body or b'{}')
    except ValueError:
        return None


class PushSubscribeView(NotificationAccessMixin, View):
    """Guarda la suscripción Web Push de este dispositivo (sesión + CSRF)."""

    def post(self, request):
        if not push_enabled():
            return JsonResponse({'detail': 'push_disabled'}, status=409)
        data = _json_body(request)
        keys = (data or {}).get('keys') or {}
        endpoint = (data or {}).get('endpoint') or ''
        p256dh, auth = keys.get('p256dh', ''), keys.get('auth', '')
        # Solo https: un endpoint arbitrario sería una petición saliente a donde
        # quiera quien se suscriba (SSRF desde el worker de Celery).
        if not (endpoint.startswith('https://') and p256dh and auth) or len(endpoint) > 1000:
            return JsonResponse({'detail': 'invalid'}, status=400)
        PushSubscription.objects.update_or_create(
            endpoint=endpoint,
            defaults={
                'user': request.user,
                'p256dh': p256dh[:200],
                'auth': auth[:100],
                'user_agent': request.META.get('HTTP_USER_AGENT', '')[:300],
                'failures': 0,
            },
        )
        return JsonResponse({'ok': True}, status=201)


class PushUnsubscribeView(NotificationAccessMixin, View):
    def post(self, request):
        data = _json_body(request) or {}
        PushSubscription.objects.filter(user=request.user, endpoint=data.get('endpoint', '')).delete()
        return JsonResponse({'ok': True})


class ServiceWorkerView(View):
    """`/sw.js` desde Django: con `Service-Worker-Allowed: /` y sin caché.

    WhiteNoise serviría el estático con hash y bajo `/static/`, y un service worker
    solo controla su propio directorio.
    """

    def get(self, request):
        from django.contrib.staticfiles import finders

        path = finders.find('js/sw.js')
        if not path:
            raise Http404
        with open(path, encoding='utf-8') as fh:
            body = fh.read()
        response = HttpResponse(body, content_type='application/javascript; charset=utf-8')
        response['Service-Worker-Allowed'] = '/'
        response['Cache-Control'] = 'no-cache'
        return response


class ManifestView(View):
    def get(self, request):
        from django.templatetags.static import static

        data = {
            'name': 'AutoClinic',
            'short_name': 'AutoClinic',
            'description': 'Panel de gestión de la clínica',
            'start_url': '/',
            'scope': '/',
            'display': 'standalone',
            'background_color': '#ffffff',
            'theme_color': '#7c3aed',
            'lang': 'es',
            'icons': [
                {'src': static('img/favicon/android-chrome-192x192.png'), 'sizes': '192x192', 'type': 'image/png'},
                {'src': static('img/favicon/android-chrome-512x512.png'), 'sizes': '512x512', 'type': 'image/png'},
            ],
        }
        response = HttpResponse(json.dumps(data), content_type='application/manifest+json')
        response['Cache-Control'] = 'no-cache'
        return response
