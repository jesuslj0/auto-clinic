"""Admin de las exportaciones: solo lectura.

Sirve para ver qué se ha pedido, quién y en qué estado. No se crean, editan ni borran
desde aquí: pedir una exportación exige un administrador de la clínica (y, para la
clínica entera, su contraseña y el segundo factor), y el admin de Django no pasa por
ninguna de esas comprobaciones. Tampoco enseña la clave del objeto del bucket.
"""
from django.contrib import admin

from exports.models import DataExport


@admin.register(DataExport)
class DataExportAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'clinic', 'scope', 'patient_label', 'status', 'requested_by_label',
                    'size_bytes', 'expires_at', 'downloads')
    list_filter = ('status', 'scope', 'clinic')
    search_fields = ('requested_by_label', 'clinic__name')
    exclude = ('file',)
    readonly_fields = [
        'id', 'clinic', 'requested_by', 'requested_by_label', 'scope', 'patient', 'patient_label',
        'status', 'size_bytes', 'checksum', 'counts', 'ready_at', 'expires_at', 'downloads', 'error',
        'created_at', 'updated_at',
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
