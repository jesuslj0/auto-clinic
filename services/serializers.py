from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from core.serializers import ClinicScopedSerializerMixin
from services.models import Service, ServiceCategory

# Campos que definen si el precio y la duración son fijos o un rango. Se validan
# juntos porque la regla del máximo depende del tipo y del mínimo.
RANGE_FIELDS = (
    'price',
    'price_type',
    'price_max',
    'duration_minutes',
    'duration_type',
    'duration_max_minutes',
)


class ServiceCategorySerializer(ClinicScopedSerializerMixin, serializers.ModelSerializer):
    service_count = serializers.IntegerField(source='services.count', read_only=True)

    class Meta:
        model = ServiceCategory
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at')


class ServiceSerializer(ClinicScopedSerializerMixin, serializers.ModelSerializer):
    price_display = serializers.ReadOnlyField()
    duration_display = serializers.ReadOnlyField()
    booking_duration_minutes = serializers.ReadOnlyField()
    category_name = serializers.CharField(source='category.name', read_only=True, default=None)
    category_color = serializers.CharField(source='category.color', read_only=True, default=None)

    class Meta:
        model = Service
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at')

    def validate(self, attrs):
        """
        Aplica la misma regla del rango que el panel web.

        DRF no ejecuta `Model.clean()`, así que se instancia un `Service` de
        usar y tirar con los valores efectivos (los del payload, y los de la
        instancia para lo que un PATCH no toque) y se delega en él: la regla
        vive en un único sitio.
        """
        attrs = super().validate(attrs)

        categoria = attrs.get('category') or (self.instance.category if self.instance else None)
        clinica = self._forced_clinic() or attrs.get('clinic') or (self.instance.clinic if self.instance else None)
        if categoria is not None and clinica is not None and categoria.clinic_id != clinica.pk:
            raise serializers.ValidationError({'category': 'La categoría pertenece a otra clínica.'})

        def valor(campo):
            if campo in attrs:
                return attrs[campo]
            if self.instance is not None:
                return getattr(self.instance, campo)
            return Service._meta.get_field(campo).get_default()

        tentativo = Service(**{campo: valor(campo) for campo in RANGE_FIELDS})
        try:
            tentativo.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict)

        # `clean()` descarta los máximos de un valor fijo: hay que reflejarlo en
        # attrs o se guardaría el máximo huérfano que traía la petición.
        attrs['price_max'] = tentativo.price_max
        attrs['duration_max_minutes'] = tentativo.duration_max_minutes
        return attrs
