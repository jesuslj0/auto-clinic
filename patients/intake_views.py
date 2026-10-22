"""Enviar la ficha al móvil del paciente, desde el panel.

La mitad de la clínica de la ficha por enlace; la del paciente es la página pública
`clinical/public_views.py`. Aquí se decide **qué se pide** (cuestionario, firma o
ambos) y **de qué episodio cuelga**, se emite el enlace (`clinical.intake.issue_link`)
y, si la ventana de 24 h de WhatsApp está abierta, se manda por el chat.

Tres cosas a tener presentes:

- **El enlace en claro se ve una sola vez**, en la respuesta del POST que lo crea.
  En la base de datos solo queda su hash, así que ninguna pantalla puede volver a
  enseñarlo: si se pierde, se emite otro (que revoca el anterior). Por eso el POST
  responde con la página y no con un redirect: no hay dónde guardarlo en medio.
- **Fuera de la ventana de 24 h no se manda nada**: WhatsApp solo admite plantillas
  aprobadas y esta no existe todavía. La clínica copia el enlace y lo envía por el
  canal que prefiera (el aviso lo dice). Enviarlo con plantilla es un paso aparte.
- **Sin ficha, sin enlace.** Se emite sobre un paciente de la clínica del usuario
  (404 si es ajeno) y que no esté archivado. Una cita sin ficha no tiene a quién
  colgarle la historia.
"""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone

from agent.services import patient_chat_access, send_staff_message
from agent.whatsapp import WhatsAppError
from clinical.intake import issue_link, link_url, mark_sent, whatsapp_text
from clinical.intake_forms import PatientFormLinkForm
from clinical.models import PatientFormLink
from patients.history_views import HistoryActionView, HistoryPageView, documents_url


class PatientFormLinkCreateView(HistoryPageView):
    """Elegir qué se pide, emitir el enlace y enviarlo."""

    template_name = 'patients/form_link_create.html'

    def get_form(self, data=None):
        return PatientFormLinkForm(data, patient=self.object)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = context.get('form') or self.get_form()
        _, chat_state = patient_chat_access(self.object)
        context.update({
            'form': form,
            'cancel_url': documents_url(self.object.pk, 'enlaces-ficha'),
            'chat_state': chat_state,
            'archived': self.object.is_archived,
        })
        return context

    def post(self, request, *args, **kwargs):
        self.load_patient()
        if self.object.is_archived:
            messages.error(request, 'La ficha está archivada: no se le puede enviar nada.')
            return redirect(documents_url(self.object.pk, 'enlaces-ficha'))

        form = self.get_form(data=request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        link, token = issue_link(
            patient=self.object,
            created_by=request.user,
            questionnaire_template=form.cleaned_data['questionnaire'],
            consent_template=form.cleaned_data['consent'],
            episode=form.cleaned_data['episode'],
            episode_reason=form.cleaned_data['episode_reason'],
        )
        url = link_url(request, token)

        session, chat_state = patient_chat_access(self.object)
        sent, send_error = False, ''
        if chat_state == 'open':
            try:
                send_staff_message(session=session, body=whatsapp_text(link, url))
            except WhatsAppError as exc:
                send_error = str(exc)
            else:
                sent = True
                mark_sent(link, channel=PatientFormLink.Channel.WHATSAPP)

        return self.render_to_response({
            **self.get_context_data(form=form),
            'result': True,
            'link': link,
            'link_url': url,
            'sent': sent,
            'send_error': send_error,
            'chat_state': chat_state,
            'expires_at': timezone.localtime(link.expires_at),
        })


class PatientFormLinkRevokeView(HistoryActionView):
    """Anular un enlace pendiente: deja de abrir la ficha de inmediato."""

    def post(self, request, id, link_id):
        self.load_patient()
        link = get_object_or_404(PatientFormLink, pk=link_id, patient=self.object)
        if link.get_status() == PatientFormLink.Status.PENDING:
            link.revoke(by=request.user)
            messages.success(request, 'Enlace anulado: ya no abre la ficha.')
        else:
            messages.info(request, 'Ese enlace ya no estaba pendiente.')
        return redirect(documents_url(self.object.pk, 'enlaces-ficha'))
