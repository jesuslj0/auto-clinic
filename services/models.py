from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models

from core.models import Clinic, TimeStampedModel

# Categorías con las que arranca una clínica nueva, orientadas a podología.
# Son solo semillas: cada clínica puede renombrarlas, borrarlas o añadir las
# suyas, por eso viven como datos y no como choices del modelo.
DEFAULT_CATEGORIES = (
    ('Quiropodia', '#7c3aed'),
    ('Onicología', '#e879a6'),
    ('Biomecánica', '#0ea5e9'),
    ('Ortopodología', '#14b8a6'),
    ('Cirugía ungueal', '#f97316'),
    ('Pie diabético', '#ef4444'),
    ('Podología deportiva', '#84cc16'),
    ('Podología infantil', '#eab308'),
)

# Colores que se reparten a las categorías creadas desde el panel, en orden.
CATEGORY_PALETTE = [color for _, color in DEFAULT_CATEGORIES]


class ServiceCategory(TimeStampedModel):
    """
    Agrupación comercial del catálogo ("Quiropodia", "Biomecánica").

    Es un modelo y no un enum porque el catálogo lo define cada clínica: dos
    clínicas de la plataforma no tienen por qué compartir especialidades.
    """

    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name='service_categories')
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=7, blank=True, help_text='Color hexadecimal, p. ej. #7c3aed.')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        unique_together = ('clinic', 'name')
        verbose_name = 'categoría de servicio'
        verbose_name_plural = 'categorías de servicio'

    def __str__(self):
        return self.name

    @classmethod
    def next_color(cls, clinic):
        """Siguiente color de la paleta, rotando según lo que ya tenga la clínica."""
        usados = cls.objects.filter(clinic=clinic).count()
        return CATEGORY_PALETTE[usados % len(CATEGORY_PALETTE)]


class Service(TimeStampedModel):
    """
    Servicio del catálogo de una clínica.

    El precio y la duración pueden ser fijos o variables. En ambos casos
    `price` y `duration_minutes` guardan el valor de referencia: el valor exacto
    si es fijo, el mínimo del rango si es variable. El máximo es opcional, de
    modo que un servicio variable sin máximo se lee como "desde X".
    """

    class ValueType(models.TextChoices):
        FIXED = 'fixed', 'Fijo'
        VARIABLE = 'variable', 'Variable'

    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name='services')
    category = models.ForeignKey(
        ServiceCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='services',
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    duration_minutes = models.PositiveIntegerField(default=30)
    duration_type = models.CharField(max_length=10, choices=ValueType.choices, default=ValueType.FIXED)
    duration_max_minutes = models.PositiveIntegerField(null=True, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    price_type = models.CharField(max_length=10, choices=ValueType.choices, default=ValueType.FIXED)
    price_max = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        unique_together = ('clinic', 'name')
        verbose_name = 'servicio'
        verbose_name_plural = 'servicios'

    def __str__(self):
        return self.name

    # ------------------------------------------------------------------
    # Lectura del rango
    # ------------------------------------------------------------------

    @property
    def has_variable_price(self):
        return self.price_type == self.ValueType.VARIABLE

    @property
    def has_variable_duration(self):
        return self.duration_type == self.ValueType.VARIABLE

    @property
    def booking_duration_minutes(self):
        """
        Minutos que la cita ocupa en la agenda.

        Con duración variable se reserva el máximo: es lo único que garantiza
        que dos citas seguidas no se pisen si la primera se alarga. Sin máximo
        declarado no hay más información que el mínimo.
        """
        if self.has_variable_duration and self.duration_max_minutes:
            return self.duration_max_minutes
        return self.duration_minutes

    @staticmethod
    def _importe(valor):
        """`50.00` → `50`, `12.50` → `12.50`. Acepta Decimal, str o número."""
        importe = Decimal(str(valor))
        if importe == importe.to_integral_value():
            return f'{importe:.0f}'
        return f'{importe:.2f}'

    @property
    def price_display(self):
        """`40 €`, `40 – 80 €` o `Desde 40 €`."""
        precio = self._importe(self.price)
        if not self.has_variable_price:
            return f'{precio} €'
        if self.price_max is None:
            return f'Desde {precio} €'
        return f'{precio} – {self._importe(self.price_max)} €'

    @property
    def duration_display(self):
        """`30 min`, `30 – 60 min` o `Desde 30 min`."""
        if not self.has_variable_duration:
            return f'{self.duration_minutes} min'
        if self.duration_max_minutes is None:
            return f'Desde {self.duration_minutes} min'
        return f'{self.duration_minutes} – {self.duration_max_minutes} min'

    # ------------------------------------------------------------------
    # Validación
    # ------------------------------------------------------------------

    def clean(self):
        """
        El máximo solo tiene sentido por encima del mínimo, y solo cuando el
        valor es variable: en modo fijo se descarta para que no quede un
        máximo huérfano de una edición anterior.

        También se comprueba aquí que la categoría sea de la misma clínica: es
        una FK sin más para la base de datos, así que nada impediría colgar un
        servicio de la categoría de otro inquilino.
        """
        super().clean()
        errores = {}

        if self.category_id and self.clinic_id and self.category.clinic_id != self.clinic_id:
            errores['category'] = 'La categoría pertenece a otra clínica.'

        if self.has_variable_price:
            if (
                self.price_max is not None
                and self.price is not None
                # Normalizados porque una instancia sin releer de la base de
                # datos puede traer los importes como texto.
                and Decimal(str(self.price_max)) <= Decimal(str(self.price))
            ):
                errores['price_max'] = 'El precio máximo debe ser mayor que el mínimo.'
        else:
            self.price_max = None

        if self.has_variable_duration:
            if (
                self.duration_max_minutes is not None
                and self.duration_minutes is not None
                and self.duration_max_minutes <= self.duration_minutes
            ):
                errores['duration_max_minutes'] = 'La duración máxima debe ser mayor que la mínima.'
        else:
            self.duration_max_minutes = None

        if errores:
            raise ValidationError(errores)
