"""Filtrado del listado global de procedimientos del panel.

Mismo planteamiento que `appointments.filters` y `billing.filters`: los
parámetros del GET se normalizan UNA vez en un objeto inmutable, y de ahí salen
el queryset y las URLs de orden y paginación (`as_params()`).

Convenios (los mismos del listado de citas):

- **Ausente no es lo mismo que vacío.** Sin `desde`/`hasta` en la dirección se
  entiende «el mes en curso»; con `desde=`/`hasta=` vacíos, «cualquier fecha».
- **Lo que se enseña es lo congelado.** Nombre e importe salen de
  `frozen_service_name` y `frozen_price`, nunca del catálogo; el catálogo solo se
  consulta para el desplegable de servicios y para filtrar por procedencia.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone

from appointments.filters import _date_or_none, _int_or_none, month_bounds
from billing.filters import scope_to_clinic

BILLING_PENDING = 'pendiente'
BILLING_DONE = 'facturado'

BILLING_CHOICES = [
    (BILLING_PENDING, 'Sin facturar'),
    (BILLING_DONE, 'Facturados'),
]

DEFAULT_SORT = 'desc'


def procedures_for(user):
    """Procedimientos que `user` puede ver, sin filtrar todavía.

    El borrado lógico se pone a mano en los tres niveles que se cruzan (visita,
    episodio e historia): un JOIN no pasa por el manager del modelo del otro lado,
    y sin esto un episodio dado de baja seguiría apareciendo aquí.

    `...patient__clinic__isnull=False` fuerza el JOIN con el paciente: la historia
    apunta a él con `db_constraint=False` (borrar un paciente no arrastra su
    documentación clínica), y una fila con el paciente ya inexistente no se podría
    pintar. Así no entra, en vez de reventar la tabla entera.
    """
    from clinical.models import PerformedProcedure

    queryset = PerformedProcedure.objects.filter(
        visit__deleted_at__isnull=True,
        visit__episode__deleted_at__isnull=True,
        visit__episode__history__deleted_at__isnull=True,
        visit__episode__history__patient__clinic__isnull=False,
    )
    return scope_to_clinic(queryset, user, 'visit__episode__history__patient__clinic')


@dataclass(frozen=True)
class ProcedureFilters:
    date_from: object = None
    date_to: object = None
    #: `None` es «todos los profesionales»; un id, quien registró el procedimiento.
    professional: int | None = None
    #: `None` es «todos»; un id, el servicio del catálogo del que salió.
    service: int | None = None
    #: '' todos, 'pendiente' sin factura, 'facturado' con ella.
    billing: str = ''
    #: `None` es «todos»; un id, los procedimientos de ese paciente (el enlace
    #: desde su ficha, que no depende de que el nombre sea único).
    patient: int | None = None
    q: str = ''
    sort: str = DEFAULT_SORT

    @classmethod
    def from_query(cls, params, *, today=None) -> 'ProcedureFilters':
        today = today or timezone.localdate()
        month_start, month_end = month_bounds(today)

        date_from = _date_or_none(params.get('desde')) if 'desde' in params else month_start
        date_to = _date_or_none(params.get('hasta')) if 'hasta' in params else month_end
        if date_from and date_to and date_from > date_to:
            date_from, date_to = date_to, date_from

        billing = (params.get('cobro') or '').strip()
        if billing not in {BILLING_PENDING, BILLING_DONE}:
            billing = ''

        sort = (params.get('sort') or '').strip()
        if sort not in ('asc', 'desc'):
            sort = DEFAULT_SORT

        return cls(
            date_from=date_from,
            date_to=date_to,
            professional=_int_or_none(params.get('profesional')),
            service=_int_or_none(params.get('servicio')),
            billing=billing,
            patient=_int_or_none(params.get('paciente')),
            q=(params.get('q') or '').strip()[:100],
            sort=sort,
        )

    def apply(self, queryset):
        if self.date_from:
            queryset = queryset.filter(performed_at__date__gte=self.date_from)
        if self.date_to:
            queryset = queryset.filter(performed_at__date__lte=self.date_to)
        if self.professional:
            queryset = queryset.filter(created_by_id=self.professional)
        if self.service:
            queryset = queryset.filter(service_id=self.service)
        if self.patient:
            queryset = queryset.filter(visit__episode__history__patient_id=self.patient)
        if self.billing == BILLING_PENDING:
            queryset = queryset.filter(invoice__isnull=True)
        elif self.billing == BILLING_DONE:
            queryset = queryset.filter(invoice__isnull=False)
        # Cada palabra tiene que aparecer en el nombre o apellidos del paciente.
        for word in self.q.split():
            queryset = queryset.filter(
                Q(visit__episode__history__patient__first_name__icontains=word)
                | Q(visit__episode__history__patient__last_name__icontains=word)
            )
        return queryset

    def order(self, queryset):
        if self.sort == 'asc':
            return queryset.order_by('performed_at', 'id')
        return queryset.order_by('-performed_at', '-id')

    def as_params(self) -> dict:
        """Los filtros ya normalizados, listos para `urlencode`.

        `desde`/`hasta` van siempre (vacíos si no hay límite): omitirlos
        significaría «el mes en curso», que no es lo que se está viendo.
        """
        params = {
            'desde': self.date_from.isoformat() if self.date_from else '',
            'hasta': self.date_to.isoformat() if self.date_to else '',
        }
        if self.professional:
            params['profesional'] = self.professional
        if self.service:
            params['servicio'] = self.service
        if self.billing:
            params['cobro'] = self.billing
        if self.patient:
            params['paciente'] = self.patient
        if self.q:
            params['q'] = self.q
        if self.sort != DEFAULT_SORT:
            params['sort'] = self.sort
        return params

    def toggled(self) -> 'ProcedureFilters':
        from dataclasses import replace

        return replace(self, sort='asc' if self.sort == 'desc' else 'desc')


def procedure_totals(queryset) -> dict:
    """Recuento e importes del queryset filtrado, en una sola consulta.

    Se suma `frozen_price`, que es lo que costó cada procedimiento el día que se
    hizo; el catálogo no interviene.
    """
    data = queryset.aggregate(
        count=Count('id'),
        total=Sum('frozen_price'),
        pending_count=Count('id', filter=Q(invoice__isnull=True)),
        pending_total=Sum('frozen_price', filter=Q(invoice__isnull=True)),
    )
    zero = Decimal('0.00')
    return {
        'count': data['count'] or 0,
        'total': data['total'] or zero,
        'pending_count': data['pending_count'] or 0,
        'pending_total': data['pending_total'] or zero,
    }
