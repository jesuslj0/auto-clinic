"""Registros de auditoría.

Dos tablas independientes y deliberadamente separadas:

- `ChangeLog`  — toda ESCRITURA sobre un modelo registrado (alta, cambio, baja).
- `AccessLog`  — toda LECTURA de datos personales o clínicos.

No comparten tabla porque no son lo mismo: los cambios se capturan solos con
señales, los accesos hay que instrumentarlos vista a vista; los volúmenes
difieren en un orden de magnitud, y sus políticas de retención serán distintas.

Ninguno de los dos se audita a sí mismo: el registro solo se conecta a los
modelos que pasan por `audit.registry.register()`, y esa función rechaza
explícitamente cualquier subclase de `AppendOnlyModel`.
"""
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from audit.context import (
    ORIGIN_ADMIN,
    ORIGIN_API,
    ORIGIN_COMMAND,
    ORIGIN_N8N,
    ORIGIN_WEB,
)
from audit.managers import AppendOnlyModel


class Origin(models.TextChoices):
    WEB = ORIGIN_WEB, 'Panel web'
    API = ORIGIN_API, 'API'
    ADMIN = ORIGIN_ADMIN, 'Admin de Django'
    COMMAND = ORIGIN_COMMAND, 'Comando / tarea'
    N8N = ORIGIN_N8N, 'Agente n8n'


class BaseLog(AppendOnlyModel):
    """Campos comunes a los dos registros.

    Todos los FK son `DO_NOTHING` + `db_constraint=False`: dar de baja a un
    usuario o borrar un paciente no puede llevarse por delante la prueba de lo
    que pasó, ni tocar su fila de auditoría. Por eso cada FK va acompañado de su
    campo denormalizado (`user_repr`, `object_repr`), que conserva la identidad
    legible del momento del evento.

    El motivo de `DO_NOTHING` es la inmutabilidad a nivel de base de datos. El
    log es de solo inserción también bajo el ORM: el trigger
    `audit_prevent_modification` rechaza CUALQUIER `UPDATE` sobre estas tablas.
    Un `on_delete=SET_NULL` obligaría a Django a emitir un
    `UPDATE ... SET user_id = NULL` al borrar el usuario, que el trigger
    bloquearía, tumbando el borrado entero. Con `DO_NOTHING` no se emite ese
    `UPDATE`: al morir el usuario o el paciente, el FK queda apuntando a un id
    que ya no existe, y la identidad la conserva el campo `*_repr`. Un log que
    apunta a un id difunto es aceptable y previsto; un log que se puede reescribir
    no prueba nada.

    `db_constraint=False` es lo que hace que ese id colgado no reviente por
    integridad referencial: sin restricción en la base de datos, la columna
    simplemente guarda el entero huérfano. Ningún modelo debe apuntar a estas
    tablas con `on_delete=CASCADE`.
    """

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        related_name='+',
        db_constraint=False,
    )
    user_repr = models.CharField(
        max_length=255,
        blank=True,
        help_text=(
            'Identidad del actor en el momento del evento. Para el agente de '
            'n8n vale «n8n:<clinic_id>» y el FK queda a nulo, porque el agente '
            'no es un usuario del sistema.'
        ),
    )

    patient = models.ForeignKey(
        'patients.Patient',
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        related_name='+',
        db_constraint=False,
        help_text='Paciente afectado, cuando el evento se puede atribuir a uno.',
    )

    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)
    origin = models.CharField(
        max_length=20, choices=Origin.choices, default=Origin.COMMAND, db_index=True
    )

    class Meta:
        abstract = True


class ChangeLog(BaseLog):
    """Una escritura sobre un modelo registrado en auditoría."""

    class Action(models.TextChoices):
        CREATE = 'create', 'Alta'
        UPDATE = 'update', 'Modificación'
        DELETE = 'delete', 'Baja'

    # DO_NOTHING + db_constraint=False por el mismo motivo que `user` y
    # `patient`: borrar un ContentType no debe emitir un UPDATE sobre esta tabla,
    # que el trigger de inmutabilidad rechazaría. `model_label` conserva la
    # etiqueta legible aunque el ContentType desaparezca.
    content_type = models.ForeignKey(
        ContentType, on_delete=models.DO_NOTHING, null=True, blank=True,
        related_name='+', db_constraint=False,
    )
    # CharField y no entero: en este proyecto conviven PKs enteras (Patient) y
    # UUID (Appointment).
    object_id = models.CharField(max_length=255, blank=True, db_index=True)
    content_object = GenericForeignKey('content_type', 'object_id')
    object_repr = models.CharField(max_length=255, blank=True)
    # Etiqueta del modelo («patients.Patient») guardada aparte para poder
    # filtrar y leer el log aunque el ContentType desaparezca.
    model_label = models.CharField(max_length=100, blank=True, db_index=True)

    action = models.CharField(max_length=10, choices=Action.choices, db_index=True)

    changes = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            'Diff con la forma {campo: {"before": ..., "after": ...}}. Los campos '
            'marcados como sensibles al registrar el modelo aparecen como '
            '{campo: {"changed": true}}, sin valores: este log no puede ser una '
            'segunda copia sin cifrar de la historia clínica.'
        ),
    )

    class Meta:
        db_table = 'audit_change_log'
        ordering = ['-timestamp']
        verbose_name = 'registro de cambio'
        verbose_name_plural = 'registros de cambios'
        indexes = [
            # «Todo lo ocurrido sobre el paciente X»
            models.Index(fields=['patient', '-timestamp'], name='idx_changelog_patient'),
            # «Todo lo que hizo el usuario Y entre dos fechas»
            models.Index(fields=['user', '-timestamp'], name='idx_changelog_user'),
            # «Historial de este objeto concreto»
            models.Index(fields=['content_type', 'object_id'], name='idx_changelog_object'),
        ]

    def __str__(self):
        return f'{self.timestamp:%Y-%m-%d %H:%M} {self.get_action_display()} {self.object_repr}'


class AccessLog(BaseLog):
    """Una lectura de datos personales o clínicos.

    Las lecturas no generan señales de Django, así que esto se puebla desde la
    capa de vistas: `AccessLogMixin`, `@log_access_view` o `log_access()`.
    """

    class Action(models.TextChoices):
        VIEW = 'view', 'Ver ficha'
        LIST = 'list', 'Listar'
        SEARCH = 'search', 'Buscar'
        EXPORT = 'export', 'Exportar'
        PRINT = 'print', 'Imprimir'
        DOWNLOAD_ATTACHMENT = 'download_attachment', 'Descargar adjunto'
        # No es una lectura, pero vive aquí y no en `ChangeLog` por dónde acaba
        # el rastro: el registry excluye `password` de los diffs, así que un
        # cambio de contraseña no genera ningún `ChangeLog` — se perdería. El
        # modelo lo admite sin forzarlo: `patient` y `content_type` son nulos y
        # ya hay precedente de eventos sin objeto (`clinical/admin.py`).
        # Se registra el hecho, nunca la contraseña.
        PASSWORD_CHANGE = 'password_change', 'Cambio de contraseña'

    # Nulos en los listados: una búsqueda no apunta a un objeto concreto.
    # DO_NOTHING + db_constraint=False: ver la nota en `ChangeLog.content_type`.
    content_type = models.ForeignKey(
        ContentType, on_delete=models.DO_NOTHING, null=True, blank=True,
        related_name='+', db_constraint=False,
    )
    object_id = models.CharField(max_length=255, blank=True, db_index=True)
    content_object = GenericForeignKey('content_type', 'object_id')
    object_repr = models.CharField(max_length=255, blank=True)

    action = models.CharField(max_length=25, choices=Action.choices, db_index=True)

    path = models.CharField(max_length=500, blank=True)
    method = models.CharField(max_length=10, blank=True)
    result_count = models.PositiveIntegerField(
        null=True, blank=True, help_text='Número de registros devueltos en listados y búsquedas.'
    )

    reason = models.TextField(
        null=True,
        blank=True,
        help_text=(
            'Justificación del acceso. Reservado para accesos excepcionales que '
            'en el futuro requieran motivar la consulta.'
        ),
    )

    class Meta:
        db_table = 'audit_access_log'
        ordering = ['-timestamp']
        verbose_name = 'registro de acceso'
        verbose_name_plural = 'registros de accesos'
        indexes = [
            models.Index(fields=['patient', '-timestamp'], name='idx_accesslog_patient'),
            models.Index(fields=['user', '-timestamp'], name='idx_accesslog_user'),
            models.Index(fields=['content_type', 'object_id'], name='idx_accesslog_object'),
        ]

    def __str__(self):
        who = self.user_repr or 'anónimo'
        return f'{self.timestamp:%Y-%m-%d %H:%M} {who} {self.get_action_display()} {self.path}'
