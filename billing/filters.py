"""Filtrado y métricas del listado de facturación.

**Un solo sitio decide qué facturas entran.** Los KPIs de la cabecera y la tabla
de abajo salen del MISMO queryset, construido aquí una vez: si el filtro viviera
repartido entre la vista y la plantilla, bastaría una condición de más en un lado
para que el «total facturado» dejara de corresponderse con las filas que se están
mirando. Un panel que se contradice consigo mismo no es un panel, es una trampa.

Tres piezas:

- `InvoiceFilters` — los parámetros del GET, ya normalizados. Es un objeto
  inmutable: se construye desde `request.GET` con `from_query()` y de ahí en
  adelante nadie vuelve a tocar la query string. Sabe además reconstruirla
  (`as_params()`), que es lo que alimenta los enlaces de orden y paginación.
- `invoices_for()` / `pending_procedures_for()` — los dos quesysets base, cada
  uno acotado a la clínica del usuario.
- `invoice_kpis()` — las cuatro métricas, en dos agregaciones.

Sobre el borrado lógico: `PatientInvoice.objects` y `PerformedProcedure.objects`
ya excluyen lo borrado (`SoftDeleteModel`), así que aquí no se repite el
`deleted_at__isnull=True`... salvo donde se cruza una relación, que NO pasa por
el manager del modelo relacionado y hay que filtrar a mano. Está marcado en cada
punto donde ocurre.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db.models import Avg, Count, DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils.dateparse import parse_date

ZERO = Decimal('0.00')

#: Salida decimal de las agregaciones. Explícita porque `Coalesce` mezcla el
#: `Sum` de un DecimalField con un `Value` y, sin ella, Django no sabe con qué
#: tipo quedarse.
_MONEY = DecimalField(max_digits=12, decimal_places=2)

#: Columnas ordenables, y el campo real de cada una. Las claves son lo que viaja
#: en la URL; el campo del modelo se queda dentro, para que un `?sort=` inventado
#: no llegue nunca al `order_by()`.
SORT_FIELDS = {
    'date': 'issued_at',
    'amount': 'total',
}
DEFAULT_SORT = 'date'
DEFAULT_DIRECTION = 'desc'


def _decimal_or_none(raw):
    """Importe de la query string, o `None` si no lo es.

    Acepta la coma decimal: el campo se teclea en un panel en español y `12,50`
    es lo que escribe cualquiera.
    """
    raw = (raw or '').strip().replace(',', '.')
    if not raw:
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return None


def _date_or_none(raw):
    """Fecha ISO de la query string, o `None`. Un `<input type="date">` la manda así."""
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        return parse_date(raw)
    except ValueError:
        return None


@dataclass(frozen=True)
class InvoiceFilters:
    """Los filtros del listado, normalizados y validados.

    Inmutable a propósito: se construye una vez por petición y se pasa a quien lo
    necesite. Lo que llega mal (una fecha imposible, un `sort` inventado, un
    importe que no es un número) se descarta en silencio y cae al valor por
    defecto — es una URL que puede venir pegada a mano, no un formulario que haya
    que validar contra el usuario.
    """

    status: str = ''
    #: Estado de COBRO ('unpaid'/'partial'/'paid'), que no es el estado del
    #: documento: una factura emitida está siempre en los dos ejes a la vez.
    collection: str = ''
    date_from: date | None = None
    date_to: date | None = None
    patient: str = ''
    amount_min: Decimal | None = None
    amount_max: Decimal | None = None
    sort: str = DEFAULT_SORT
    direction: str = DEFAULT_DIRECTION

    # -- construcción -------------------------------------------------------

    @classmethod
    def from_query(cls, params, *, statuses=None) -> 'InvoiceFilters':
        """Lee los filtros de un `request.GET` (o cualquier `QueryDict`)."""
        from billing.models import PatientInvoice

        valid_statuses = statuses or {value for value, _ in PatientInvoice.Status.choices}

        status = (params.get('status') or '').strip()
        if status not in valid_statuses:
            status = ''

        valid_collection = {value for value, _ in PatientInvoice.PaymentState.choices}
        collection = (params.get('cobro') or '').strip()
        if collection not in valid_collection:
            collection = ''

        sort = (params.get('sort') or '').strip()
        if sort not in SORT_FIELDS:
            sort = DEFAULT_SORT

        direction = (params.get('direction') or '').strip()
        if direction not in ('asc', 'desc'):
            direction = DEFAULT_DIRECTION

        date_from = _date_or_none(params.get('desde'))
        date_to = _date_or_none(params.get('hasta'))
        # Un rango del revés no devuelve nada y parece un error de la aplicación.
        # Se endereza, que es lo que el usuario quiso decir.
        if date_from and date_to and date_from > date_to:
            date_from, date_to = date_to, date_from

        amount_min = _decimal_or_none(params.get('min'))
        amount_max = _decimal_or_none(params.get('max'))
        if amount_min is not None and amount_max is not None and amount_min > amount_max:
            amount_min, amount_max = amount_max, amount_min

        return cls(
            status=status,
            collection=collection,
            date_from=date_from,
            date_to=date_to,
            patient=(params.get('q') or '').strip(),
            amount_min=amount_min,
            amount_max=amount_max,
            sort=sort,
            direction=direction,
        )

    # -- aplicación ---------------------------------------------------------

    def apply(self, queryset):
        """Aplica los filtros al queryset de facturas.

        Deliberadamente NO ordena, y la única anotación que puede añadir es
        `with_collection()`, cuando se filtra por estado de cobro. Quien pinta la
        tabla encadena `order()` y su `annotate(Count(...))`, y quien calcula los
        KPIs agrega sobre esto tal cual: colar aquí ese `Count` inflaría después
        el `Sum('total')` de las métricas, porque el JOIN a los procedimientos
        multiplica las filas de cada factura. `with_collection()` sí puede entrar
        porque es una subconsulta escalar y no multiplica nada (ver
        `billing.managers`).
        """
        from billing.models import PatientInvoice

        if self.status:
            queryset = queryset.filter(status=self.status)
        if self.collection:
            # El estado de cobro solo existe para las EMITIDAS: un borrador
            # todavía puede cambiar de importe y una anulada dejó de deber nada,
            # así que las dos saldrían como «impagadas» sin serlo. Acotarlo aquí
            # es además lo que hace que el chip de una fila lleve siempre a una
            # lista donde esa misma fila aparece.
            queryset = queryset.with_collection().filter(
                status=PatientInvoice.Status.ISSUED,
                payment_state=self.collection,
            )
        if self.date_from:
            queryset = queryset.filter(issued_at__date__gte=self.date_from)
        if self.date_to:
            queryset = queryset.filter(issued_at__date__lte=self.date_to)
        if self.amount_min is not None:
            queryset = queryset.filter(total__gte=self.amount_min)
        if self.amount_max is not None:
            queryset = queryset.filter(total__lte=self.amount_max)
        if self.patient:
            queryset = queryset.filter(self._patient_q())
        return queryset

    def _patient_q(self, prefix='') -> Q:
        """Búsqueda por nombre de paciente, palabra a palabra.

        Cada palabra tiene que aparecer en alguno de los campos, así que «pérez
        ana» encuentra a Ana Pérez igual que «ana pérez». Se busca también en
        `frozen_patient_name` —que solo existe en la factura, no en el
        procedimiento, de ahí el `prefix`— para que una factura cuyo paciente se
        haya dado de baja siga siendo localizable por el nombre que lleva escrito.
        """
        patient_path = f'{prefix}patient' if prefix else 'patient'
        query = Q()
        for word in self.patient.split():
            match = (
                Q(**{f'{patient_path}__first_name__icontains': word})
                | Q(**{f'{patient_path}__last_name__icontains': word})
            )
            if not prefix:
                match |= Q(frozen_patient_name__icontains=word)
            query &= match
        return query

    def apply_to_procedures(self, queryset):
        """Los mismos filtros que tienen sentido sobre procedimientos sueltos.

        Un procedimiento sin facturar no tiene estado de factura ni importe de
        factura, así que de los cuatro filtros solo se trasladan dos: el paciente
        y el rango de fechas, que aquí se mide sobre `performed_at` (cuándo se
        hizo) porque no hay `issued_at` que medir. El rango de importe se deja
        fuera a propósito: `total` es el de una factura entera y compararlo con el
        precio de una línea suelta no querría decir nada.
        """
        if self.date_from:
            queryset = queryset.filter(performed_at__date__gte=self.date_from)
        if self.date_to:
            queryset = queryset.filter(performed_at__date__lte=self.date_to)
        if self.patient:
            queryset = queryset.filter(self._patient_q(prefix='visit__episode__history__'))
        return queryset

    def order(self, queryset):
        """Ordena por la columna pedida, con los nulos siempre al final.

        Un borrador no tiene `issued_at`, y en PostgreSQL los nulos se van arriba
        en orden descendente: sin `nulls_last` la lista empezaría por las facturas
        sin fecha. El desempate por `-id` mantiene el orden estable entre facturas
        emitidas el mismo día.
        """
        field = F(SORT_FIELDS[self.sort])
        expression = (
            field.desc(nulls_last=True) if self.direction == 'desc'
            else field.asc(nulls_last=True)
        )
        return queryset.order_by(expression, '-id')

    # -- reconstrucción de la URL ------------------------------------------

    @property
    def has_filters(self) -> bool:
        """¿Hay algún filtro puesto? (el orden no cuenta: siempre hay uno)."""
        return any([
            self.status, self.collection, self.date_from, self.date_to,
            self.patient,
            self.amount_min is not None, self.amount_max is not None,
        ])

    def as_params(self) -> dict:
        """Los filtros como pares para la query string, sin los vacíos.

        Es la única fuente de las URLs de orden y paginación: se reconstruyen
        desde los filtros ya normalizados y no desde `request.GET`, para que un
        enlace de la página 3 no arrastre la basura que trajera la dirección
        original.
        """
        params = {}
        if self.status:
            params['status'] = self.status
        if self.collection:
            params['cobro'] = self.collection
        if self.date_from:
            params['desde'] = self.date_from.isoformat()
        if self.date_to:
            params['hasta'] = self.date_to.isoformat()
        if self.patient:
            params['q'] = self.patient
        if self.amount_min is not None:
            params['min'] = f'{self.amount_min:f}'
        if self.amount_max is not None:
            params['max'] = f'{self.amount_max:f}'
        params['sort'] = self.sort
        params['direction'] = self.direction
        return params

    def toggled(self, column: str) -> 'InvoiceFilters':
        """Los mismos filtros, ordenados por `column`.

        Si ya se ordenaba por ella, invierte el sentido; si no, entra por el
        sentido por defecto (lo más reciente y lo más caro primero, que es lo que
        se quiere ver al pinchar una columna por primera vez).
        """
        if column == self.sort:
            direction = 'asc' if self.direction == 'desc' else 'desc'
        else:
            direction = DEFAULT_DIRECTION
        return replace(self, sort=column, direction=direction)


# ---------------------------------------------------------------------------
# Quesysets base, acotados a la clínica
# ---------------------------------------------------------------------------

def _scope_to_clinic(queryset, user, clinic_path='clinic'):
    """Aislamiento multi-tenant, igual que en el resto del panel.

    Un usuario con clínica ve la suya —también si es superusuario—; solo el
    superusuario SIN clínica (equipo de plataforma) lo ve todo. Cualquier otro
    caso no ve nada: es preferible una lista vacía a una fuga entre clínicas.
    """
    if user.clinic_id:
        return queryset.filter(**{clinic_path: user.clinic})
    if user.is_superuser:
        return queryset
    return queryset.none()


def invoices_for(user):
    """Facturas que `user` puede ver. Sin filtrar todavía: eso es `InvoiceFilters`."""
    from billing.models import PatientInvoice

    return _scope_to_clinic(PatientInvoice.objects.all(), user)


def pending_procedures_for(user):
    """Procedimientos hechos que aún no cuelgan de ninguna factura.

    Es OTRO queryset, no una vista de las facturas, y por eso no se le aplica el
    filtro de estado: lo pendiente de facturar no tiene estado de factura — es
    justo lo que todavía no es ninguna.

    El borrado lógico se filtra en dos niveles distintos: el del propio
    procedimiento lo pone su manager, pero el de la visita, el episodio y la
    historia hay que ponerlo a mano, porque cruzar una relación NO pasa por el
    manager del modelo del otro lado. Sin esto, dar de baja un episodio entero
    dejaría sus procedimientos contando como pendientes de cobro para siempre.
    """
    from clinical.models import PerformedProcedure

    queryset = PerformedProcedure.objects.filter(
        invoice__isnull=True,
        visit__deleted_at__isnull=True,
        visit__episode__deleted_at__isnull=True,
        visit__episode__history__deleted_at__isnull=True,
    )
    return _scope_to_clinic(queryset, user, 'visit__episode__history__patient__clinic')


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------

def invoice_kpis(invoices, pending_procedures) -> dict:
    """Las seis métricas de la cabecera, en dos consultas.

    `invoices` es el queryset YA filtrado que alimenta también la tabla: los KPIs
    y las filas se mueven juntos por construcción, no por disciplina.

    Todo se calcula en la base de datos. Traerse las facturas para sumarlas en
    Python daría el mismo número hoy y se caería el día que haya diez mil, y
    además sumaría solo la página visible.

    Los tres primeros KPIs salen de UN agregado con `filter=` por expresión, que
    en SQL es un `FILTER (WHERE ...)` por columna: una sola pasada por la tabla en
    vez de tres. Lo facturado y el ticket medio miran solo las **emitidas** — un
    borrador no se ha cobrado y una anulada dejó de valer—, mientras que el
    recuento cuenta todas las que se están viendo.

    Los dos de cobro entran en ese MISMO agregado, y solo pueden hacerlo porque
    `amount_collected` es una subconsulta escalar: con el `Sum` con JOIN que
    tenía antes, sumar los pagos al lado del `Sum('total')` habría inflado los
    dos —que es justo la trampa contra la que avisa el resto de este módulo—.
    Django envuelve el conjunto en una subconsulta y agrega por fuera: una sola
    sentencia.

    El último es una consulta aparte, y no puede ser de otra forma: se calcula
    sobre procedimientos, no sobre facturas.
    """
    from billing.models import PatientInvoice

    issued = Q(status=PatientInvoice.Status.ISSUED)
    totals = invoices.with_collection().aggregate(
        invoice_count=Count('id'),
        issued_count=Count('id', filter=issued),
        total_billed=Coalesce(
            Sum('total', filter=issued), Value(ZERO), output_field=_MONEY,
        ),
        average_ticket=Coalesce(
            Avg('total', filter=issued), Value(ZERO), output_field=_MONEY,
        ),
        # Lo que falta por entrar por la puerta, factura a factura. Mira solo
        # las emitidas por lo mismo que el total facturado: un borrador todavía
        # no debe nada y una anulada dejó de deberlo. Como el sobrepago está
        # vetado en el modelo, la resta nunca sale negativa.
        pending_collection_amount=Coalesce(
            Sum(F('total') - F('amount_collected'), filter=issued),
            Value(ZERO), output_field=_MONEY,
        ),
        # Emitidas con saldo pendiente: impagadas Y parciales. Lo que interesa
        # de esta cifra es a cuántos pacientes hay que reclamar, y una factura
        # cobrada a medias cuenta igual que una que no se ha tocado.
        unpaid_invoice_count=Count(
            'id',
            filter=issued & ~Q(payment_state=PatientInvoice.PaymentState.PAID),
        ),
    )
    pending = pending_procedures.aggregate(
        pending_count=Count('id'),
        pending_amount=Coalesce(
            Sum('frozen_price'), Value(ZERO), output_field=_MONEY,
        ),
    )
    return {**totals, **pending}
