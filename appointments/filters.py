"""Filtrado del listado de citas del panel.

Mismo planteamiento que `billing.filters`: los parámetros del GET se normalizan
UNA vez en un objeto inmutable, y de ahí salen tanto el queryset como las URLs de
orden y paginación (`as_params()`). La query string no se vuelve a leer a mano en
ningún otro sitio, que es lo que hace que ordenar o pasar de página no pierda los
filtros ni los duplique.

Dos convenios que conviene tener presentes:

- **Ausente no es lo mismo que vacío.** Sin `desde`/`hasta` en la dirección se
  entiende «el mes en curso»; con `desde=`/`hasta=` vacíos, «cualquier fecha».
  Igual con `profesional`: ausente es «lo mío», vacío es «todos». Es la misma
  convención que ya tenía el filtro de día de esta pantalla, y es lo que permite
  que el panel de control enlace a «todas las pendientes, de toda la clínica».
- **El filtro de procedimiento cruza a la capa clínica**, y un JOIN no pasa por
  el manager del modelo relacionado. Por eso el `deleted_at` de la visita aparece
  explícito: un procedimiento cuya visita se dio de baja no debe hacer que su
  cita cuente como «con procedimiento».
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, replace
from datetime import date

from django.db.models import Exists, OuterRef
from django.utils import timezone
from django.utils.dateparse import parse_date

#: Valores del filtro de procedimiento. Viajan en castellano porque la URL del
#: panel se lee y se comparte.
PROCEDURE_WITH = 'con'
PROCEDURE_WITHOUT = 'sin'

PROCEDURE_CHOICES = [
    (PROCEDURE_WITH, 'Con procedimiento'),
    (PROCEDURE_WITHOUT, 'Sin procedimiento'),
]

DEFAULT_SORT = 'asc'


def _date_or_none(raw):
    """Fecha ISO de la query string, o `None`. Un `<input type="date">` la manda así."""
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        return parse_date(raw)
    except ValueError:
        return None


def _int_or_none(raw):
    """Entero de la query string, o `None` si no lo es o viene vacío."""
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def month_bounds(day: date) -> tuple[date, date]:
    """Primer y último día del mes al que pertenece `day`."""
    last = calendar.monthrange(day.year, day.month)[1]
    return day.replace(day=1), day.replace(day=last)


def annotate_procedures(queryset):
    """Marca cada cita con `has_procedure`: si tiene algún procedimiento hecho.

    Se anota siempre, se filtre o no por ello: la tabla enseña el distintivo, y
    resolverlo aquí evita una consulta por fila al pintarlo.

    `Exists` y no un `Count` sobre el JOIN porque la pregunta es de sí o no: unir
    a los procedimientos multiplicaría las filas de una cita con dos y rompería
    el recuento del paginador.

    Los dos filtros de borrado lógico no sobran ni se solapan.
    `PerformedProcedure.objects` ya excluye los procedimientos dados de baja
    (`SoftDeleteModel`), pero el salto a `visit` es un JOIN y ese NO pasa por el
    manager de `Visit`: sin la condición explícita, una visita borrada seguiría
    arrastrando sus procedimientos.
    """
    from clinical.models import PerformedProcedure

    return queryset.annotate(
        has_procedure=Exists(
            PerformedProcedure.objects.filter(
                visit__appointment=OuterRef('pk'),
                visit__deleted_at__isnull=True,
            )
        )
    )


@dataclass(frozen=True)
class AppointmentFilters:
    """Los filtros del listado, ya normalizados.

    Inmutable a propósito: se construye una vez por petición con `from_query()`.
    Lo que llegue mal —una fecha imposible, un estado inventado, un id que no es
    un número— se descarta en silencio y cae al valor por defecto. Es una URL que
    puede venir pegada a mano, no un formulario que validar contra el usuario.
    """

    date_from: date | None = None
    date_to: date | None = None
    status: str = ''
    #: `None` es «todos los profesionales»; un id, ese profesional.
    professional: int | None = None
    #: '' todas, 'con' solo las que tienen procedimiento, 'sin' las que no.
    procedure: str = ''
    sort: str = DEFAULT_SORT

    # -- construcción -------------------------------------------------------

    @classmethod
    def from_query(cls, params, *, default_professional_id=None, today=None) -> 'AppointmentFilters':
        """Lee los filtros de un `request.GET` (o cualquier `QueryDict`).

        `default_professional_id` es a quién se filtra cuando la dirección no
        dice nada: la ficha de quien mira. Se pasa desde la vista porque este
        módulo no sabe de usuarios.
        """
        from appointments.models import Appointment

        today = today or timezone.localdate()
        month_start, month_end = month_bounds(today)

        # Ausente = el mes en curso. Presente y vacío = sin límite por ese lado.
        date_from = _date_or_none(params.get('desde')) if 'desde' in params else month_start
        date_to = _date_or_none(params.get('hasta')) if 'hasta' in params else month_end
        # Un rango del revés no devuelve nada y parece un error de la
        # aplicación. Se endereza, que es lo que el usuario quiso decir.
        if date_from and date_to and date_from > date_to:
            date_from, date_to = date_to, date_from

        valid_statuses = {value for value, _ in Appointment.Status.choices}
        status = (params.get('status') or '').strip()
        if status not in valid_statuses:
            status = ''

        # Ausente = la ficha de quien mira. Presente y vacío = todos.
        professional = (
            _int_or_none(params.get('profesional'))
            if 'profesional' in params
            else default_professional_id
        )

        procedure = (params.get('procedimiento') or '').strip()
        if procedure not in {PROCEDURE_WITH, PROCEDURE_WITHOUT}:
            procedure = ''

        sort = (params.get('sort') or '').strip()
        if sort not in ('asc', 'desc'):
            sort = DEFAULT_SORT

        return cls(
            date_from=date_from,
            date_to=date_to,
            status=status,
            professional=professional,
            procedure=procedure,
            sort=sort,
        )

    # -- aplicación ---------------------------------------------------------

    def apply(self, queryset):
        """Aplica los filtros. No ordena: de eso se encarga `order()`.

        Espera un queryset que ya venga por `annotate_procedures()`, porque el
        filtro de procedimiento se resuelve sobre esa anotación.
        """
        if self.date_from:
            queryset = queryset.filter(scheduled_at__date__gte=self.date_from)
        if self.date_to:
            queryset = queryset.filter(scheduled_at__date__lte=self.date_to)
        if self.status:
            queryset = queryset.filter(status=self.status)
        if self.professional is not None:
            queryset = queryset.filter(professional_id=self.professional)
        if self.procedure:
            queryset = queryset.filter(has_procedure=self.procedure == PROCEDURE_WITH)
        return queryset

    def order(self, queryset):
        return queryset.order_by('scheduled_at' if self.sort == 'asc' else '-scheduled_at')

    # -- reconstrucción de la URL -------------------------------------------

    def as_params(self) -> dict:
        """Los filtros como pares para la query string.

        Las fechas y el profesional viajan SIEMPRE, incluso vacíos, porque aquí
        su ausencia tiene significado propio: omitir un `desde` vacío convertiría
        un «todas las fechas» en «este mes» nada más pasar de página, y omitir un
        `profesional` vacío devolvería la lista a «lo mío».
        """
        params = {
            'desde': self.date_from.isoformat() if self.date_from else '',
            'hasta': self.date_to.isoformat() if self.date_to else '',
            'profesional': str(self.professional) if self.professional is not None else '',
        }
        if self.status:
            params['status'] = self.status
        if self.procedure:
            params['procedimiento'] = self.procedure
        params['sort'] = self.sort
        return params

    def toggled(self) -> 'AppointmentFilters':
        """Los mismos filtros con el orden por fecha invertido."""
        return replace(self, sort='desc' if self.sort == 'asc' else 'asc')
