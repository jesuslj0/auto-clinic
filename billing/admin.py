from django.contrib import admin

from billing.models import PatientInvoice, Payment, Subscription


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('clinic', 'plan_name', 'status', 'starts_at', 'ends_at', 'auto_renew')
    list_filter = ('status', 'auto_renew')
    search_fields = ('clinic__name', 'plan_name')


@admin.register(PatientInvoice)
class PatientInvoiceAdmin(admin.ModelAdmin):
    """Solo lectura sobre lo emitido.

    El admin no es una puerta trasera al documento: lo congelado se mira, no se
    edita. Emitir y anular pasan por `issue()`/`void()`, que es donde están las
    reglas.
    """

    list_display = ('__str__', 'clinic', 'patient', 'status', 'total', 'issued_at')
    list_filter = ('status', 'clinic')
    search_fields = ('number', 'frozen_patient_name')
    readonly_fields = (
        'number', 'issued_at', 'total', 'lines', 'frozen_patient_name',
        'frozen_created_by_name', 'voided_at',
        'created_at', 'updated_at', 'deleted_at',
    )
    raw_id_fields = ('patient', 'created_by')

    def get_queryset(self, request):
        return PatientInvoice.all_objects.select_related(
            'clinic', 'patient', 'created_by__user'
        )

    def save_model(self, request, obj, form, change):
        # Mismo gesto que en el admin clínico: quien da de alta desde aquí es
        # quien la registra, salvo que se haya dicho otra cosa.
        if not change and obj.created_by_id is None:
            obj.created_by = getattr(request.user, 'professional_profile', None)
        super().save_model(request, obj, form, change)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    """Solo lectura: un recibo no se corrige ni se borra.

    El alta sí se permite —es la vía de registrar un cobro mientras no haya
    pantalla— y pasa por el `save()` del modelo, que es donde están las reglas.
    """

    list_display = (
        '__str__', 'clinic', 'frozen_invoice_number', 'amount', 'method',
        'paid_at', 'frozen_created_by_name',
    )
    list_filter = ('method', 'clinic')
    search_fields = ('receipt_number', 'frozen_invoice_number', 'frozen_patient_name')
    readonly_fields = (
        'receipt_number', 'frozen_patient_name', 'frozen_invoice_number',
        'frozen_created_by_name', 'created_at', 'updated_at', 'deleted_at',
    )
    raw_id_fields = ('invoice', 'created_by')

    def get_queryset(self, request):
        return Payment.all_objects.select_related(
            'clinic', 'invoice', 'created_by__user'
        )

    def save_model(self, request, obj, form, change):
        if not change and obj.created_by_id is None:
            obj.created_by = getattr(request.user, 'professional_profile', None)
        super().save_model(request, obj, form, change)

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
