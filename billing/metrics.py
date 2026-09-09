"""Métricas económicas del panel de control.

El panel enseña **dinero que ha entrado**, no una estimación. Antes sumaba el
precio de catálogo de las citas completadas del mes, lo que tenía dos defectos
de fondo: leía el precio *vivo* —así que subir la tarifa reescribía hacia atrás
los ingresos de enero, justo contra el principio de precios congelados que rige
la capa clínica— y no distinguía lo facturado de lo cobrado. Aquí se mira lo
único que no admite interpretación: los `Payment`, por su `paid_at`.

Vive en `billing` y no en `core` a propósito. Quién ve qué facturas, qué cuenta
como cobrado y qué significa «pendiente» ya está decidido en `billing.filters` y
`billing.managers`; el panel es un consumidor más, como el listado. Si esto
viviera en la vista del panel habría dos definiciones de «pendiente de cobro» y
tarde o temprano dirían cosas distintas en dos pantallas.

Todo se agrega en la base de datos, y son **tres consultas** en total:

1. la serie diaria de cobros del mes (de la que sale también el total, sumando
   como mucho 31 filas ya traídas, en vez de repetir el agregado),
2. el mismo tramo del mes anterior, para la comparación,
3. facturado, pendiente de cobro y nº de impagadas, las tres en un solo
   `aggregate` con `filter=` por expresión.

Más `pending_procedures_for()`, que es la cuarta y ya existía.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncDate

from billing.filters import (
    ZERO,
    invoices_for,
    pending_procedures_for,
    scope_to_clinic,
)

#: Tipo de salida del dinero agregado. Explícito porque `Coalesce` mezcla un
#: `Sum` con un `Value` y sin esto Django no sabe con qué tipo quedarse. Mismo
#: criterio —y mismo tamaño— que el `_MONEY` de `billing.filters` y el de
#: `billing.managers`.
_MONEY = DecimalField(max_digits=12, decimal_places=2)

#: Altura mínima de una barra con importe, en porcentaje. Un día que cobró poco
#: cobró algo, y una barra de medio píxel se lee igual que un día vacío.
MIN_BAR_PCT = 3


def payments_for(user):
    """Cobros que `user` puede ver. Hermana de `filters.invoices_for()`.

    `Payment.clinic` está denormalizada —la copia se valida al registrar el
    cobro, en `Payment._prepare_receipt()`—, así que no hace falta subir por la
    factura para acotar por clínica. `Payment.objects` ya excluye lo borrado.
    """
    from billing.models import Payment

    return scope_to_clinic(Payment.objects.all(), user)


def _month_bounds(day: date) -> tuple[date, date]:
    """Primer y último día del mes de `day`."""
    last = calendar.monthrange(day.year, day.month)[1]
    return day.replace(day=1), day.replace(day=last)


def _previous_month_span(today: date) -> tuple[date, date]:
    """El MISMO tramo del mes anterior: del día 1 al mismo día del mes.

    Comparar el mes a medias contra el mes anterior entero diría siempre que se
    va peor, que es una forma tonta de mentir. Se compara tramo con tramo.

    El día se recorta a la longitud del mes anterior, que puede ser más corto:
    el 31 de marzo se compara contra el 28 de febrero, no contra un 31 que no
    existe.
    """
    last_day_prev = today.replace(day=1) - timedelta(days=1)
    start = last_day_prev.replace(day=1)
    end = last_day_prev.replace(day=min(today.day, last_day_prev.day))
    return start, end


def _delta_pct(current: Decimal, previous: Decimal) -> int | None:
    """Variación porcentual entre dos tramos, o `None` si no hay con qué comparar.

    Sin cobros el mes pasado no hay porcentaje que valga: pasar de 0 a 300 € no
    es «un 100 % más», y un «∞ %» en un panel es ruido. La tarjeta lo dice con
    palabras en ese caso.
    """
    if previous <= 0:
        return None
    return int(round((current - previous) / previous * 100))


def _daily_series(user, month_start: date, month_end: date, today: date) -> list[dict]:
    """Un punto por CADA día del mes, con lo cobrado ese día.

    La consulta solo devuelve los días en que entró dinero; el resto se rellenan
    a cero aquí. Una gráfica que se salta los días vacíos miente sobre el ritmo
    del mes: comprime los huecos y hace parecer constante lo que fue a rachas.

    `TruncDate` agrupa por día **en la zona horaria local**, que es la que ve
    quien mira el panel — no la UTC en que se guarda `paid_at`.

    `pct` se normaliza contra el máximo diario, no contra el total: lo que se
    quiere ver es la forma del mes, y contra el total todas las barras serían
    igual de planas.
    """
    rows = (
        payments_for(user)
        .filter(paid_at__date__gte=month_start, paid_at__date__lte=month_end)
        .annotate(day=TruncDate('paid_at'))
        .values('day')
        .annotate(amount=Sum('amount'))
        .order_by('day')
    )
    by_day = {row['day']: row['amount'] or ZERO for row in rows}
    peak = max(by_day.values(), default=ZERO)

    series = []
    for number in range(1, month_end.day + 1):
        current = month_start.replace(day=number)
        amount = by_day.get(current, ZERO)
        if amount > 0 and peak > 0:
            pct = max(MIN_BAR_PCT, int(round(amount / peak * 100)))
        else:
            pct = 0
        series.append({
            'date': current,
            'amount': amount,
            'pct': pct,
            'is_today': current == today,
            'is_future': current > today,
        })
    return series


def dashboard_revenue(user, today: date) -> dict:
    """Todo el bloque económico del panel, en cuatro consultas.

    Devuelve tanto lo cobrado (caja) como lo facturado (devengo) porque no son
    lo mismo y el panel enseña los dos: `collected` es dinero que entró en el
    mes, `billed` es lo que se emitió en el mes. Una factura emitida en enero y
    cobrada en febrero cuenta en cada uno de su lado.

    `pending_amount` y `unpaid_count`, en cambio, NO se acotan al mes: lo que se
    reclama se reclama entero, venga de cuando venga. Sería engañoso enseñar
    solo la deuda nacida este mes.
    """
    from billing.models import PatientInvoice

    month_start, month_end = _month_bounds(today)
    series = _daily_series(user, month_start, month_end, today)
    collected = sum((point['amount'] for point in series), ZERO)

    paid_points = [point for point in series if point['amount'] > 0]
    best_day = max(paid_points, key=lambda point: point['amount'], default=None)

    previous_start, previous_end = _previous_month_span(today)
    previous_collected = payments_for(user).filter(
        paid_at__date__gte=previous_start, paid_at__date__lte=previous_end,
    ).aggregate(
        total=Coalesce(Sum('amount'), Value(ZERO), output_field=_MONEY),
    )['total']

    # Las tres cifras de facturas, en un solo agregado. Solo pueden ir juntas
    # porque `amount_collected` es una subconsulta escalar (ver
    # `billing.managers.with_collection`): con un `Sum` con JOIN a los pagos,
    # sumarlos al lado del `Sum('total')` inflaría los dos.
    issued = Q(status=PatientInvoice.Status.ISSUED)
    this_month = Q(issued_at__date__gte=month_start, issued_at__date__lte=month_end)
    invoices = invoices_for(user).with_collection().aggregate(
        billed=Coalesce(
            Sum('total', filter=issued & this_month), Value(ZERO), output_field=_MONEY,
        ),
        # Saldo vivo, sin acotar al mes. El sobrepago está vetado en el modelo,
        # así que la resta nunca sale negativa.
        pending_amount=Coalesce(
            Sum(F('total') - F('amount_collected'), filter=issued),
            Value(ZERO), output_field=_MONEY,
        ),
        # Emitidas con algo por cobrar: impagadas Y parciales. Lo que importa es
        # a cuánta gente hay que reclamar, y una cobrada a medias cuenta igual.
        unpaid_count=Count(
            'id', filter=issued & ~Q(payment_state=PatientInvoice.PaymentState.PAID),
        ),
    )

    # Trabajo hecho que todavía no es una factura. Es OTRA cosa que lo pendiente
    # de cobro: aquí falta emitir el documento, allí falta que entre el dinero.
    unbilled = pending_procedures_for(user).aggregate(
        unbilled_count=Count('id'),
        unbilled_amount=Coalesce(
            Sum('frozen_price'), Value(ZERO), output_field=_MONEY,
        ),
    )

    delta_pct = _delta_pct(collected, previous_collected)
    return {
        'collected': collected,
        'series': series,
        'best_day': best_day,
        'previous_collected': previous_collected,
        'previous_month_start': previous_start,
        'delta_pct': delta_pct,
        # El signo lo dice la flecha, así que la plantilla pinta el valor sin
        # él: un «▼ -12 %» dice la misma cosa dos veces. Django no tiene un
        # filtro `abs`, y esto es más barato que inventarse uno.
        'delta_abs': None if delta_pct is None else abs(delta_pct),
        'month_start': month_start,
        'month_end': month_end,
        **invoices,
        **unbilled,
    }
