"""Captura de escrituras sobre los modelos registrados.

`pre_save` guarda el estado anterior en la propia instancia, `post_save` calcula
el diff contra él y `post_delete` deja constancia de la baja.

LIMITACIÓN IMPORTANTE — las operaciones en bloque del ORM NO emiten señales y
por tanto NO quedan auditadas:

    Modelo.objects.bulk_create([...])     # sin post_save
    queryset.update(campo=valor)          # sin pre_save/post_save
    queryset.delete()                     # sin post_delete
    queryset.bulk_update([...], [...])    # sin señales

Sobre un modelo auditado hay que recorrer e invocar `instance.save()` /
`instance.delete()`. Está explicado en el README de la app.
"""
from __future__ import annotations

import json

from django.core.serializers.json import DjangoJSONEncoder
from django.db.models.signals import post_delete, post_save, pre_save

from audit.context import get_audit_context, resolve_actor
from audit.writer import write_log

_PREVIOUS_ATTR = '_audit_previous_state'


def _dispatch_uid(model) -> str:
    return f'audit:{model._meta.label_lower}'


def connect_signals(model) -> None:
    uid = _dispatch_uid(model)
    pre_save.connect(capture_previous_state, sender=model, dispatch_uid=uid)
    post_save.connect(log_save, sender=model, dispatch_uid=uid)
    post_delete.connect(log_delete, sender=model, dispatch_uid=uid)


def disconnect_signals(model) -> None:
    uid = _dispatch_uid(model)
    pre_save.disconnect(sender=model, dispatch_uid=uid)
    post_save.disconnect(sender=model, dispatch_uid=uid)
    post_delete.disconnect(sender=model, dispatch_uid=uid)


# ---------------------------------------------------------------------------
# Serialización de valores
# ---------------------------------------------------------------------------

def _jsonable(value):
    """Convierte a algo que quepa en un JSONField sin reventar.

    `DjangoJSONEncoder` cubre fechas, `Decimal`, UUID y `Promise`. Lo que no
    cubre (ficheros, objetos raros) cae a `str()`: es un log, no un backup.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    try:
        return json.loads(json.dumps(value, cls=DjangoJSONEncoder))
    except (TypeError, ValueError):
        return str(value)


def _is_empty(value) -> bool:
    return value is None or value == '' or value == [] or value == {}


def _snapshot(instance, config) -> dict:
    """Valores actuales de los campos concretos no excluidos.

    Los ManyToMany quedan fuera a propósito: no emiten `post_save` (van por
    `m2m_changed`, fuera del alcance de esta versión).
    """
    snapshot = {}
    for db_field in instance._meta.concrete_fields:
        name = db_field.name
        if name in config.exclude:
            continue
        snapshot[name] = _jsonable(db_field.value_from_object(instance))
    return snapshot


def _mask(config, field_name, before, after) -> dict:
    """Entrada del diff, enmascarada si el campo es sensible.

    De un campo sensible se registra que cambió, nunca qué valor tenía. El log
    de auditoría no puede convertirse en una segunda copia sin cifrar de la
    historia clínica.
    """
    if config.is_sensitive(field_name):
        return {'changed': True}
    return {'before': before, 'after': after}


# ---------------------------------------------------------------------------
# Receptores
# ---------------------------------------------------------------------------

def capture_previous_state(sender, instance, raw=False, **kwargs):
    """Guarda el estado en base de datos ANTES de escribir, para el diff."""
    if raw:
        return
    from audit import registry

    config = registry.get_config(sender)
    if config is None:
        return

    previous = None
    if instance.pk is not None:
        stored = sender._base_manager.filter(pk=instance.pk).first()
        if stored is not None:
            previous = _snapshot(stored, config)
    setattr(instance, _PREVIOUS_ATTR, previous)


def log_save(sender, instance, created=False, raw=False, **kwargs):
    if raw:
        # Carga de fixtures: no es una acción de nadie.
        return
    from audit import registry
    from audit.models import ChangeLog

    config = registry.get_config(sender)
    if config is None:
        return

    previous = getattr(instance, _PREVIOUS_ATTR, None)
    current = _snapshot(instance, config)

    if created or previous is None:
        action = ChangeLog.Action.CREATE
        changes = {
            name: _mask(config, name, None, value)
            for name, value in current.items()
            if not _is_empty(value)
        }
    else:
        action = ChangeLog.Action.UPDATE
        changes = {
            name: _mask(config, name, previous.get(name), value)
            for name, value in current.items()
            if previous.get(name) != value
        }
        if not changes:
            # Un `save()` que no cambió nada no es un evento. Registrarlo solo
            # llenaría el log de ruido y escondería los cambios de verdad.
            return

    setattr(instance, _PREVIOUS_ATTR, None)

    _write_change(
        action=action,
        instance=instance,
        config=config,
        changes=changes,
        deleted=False,
    )


def log_delete(sender, instance, **kwargs):
    from audit import registry
    from audit.models import ChangeLog

    config = registry.get_config(sender)
    if config is None:
        return

    changes = {
        name: _mask(config, name, value, None)
        for name, value in _snapshot(instance, config).items()
        if not _is_empty(value)
    }

    _write_change(
        action=ChangeLog.Action.DELETE,
        instance=instance,
        config=config,
        changes=changes,
        deleted=True,
    )


def _write_change(*, action, instance, config, changes, deleted):
    from django.contrib.contenttypes.models import ContentType

    from audit.models import ChangeLog

    context = get_audit_context()
    user, user_repr, origin = resolve_actor(context)
    patient = config.resolve_patient(instance)

    if deleted:
        # En una baja, la fila referenciada puede haber desaparecido ya (la
        # propia instancia, o el paciente en un borrado en cascada). Apuntar el
        # FK a algo inexistente reventaría por integridad referencial, así que
        # se deja a nulo: `object_repr` y `user_repr` conservan la identidad.
        patient = _still_exists(patient)
        user = _still_exists(user)

    write_log(
        ChangeLog,
        user=user,
        user_repr=user_repr,
        patient=patient,
        content_type=ContentType.objects.get_for_model(instance, for_concrete_model=False),
        object_id=str(instance.pk or ''),
        object_repr=_safe_str(instance),
        model_label=instance._meta.label,
        action=action,
        changes=changes,
        ip=context.ip,
        user_agent=context.user_agent,
        origin=origin,
    )


def _still_exists(obj):
    """`obj` si su fila sigue en la base de datos, `None` si ya no está.

    Se accede al modelo por `_meta.model` y no por `type(obj)`: `request.user` es
    un `SimpleLazyObject` y su `type()` no es el modelo.
    """
    if obj is None or obj.pk is None:
        return None
    model = obj._meta.model
    return obj if model._base_manager.filter(pk=obj.pk).exists() else None


def _safe_str(instance) -> str:
    try:
        return str(instance)[:255]
    except Exception:
        return f'{instance._meta.label}#{instance.pk}'[:255]
