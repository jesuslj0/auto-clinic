"""Registro explícito de los modelos auditados.

Nada se audita automáticamente. Cada app declara qué modelos suyos entran, desde
su `AppConfig.ready()`:

    from audit import registry

    class PatientsConfig(AppConfig):
        def ready(self):
            from patients.models import Patient
            registry.register(Patient, sensitive=['notes', 'date_of_birth'])

Las señales se conectan con `sender=modelo`, así que un modelo no registrado
nunca llega siquiera a ejecutar el receptor: no hay ruido ni coste.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from django.core.exceptions import ImproperlyConfigured
from django.db import models

# Campos que solo generan ruido en el diff: cambian en cada guardado sin aportar
# información, o son secretos que no deben acabar en el log.
DEFAULT_EXCLUDED_FIELDS = frozenset({
    'created_at',
    'updated_at',
    'modified_at',
    'last_login',
    'date_joined',
    'password',
})


@dataclass(frozen=True)
class AuditedModelConfig:
    model: type[models.Model]
    exclude: frozenset[str] = field(default_factory=frozenset)
    sensitive: frozenset[str] = field(default_factory=frozenset)
    patient_resolver: Callable | None = None

    def resolve_patient(self, instance):
        """Paciente al que atribuir el evento, o `None`."""
        resolver = self.patient_resolver or default_patient_resolver
        try:
            return resolver(instance)
        except Exception:
            # Resolver el paciente puede fallar en un borrado en cascada, donde
            # la fila relacionada ya no existe. Un log sin paciente sigue siendo
            # mejor que una operación abortada por esto.
            return None

    def is_sensitive(self, field_name: str) -> bool:
        return field_name in self.sensitive


_registry: dict[type[models.Model], AuditedModelConfig] = {}


def default_patient_resolver(instance):
    """Heurística por defecto: el propio paciente, o su FK `patient`.

    Se sustituye con `patient_resolver=` cuando el modelo cuelga del paciente
    por un camino más largo (por ejemplo, una futura nota clínica que llegue al
    paciente a través del episodio).
    """
    from patients.models import Patient

    if isinstance(instance, Patient):
        return instance
    if getattr(instance, 'patient_id', None):
        return instance.patient
    return None


def register(model, *, exclude=(), sensitive=(), patient_resolver=None) -> AuditedModelConfig:
    """Da de alta un modelo en la auditoría de cambios.

    - `exclude`: campos que no se miran para el diff (se suman a
      `DEFAULT_EXCLUDED_FIELDS`).
    - `sensitive`: campos de los que se registra QUE cambiaron pero nunca su
      valor. Para datos clínicos y secretos.
    - `patient_resolver`: `callable(instancia) -> Patient | None`.
    """
    from audit.managers import AppendOnlyModel
    from audit.signals import connect_signals

    if issubclass(model, AppendOnlyModel):
        raise ImproperlyConfigured(
            f'{model.__name__} es un registro de auditoría y no puede auditarse a '
            f'sí mismo: sería una recursión infinita.'
        )

    unknown = (set(exclude) | set(sensitive)) - {f.name for f in model._meta.get_fields()}
    if unknown:
        raise ImproperlyConfigured(
            f'Campos inexistentes en {model.__name__}: {sorted(unknown)}.'
        )

    config = AuditedModelConfig(
        model=model,
        exclude=frozenset(exclude) | DEFAULT_EXCLUDED_FIELDS,
        sensitive=frozenset(sensitive),
        patient_resolver=patient_resolver,
    )
    _registry[model] = config
    connect_signals(model)
    return config


def unregister(model) -> None:
    """Da de baja un modelo. Pensado para tests."""
    from audit.signals import disconnect_signals

    _registry.pop(model, None)
    disconnect_signals(model)


def get_config(model) -> AuditedModelConfig | None:
    return _registry.get(model)


def is_registered(model) -> bool:
    return model in _registry


def registered_models():
    return tuple(_registry)
