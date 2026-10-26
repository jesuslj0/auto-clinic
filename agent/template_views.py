"""Pestaña «Plantillas» del agente: ver, crear, editar y enviar a revisión.

Solo administradores de la clínica (misma regla que el resto de `/agente/`). El
estado de cada plantilla lo manda Meta; aquí solo se redacta y se pide revisión.
"""

from django.contrib import messages
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect
from django.views import View

from agent import wa_templates
from agent.forms import WhatsAppTemplateForm
from agent.models import WhatsAppTemplate
from agent.settings_views import AgentSettingsMixin
from agent.whatsapp import WhatsAppError
from appointments.reminder_fields import default_map, fields_for

Status = WhatsAppTemplate.Status
Purpose = WhatsAppTemplate.Purpose

#: Los envíos automáticos que una plantilla puede cubrir y cómo se explican en el panel.
#: `when` completa «Cada paciente… recibirá este mensaje {when}».
PURPOSE_COPY = {
    Purpose.REMINDER_24H: {
        'name': 'Recordatorio de cita',
        'action': 'Usar como recordatorio',
        'when': '24 horas antes de la cita, si la tiene confirmada y aún no ha respondido',
        'hint': 'Recuerda que el texto debe pedir que respondan SÍ o NO: el agente gestiona la respuesta.',
    },
    Purpose.BOOKING_CONFIRMED: {
        'name': 'Aviso de cita confirmada',
        'action': 'Usar como aviso de cita confirmada',
        'when': 'cuando la clínica confirma su cita (o la da de alta ya confirmada)',
        'hint': 'Solo avisa de lo que hace la clínica: si el paciente pidió la cita por WhatsApp, no se repite.',
    },
    Purpose.BOOKING_CHANGED: {
        'name': 'Aviso de cambio de cita',
        'action': 'Usar como aviso de cambio',
        'when': 'cuando la clínica le cambia la hora de la cita',
        'hint': 'Puedes usar la fecha y la hora anteriores además de las nuevas.',
    },
    Purpose.BOOKING_CANCELLED: {
        'name': 'Aviso de cita cancelada',
        'action': 'Usar como aviso de cancelación',
        'when': 'cuando la clínica le cancela la cita',
        'hint': 'No se envía si cancela el propio paciente ni cuando una cita sin confirmar caduca.',
    },
}


class TemplateScopedMixin(AgentSettingsMixin):
    """Plantillas de la clínica del usuario: ninguna de otra es alcanzable."""

    tab = 'templates'

    def get_template(self, pk):
        return get_object_or_404(WhatsAppTemplate, pk=pk, clinic=self.request.user.clinic)

    def render_tab(self, request, **extra):
        clinic = request.user.clinic
        return super().render_tab(
            request,
            # Sin la cuenta de WhatsApp Business, Meta no deja gestionar plantillas.
            can_use_meta=bool(
                clinic.whatsapp_token and clinic.whatsapp_business_account_id
            ),
            **extra,
        )


class TemplateListView(TemplateScopedMixin, View):
    template_name = 'agent/settings/templates.html'

    def get(self, request):
        clinic = request.user.clinic
        templates = clinic.whatsapp_templates.all()

        counts = dict(
            templates.values_list('status').annotate(n=Count('pk')).order_by()
        )
        current = request.GET.get('estado', '')
        if current in Status.values:
            templates = templates.filter(status=current)
        else:
            current = ''

        filters = [{'value': '', 'label': 'Todas', 'count': sum(counts.values())}]
        for value, label in Status.choices:
            count = counts.get(value, 0)
            # Pausada y desactivada solo estorban cuando existe alguna.
            if count or value in (Status.DRAFT, Status.PENDING, Status.APPROVED, Status.REJECTED):
                filters.append({'value': value, 'label': label, 'count': count})

        return self.render_tab(
            request,
            templates=templates,
            filters=filters,
            current_status=current,
            total=filters[0]['count'],
            automatic_uses=self._automatic_uses(clinic),
            use_options=[
                {'value': value, 'action': copy['action']} for value, copy in PURPOSE_COPY.items()
            ],
        )

    @staticmethod
    def _automatic_uses(clinic):
        """Estado de cada envío automático: sin plantilla marcada está apagado, y no se ve de otra forma."""
        marked = {t.purpose: t for t in clinic.whatsapp_templates.exclude(purpose='')}
        uses = []
        for value, copy in PURPOSE_COPY.items():
            template = marked.get(value)
            if template is None:
                state = 'off'
            elif template.why_not_automatic() or template.reminder_map_problem():
                state = 'blocked'
            else:
                state = 'on'
            uses.append({'purpose': value, 'name': copy['name'], 'state': state, 'template': template})
        return uses


class TemplateFormView(TemplateScopedMixin, View):
    """Crear (sin `pk`) o editar. Una plantilla que no se puede editar se muestra en solo lectura."""

    template_name = 'agent/settings/template_form.html'

    def get(self, request, pk=None):
        template = self.get_template(pk) if pk else None
        form = WhatsAppTemplateForm(instance=template, clinic=request.user.clinic)
        return self._render(request, form, template)

    def post(self, request, pk=None):
        template = self.get_template(pk) if pk else None
        if template is not None and not template.is_editable:
            messages.error(request, 'Esta plantilla ya no se puede editar. Duplícala para hacer una versión nueva.')
            return redirect('agent_settings:template-edit', pk=template.pk)

        form = WhatsAppTemplateForm(request.POST, instance=template, clinic=request.user.clinic)
        if not form.is_valid():
            return self._render(request, form, template)

        saved = form.save(commit=False)
        saved.updated_by = request.user
        saved.save()
        messages.success(
            request,
            'Plantilla guardada. Cuando la tengas lista, envíala a revisión de Meta.'
            if saved.status == Status.DRAFT else 'Plantilla guardada.',
        )
        return redirect('agent_settings:template-edit', pk=saved.pk)

    def _render(self, request, form, template):
        return self.render_tab(
            request,
            form=form,
            template=template,
            readonly=template is not None and not template.is_editable,
            init={
                'name': form['name'].value() or '',
                'header': form['header_text'].value() or '',
                'body': form['body'].value() or '',
                'footer': form['footer'].value() or '',
                'examples': form.examples,
                'dirty': form.is_bound,
            },
        )


class TemplateActionView(TemplateScopedMixin, View):
    """Acciones de un solo clic sobre una plantilla (siempre POST)."""

    http_method_names = ['post']
    action = None

    def post(self, request, pk):
        template = self.get_template(pk)
        return getattr(self, f'do_{self.action}')(request, template)

    def do_submit(self, request, template):
        try:
            wa_templates.submit_template(template, user=request.user)
        except wa_templates.TemplateNotSubmittable as exc:
            messages.error(request, str(exc))
        except WhatsAppError as exc:
            messages.error(request, f'No se pudo enviar a revisión. {exc}')
        else:
            messages.success(
                request,
                f'«{template.name}» enviada a revisión. Meta suele responder en unos minutos; '
                'usa «Actualizar estados» para ver el resultado.',
            )
        return redirect('agent_settings:templates')

    def do_delete(self, request, template):
        if not template.is_deletable:
            messages.error(request, 'Solo se pueden eliminar borradores que no se hayan enviado a Meta.')
        else:
            name = template.name
            template.delete()
            messages.success(request, f'Borrador «{name}» eliminado.')
        return redirect('agent_settings:templates')

    def do_clear_purpose(self, request, template):
        copy = PURPOSE_COPY.get(template.purpose)
        template.purpose = ''
        template.updated_by = request.user
        template.save()
        messages.success(
            request,
            f'«{copy["name"]}» queda desactivado: no se enviará por WhatsApp hasta que marques otra plantilla.'
            if copy else 'La plantilla deja de usarse en envíos automáticos.',
        )
        return redirect('agent_settings:templates')

    def do_duplicate(self, request, template):
        name = f'{template.name}_copia'
        suffix = 1
        taken = set(
            request.user.clinic.whatsapp_templates.filter(language=template.language)
            .values_list('name', flat=True)
        )
        while name in taken:
            suffix += 1
            name = f'{template.name}_copia{suffix}'

        copy = WhatsAppTemplate.objects.create(
            clinic=template.clinic,
            name=name,
            language=template.language,
            category=template.category,
            header_text=template.header_text,
            body=template.body,
            footer=template.footer,
            body_examples=template.body_examples,
            updated_by=request.user,
        )
        messages.success(request, f'Copia creada como borrador: «{copy.name}».')
        return redirect('agent_settings:template-edit', pk=copy.pk)


class TemplateSubmitView(TemplateActionView):
    action = 'submit'


class TemplateDeleteView(TemplateActionView):
    action = 'delete'


class TemplateDuplicateView(TemplateActionView):
    action = 'duplicate'


class TemplateClearPurposeView(TemplateActionView):
    action = 'clear_purpose'


class TemplateReminderView(TemplateScopedMixin, View):
    """Elegir qué dato de la cita va en cada variable y dejar la plantilla para un envío automático.

    El envío (`?uso=`) es uno de `PURPOSE_COPY`: el recordatorio de 24 h o un aviso de cita.
    """

    template_name = 'agent/settings/template_reminder.html'

    def get_use(self, request, pk):
        """La plantilla y el uso pedido, o None si el uso no existe o la plantilla no sirve."""
        template = self.get_template(pk)
        purpose = request.POST.get('uso') or request.GET.get('uso') or template.purpose or Purpose.REMINDER_24H
        if purpose not in PURPOSE_COPY:
            return template, None, 'Ese envío automático no existe.'
        return template, purpose, template.why_not_automatic()

    def get(self, request, pk):
        template, purpose, reason = self.get_use(request, pk)
        if reason:
            messages.error(request, f'No se puede usar así. {reason}')
            return redirect('agent_settings:templates')
        keep = template.variable_map if template.purpose == purpose else None
        chosen = keep or default_map(template.variables)
        return self._render(request, template, purpose, chosen)

    def post(self, request, pk):
        template, purpose, reason = self.get_use(request, pk)
        if reason:
            messages.error(request, f'No se puede usar así. {reason}')
            return redirect('agent_settings:templates')

        allowed = {field.key for field in fields_for(purpose)}
        chosen = {str(n): request.POST.get(f'field_{n}', '') for n in template.variables}
        if any(key not in allowed for key in chosen.values()):
            messages.error(request, 'Elige un dato para cada variable.')
            return self._render(request, template, purpose, chosen)

        with transaction.atomic():
            # Una por uso y clínica: la que lo era hasta ahora deja de serlo. Con save(),
            # no con update(): está en la auditoría.
            for previous in request.user.clinic.whatsapp_templates.filter(
                purpose=purpose
            ).exclude(pk=template.pk):
                previous.purpose = ''
                previous.updated_by = request.user
                previous.save()
            template.purpose = purpose
            template.variable_map = chosen
            template.updated_by = request.user
            template.save()
        copy = PURPOSE_COPY[purpose]
        messages.success(
            request,
            f'«{template.name}» es ahora el «{copy["name"].lower()}». '
            f'Cada paciente lo recibirá {copy["when"]}.',
        )
        return redirect('agent_settings:templates')

    def _render(self, request, template, purpose, chosen):
        clinic = request.user.clinic
        examples = {'clinic_name': clinic.name}
        address = ', '.join(part for part in (clinic.address, clinic.city) if part)
        if address:
            examples['clinic_address'] = address
        fields = [
            {'key': f.key, 'label': f.label, 'example': examples.get(f.key, f.example)}
            for f in fields_for(purpose)
        ]
        return self.render_tab(
            request,
            template=template,
            purpose=purpose,
            copy=PURPOSE_COPY[purpose],
            fields=fields,
            init={
                'body': template.body,
                'header': template.header_text,
                'footer': template.footer,
                'variables': template.variables,
                'chosen': chosen,
                'fields': [{'key': f['key'], 'example': f['example']} for f in fields],
            },
            variables=template.variables,
            chosen=chosen,
        )


class TemplateSyncView(TemplateScopedMixin, View):
    """Trae el estado de Meta de todas las plantillas e importa las que falten."""

    http_method_names = ['post']

    def post(self, request):
        try:
            updated, imported = wa_templates.sync_templates(request.user.clinic)
        except WhatsAppError as exc:
            messages.error(request, f'No se pudo consultar a Meta. {exc}')
        else:
            if updated or imported:
                parts = []
                if updated:
                    parts.append(f'{updated} cambiaron de estado')
                if imported:
                    parts.append(f'{imported} se importaron de Meta')
                messages.success(request, 'Estados actualizados: ' + ' y '.join(parts) + '.')
            else:
                messages.info(request, 'Todo al día: no hay cambios en Meta.')
        return redirect('agent_settings:templates')
