"""Plantillas de WhatsApp: envío a revisión y sincronización de estados con Meta.

El texto se redacta en el panel (`WhatsAppTemplate`); aquí se traduce al formato
de componentes de la Graph API y se trae de vuelta lo que Meta decide. El estado
local es un reflejo del de Meta: solo `submit_template()` y `sync_templates()`
lo mueven.
"""

from django.db import transaction
from django.utils import timezone

from agent import whatsapp
from agent.models import WhatsAppTemplate

Status = WhatsAppTemplate.Status

# Estados de Meta → los nuestros. `IN_APPEAL` y `LIMIT_EXCEEDED` no tienen
# equivalente propio: el primero sigue en revisión, el segundo sigue aprobada.
META_STATUS = {
    'APPROVED': Status.APPROVED,
    'PENDING': Status.PENDING,
    'IN_APPEAL': Status.PENDING,
    'REJECTED': Status.REJECTED,
    'PAUSED': Status.PAUSED,
    'DISABLED': Status.DISABLED,
    'LIMIT_EXCEEDED': Status.APPROVED,
}

# Cuando Meta no da motivo, no se deja el rechazo mudo.
NO_REASON = {'NONE', 'UNKNOWN', ''}


class TemplateNotSubmittable(Exception):
    """La plantilla no está en un estado que se pueda enviar a revisión."""


def build_components(template):
    """Componentes `HEADER`/`BODY`/`FOOTER` de la plantilla en el formato de la Graph API."""
    components = []
    if template.header_text:
        components.append({'type': 'HEADER', 'format': 'TEXT', 'text': template.header_text})

    body = {'type': 'BODY', 'text': template.body}
    if template.variables:
        body['example'] = {'body_text': [list(template.body_examples)]}
    components.append(body)

    if template.footer:
        components.append({'type': 'FOOTER', 'text': template.footer})
    return components


def submit_template(template, user=None):
    """Envía la plantilla a revisión de Meta y la deja «En revisión».

    Si Meta ya la conoce (se rechazó y se corrigió), se actualiza esa misma en
    vez de crear otra con el nombre repetido. Lanza ``whatsapp.WhatsAppError``
    si Meta la rechaza de entrada; entonces no cambia nada.
    """
    if template.status not in (Status.DRAFT, Status.REJECTED) or template.has_unsupported_components:
        raise TemplateNotSubmittable('Solo se puede enviar una plantilla en borrador o rechazada.')

    clinic = template.clinic
    components = build_components(template)
    if template.meta_template_id:
        response = whatsapp.update_template(
            clinic, template.meta_template_id, {'category': template.category, 'components': components}
        )
    else:
        response = whatsapp.create_template(clinic, {
            'name': template.name,
            'language': template.language,
            'category': template.category,
            'components': components,
        })

    template.meta_template_id = str(response.get('id') or template.meta_template_id)
    template.status = META_STATUS.get(response.get('status'), Status.PENDING)
    template.rejection_reason = ''
    template.submitted_at = timezone.now()
    template.status_synced_at = template.submitted_at
    template.updated_by = user
    template.save()
    return template


def _parse_components(components):
    """(header, body, footer, soportado) de los componentes que devuelve Meta."""
    header = body = footer = ''
    supported = True
    for component in components or []:
        kind = component.get('type')
        if kind == 'HEADER' and component.get('format') == 'TEXT':
            header = component.get('text', '')
        elif kind == 'BODY':
            body = component.get('text', '')
        elif kind == 'FOOTER':
            footer = component.get('text', '')
        else:
            supported = False
    return header, body, footer, supported


def _examples_from(components):
    for component in components or []:
        if component.get('type') == 'BODY':
            rows = (component.get('example') or {}).get('body_text') or [[]]
            return list(rows[0])
    return []


def sync_templates(clinic):
    """Trae de Meta el estado de las plantillas de la clínica e importa las que falten.

    Devuelve ``(actualizadas, importadas)``. Una plantilla local en borrador que
    Meta no conoce no se toca. Las importadas con elementos que el panel no edita
    quedan en solo lectura. Lanza ``whatsapp.WhatsAppError`` si no se puede
    consultar a Meta.
    """
    remote = whatsapp.list_templates(clinic)
    now = timezone.now()

    local = list(clinic.whatsapp_templates.all())
    by_meta_id = {t.meta_template_id: t for t in local if t.meta_template_id}
    by_identity = {(t.name, t.language): t for t in local}

    updated = imported = 0
    with transaction.atomic():
        for item in remote:
            template = by_meta_id.get(str(item.get('id'))) or by_identity.get(
                (item.get('name'), item.get('language'))
            )
            status = META_STATUS.get(item.get('status'))
            if status is None:
                continue
            reason = item.get('rejected_reason') or ''
            reason = '' if reason in NO_REASON else reason

            if template is None:
                header, body, footer, supported = _parse_components(item.get('components'))
                category = item.get('category')
                if category not in WhatsAppTemplate.Category.values:
                    # AUTHENTICATION y las que Meta añada: se ven, no se editan.
                    supported = False
                    category = WhatsAppTemplate.Category.UTILITY
                if item.get('language') not in WhatsAppTemplate.Language.values:
                    # Idioma que el panel no ofrece: se salta antes que
                    # guardar un código que el selector no sabe pintar.
                    continue
                WhatsAppTemplate.objects.create(
                    clinic=clinic,
                    name=item['name'],
                    language=item['language'],
                    category=category,
                    header_text=header,
                    body=body,
                    footer=footer,
                    body_examples=_examples_from(item.get('components')),
                    status=status,
                    rejection_reason=reason,
                    meta_template_id=str(item['id']),
                    has_unsupported_components=not supported,
                    status_synced_at=now,
                )
                imported += 1
                continue

            # Un borrador (nuevo, o rechazado y corregido) tiene texto local sin
            # enviar: el estado de la versión anterior que Meta conserva no lo pisa.
            if template.status == Status.DRAFT:
                continue

            changed = (
                template.status != status
                or template.rejection_reason != reason
                or template.meta_template_id != str(item['id'])
            )
            if not changed:
                continue
            template.status = status
            template.rejection_reason = reason
            template.meta_template_id = str(item['id'])
            template.status_synced_at = now
            template.save()
            updated += 1
    return updated, imported
