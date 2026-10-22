"""Página pública de la ficha (`/ficha/<token>/`): el paciente contesta desde el móvil.

Es la única vista de la capa clínica que no exige sesión, así que cada decisión
aquí es una decisión de seguridad. Lo que la hace defendible:

- **No enseña nada de la historia.** Solo el cuestionario en blanco, el texto del
  consentimiento y el nombre de la clínica: lo que ve quien tenga el enlace es lo
  mismo que vería cualquiera al abrir el formulario en papel. Ni el nombre del
  paciente, ni respuestas anteriores, ni citas.
- **Un GET no gasta el enlace.** Los previsualizadores de WhatsApp y los
  antivirus abren las URL; el enlace solo se consume al enviar (`complete_link`).
- **Un enlace que no vale no dice por qué.** Desconocido, caducado, usado,
  revocado o bloqueado dan la misma página y el mismo 404: quien tantea no
  aprende nada. Los tokens desconocidos se cuentan por IP (`throttled`).
- **No hay API ni cuerpo JSON.** POST clásico con CSRF, como el resto de la capa.
- **Cabeceras cerradas** (`_secure`): `no-store`, sin `Referer` a otros orígenes, `noindex`, sin
  marcos, y una CSP que solo permite recursos propios. La página no carga nada de
  terceros —ni fuentes, ni CDN, ni analítica—: abrirla no le cuenta a nadie la IP
  del paciente más que a nosotros.
- **Todo queda atribuido** (`patient_actor`): lo escrito en `ChangeLog` y la
  apertura en `AccessLog` llevan como actor el enlace, con origen `public`, IP y
  user agent.
"""
from functools import wraps

from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.clickjacking import xframe_options_deny
from django.views.decorators.csrf import csrf_protect

from audit.context import get_audit_context
from audit.mixins import log_access
from audit.models import AccessLog
from clinical.intake import (
    PRIVACY_NOTICE_VERSION,
    LinkNoLongerUsable,
    complete_link,
    find_link,
    link_is_openable,
    patient_actor,
    register_failed_verification,
    register_unknown_token,
    throttled,
)
from clinical.intake_forms import IntakeIdentityForm, PublicAnamnesisForm, PublicConsentForm

#: Solo recursos propios. `data:` para que el lienzo pueda repintar la firma si el
#: formulario vuelve con errores. Sin `unsafe-inline`: la página no lleva ni
#: estilos ni scripts en línea.
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'none'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)


def _secure(view):
    """Cabeceras de toda respuesta de la página pública, también las de error."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        response['Cache-Control'] = 'no-store, private'
        response['Pragma'] = 'no-cache'
        # `same-origin` y no `no-referrer`: con este último Chrome manda `Origin: null`
        # en el POST del formulario y Django lo rechaza por CSRF. Para lo que se
        # protege es equivalente: la página no tiene enlaces salientes y a otros
        # orígenes no se envía referer, así que el token no sale de aquí.
        response['Referrer-Policy'] = 'same-origin'
        response['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
        response['Content-Security-Policy'] = CSP
        response['X-Content-Type-Options'] = 'nosniff'
        return response
    return wrapper


def _client_ip():
    return get_audit_context().ip or ''


def _invalid(request, status=404):
    """La respuesta única para un enlace que no se puede usar, sea por lo que sea."""
    return render(request, 'intake/invalid.html', status=status)


@method_decorator([_secure, never_cache, xframe_options_deny, csrf_protect], name='dispatch')
class IntakeFillView(View):
    """Ver y enviar la ficha."""

    template_name = 'intake/fill.html'

    # -- el enlace ----------------------------------------------------------

    def load(self, request, token):
        """`(enlace, None)` si se puede abrir, o `(None, respuesta de error)`."""
        ip = _client_ip()
        if throttled(ip):
            response = _invalid(request, status=429)
            response['Retry-After'] = '900'
            return None, response
        link = find_link(token)
        if link is None:
            register_unknown_token(ip)
        if not link_is_openable(link):
            return None, _invalid(request)
        return link, None

    def build_forms(self, link, data=None):
        """Los formularios que pide el enlace, con la versión vigente de cada cosa.

        Devuelve `None` si algo de lo pedido ya no tiene versión vigente publicada
        (se retiró mientras el enlace estaba vivo): se pide uno nuevo a la clínica,
        no se enseña la mitad.
        """
        patient = link.patient
        forms = {'identity': IntakeIdentityForm(data, patient=patient, prefix='who')}

        if link.questionnaire_template_id:
            version = link.questionnaire_template.current_version
            if version is None or not link.questionnaire_template.is_active:
                return None
            forms['anamnesis'] = PublicAnamnesisForm(
                data, version=version, patient=patient, prefix='q',
            )
            forms['questionnaire_version'] = version
        if link.consent_template_id:
            version = link.consent_template.current_version
            if version is None or not version.is_published or not link.consent_template.is_active:
                return None
            forms['consent'] = PublicConsentForm(
                data, patient=patient, consent_version=version, prefix='c',
            )
            forms['consent_version'] = version
        return forms

    def render_form(self, request, link, forms, status=200):
        context = {
            'link': link,
            'clinic': link.clinic,
            'identity': forms['identity'],
            'anamnesis': forms.get('anamnesis'),
            'consent': forms.get('consent'),
            'consent_version': forms.get('consent_version'),
            'consent_template': link.consent_template,
            'questionnaire_template': link.questionnaire_template,
            'privacy_notice_version': PRIVACY_NOTICE_VERSION,
        }
        return render(request, self.template_name, context, status=status)

    # -- GET ----------------------------------------------------------------

    def get(self, request, token):
        link, error = self.load(request, token)
        if error is not None:
            return error
        forms = self.build_forms(link)
        if forms is None:
            return _invalid(request)
        # Aunque la página no enseña nada de la historia, quedan anotados quién la
        # abrió y desde dónde: es el primer dato de cualquier investigación.
        with patient_actor(link):
            log_access(action=AccessLog.Action.VIEW, patient=link.patient, obj=link, request=request)
        return self.render_form(request, link, forms)

    # -- POST ---------------------------------------------------------------

    def post(self, request, token):
        link, error = self.load(request, token)
        if error is not None:
            return error
        forms = self.build_forms(link, data=request.POST)
        if forms is None:
            return _invalid(request)

        parts = [forms['identity'], forms.get('anamnesis'), forms.get('consent')]
        # `is_valid()` en todos, sin cortar al primero que falle: el paciente ve
        # todos los errores de una vez en lugar de uno por intento.
        valid = all([part.is_valid() for part in parts if part is not None])

        identity = forms['identity']
        if identity.verification_failed and register_failed_verification(link):
            # Quinto fallo: el enlace queda bloqueado y la página, igual que
            # cualquier enlace muerto.
            return _invalid(request)

        if not valid:
            return self.render_form(request, link, forms)

        signer_name, signer_relationship = identity.signer()
        anamnesis, consent = forms.get('anamnesis'), forms.get('consent')
        try:
            complete_link(
                link,
                questionnaire_version=forms.get('questionnaire_version'),
                answers=anamnesis.answers() if anamnesis else None,
                consent_version=forms.get('consent_version'),
                signature_image=consent.signature_file() if consent else None,
                completed_by_name=signer_name,
                completed_by_relationship=signer_relationship,
            )
        except LinkNoLongerUsable:
            return _invalid(request)
        except ValidationError as exc:
            # El modelo valida otra vez (clínica, imagen): si algo se le escapó al
            # formulario, se enseña como error y no como un 500.
            identity.add_error(None, exc)
            return self.render_form(request, link, forms)
        return redirect('intake:done')


@_secure
@never_cache
@xframe_options_deny
def intake_done(request):
    """«Gracias». Sin token en la URL y sin ningún dato: es la misma para todos."""
    return render(request, 'intake/done.html')
