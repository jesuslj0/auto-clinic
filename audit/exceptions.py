class AuditLogImmutable(Exception):
    """Se ha intentado modificar o borrar un registro de auditoría.

    Los registros son de solo inserción por diseño: un log que se puede editar
    no prueba nada.
    """


class AuditWriteError(Exception):
    """No se ha podido escribir el registro de auditoría.

    Con la política `fail_closed` (la de por defecto) esta excepción se propaga
    y aborta la operación que se estaba auditando.
    """
