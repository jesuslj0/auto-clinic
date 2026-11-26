from django.apps import AppConfig


class ExportsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'exports'
    verbose_name = 'Exportaciones de datos'

    def ready(self):
        from audit import registry

        from exports.models import DataExport

        # La exportación es un acceso masivo a datos de salud: que se pidió, quién
        # y cuándo debe poder reconstruirse. `file` es sensible porque es la clave
        # del objeto en el bucket: el log no debe ser un índice de ficheros.
        registry.register(DataExport, sensitive=['file'])
