"""Alertas clínicas desde la ficha: dar de alta a mano, retocar, desactivar y reactivar.

Hasta aquí las alertas solo nacían de la anamnesis (motor de derivación). Esto es la
otra vía: lo que el equipo sabe y no está en un cuestionario. Sesión y CSRF, **sin
API**: el `Api-Key` del agente no llega a la capa clínica.

Reglas, por orden de importancia:

- **Una alerta no se borra: se desactiva.** No hay vista de borrado. Desactivar
  conserva la fila, que es lo que permite responder a «esto se sabía en aquel
  momento»; reactivar la devuelve a la ficha. Los dos pasan por `deactivate()` /
  `reactivate()` y quedan en el `ChangeLog` (la `note`, enmascarada).
- **Solo las manuales se editan.** Una derivada es lo que dijo la anamnesis; se
  corrige contestándola de nuevo. Se pueden desactivar igualmente (un profesional
  puede juzgar que no aplica), pero no retocar.
- **El motor no toca las manuales** y estas vistas no inventan procedencia: el alta
  es siempre `source='manual'`, firmada por el profesional que la registra.
- **Aislamiento:** la alerta se busca *dentro del paciente de la URL*, que a su vez
  está acotado a la clínica del usuario. Ajena, 404.
- **Lecturas con `AccessLog`:** las pantallas con GET que enseñan una alerta (la
  edición) la registran; las acciones solo-POST no leen nada.
"""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse

from clinical.forms import ClinicalAlertForm
from clinical.models import ClinicalAlert
from patients.history_views import HistoryActionView, HistoryAccessMixin, HistoryFormView


def alerts_url(patient_id):
    return reverse('patients:tab-alerts', kwargs={'id': patient_id})


class AlertAccessMixin(HistoryAccessMixin):
    """La alerta de la URL, buscada dentro del paciente ya resuelto."""

    def get_alert(self):
        if not hasattr(self, '_alert'):
            self._alert = get_object_or_404(
                ClinicalAlert.objects.select_related('created_by__user'),
                pk=self.kwargs['alert_id'],
                patient=self.object,
            )
        return self._alert


class AlertFormView(AlertAccessMixin, HistoryFormView):
    """Base de las pantallas de formulario de alertas."""

    back_label = 'Volver a las alertas'

    def get_cancel_url(self):
        return alerts_url(self.object.pk)


class PatientAlertCreateView(AlertFormView):
    """Dar de alta una alerta a mano."""

    page_title = 'Nueva alerta clínica'
    submit_label = 'Dar de alta la alerta'

    def get_form(self, data=None):
        return ClinicalAlertForm(data, patient=self.object)

    def form_valid(self, form):
        alert = form.save(commit=False)
        alert.patient = self.object
        alert.source = ClinicalAlert.Source.MANUAL
        alert.created_by = self.get_professional()
        alert.save()
        messages.success(self.request, f'Alerta «{alert.get_alert_type_display()}» registrada.')
        return redirect(alerts_url(self.object.pk))


class PatientAlertEditView(AlertFormView):
    """Retocar gravedad y detalle de una alerta manual. El tipo no cambia."""

    page_title = 'Editar alerta'

    def get_access_object(self):
        return self.get_alert()

    def get_page_subtitle(self):
        return f'{self.object} · {self.get_alert().get_alert_type_display()}'

    def get_form(self, data=None):
        return ClinicalAlertForm(data, instance=self.get_alert(), patient=self.object)

    def refuse_derived(self):
        messages.error(
            self.request,
            'Esta alerta salió de una anamnesis: no se edita. Contesta de nuevo la anamnesis, '
            'o desactívala y da de alta una manual.',
        )
        return redirect(alerts_url(self.object.pk))

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.get_alert().source != ClinicalAlert.Source.MANUAL:
            return self.refuse_derived()
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self.load_patient()
        if self.get_alert().source != ClinicalAlert.Source.MANUAL:
            return self.refuse_derived()
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        form.save()
        messages.success(self.request, 'Alerta actualizada.')
        return redirect(alerts_url(self.object.pk))


class PatientAlertDeactivateView(AlertAccessMixin, HistoryActionView):
    """Retirar la alerta de la ficha conservando la fila."""

    def post(self, request, id, alert_id):
        self.load_patient()
        alert = self.get_alert()
        if alert.is_active:
            alert.deactivate()
            messages.success(request, f'Alerta «{alert.get_alert_type_display()}» desactivada.')
        return redirect(alerts_url(self.object.pk))


class PatientAlertReactivateView(AlertAccessMixin, HistoryActionView):
    """Volver a poner una alerta desactivada en la ficha."""

    def post(self, request, id, alert_id):
        self.load_patient()
        alert = self.get_alert()
        if not alert.is_active:
            alert.reactivate()
            messages.success(request, f'Alerta «{alert.get_alert_type_display()}» reactivada.')
        return redirect(alerts_url(self.object.pk))
