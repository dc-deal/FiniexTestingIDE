"""
FiniexTestingIDE - Carried Readings (#626)

Whether a session continues the drawdown curve and the risk baseline its carry-over holds, and
whether it may write its own over them.

Both are readings of one account, and a rehearsal's account is simulated: its peak, its deepest
decline and the denominator its limits measured against describe money that never existed. A
session trading real money that continued them would report the rehearsal's declines as its own
and measure its breaker against a simulated peak — blocking entries from the first tick, or
flattening, when that peak stood above the real account, and loosening every limit when it stood
below. So the readings follow the run kind: continued only by a session whose orders go where
theirs went, and never written over by a rehearsal where real money wrote them — real money, a
rehearsal and real money again continues the real account's readings.
"""

from python.framework.types.persistence_types import ColdStartPayload, ReadingsVerdict
from python.framework.types.run_kind_types import OrdersTo

# Who wrote the carried readings, and what this session is — in the words a log reader uses
_WRITER = {
    OrdersTo.SIMULATED: 'a rehearsal (orders simulated)',
    OrdersTo.VENUE: 'a session that placed real orders',
}
_SESSION = {
    OrdersTo.SIMULATED: 'is a rehearsal (orders simulated)',
    OrdersTo.VENUE: 'places real orders',
}


def carried_readings_verdict(
    carried: ColdStartPayload,
    orders_to: OrdersTo,
    new_deployment: bool,
    persist_baseline: bool,
) -> ReadingsVerdict:
    """
    Decide whether this session continues the readings its carry-over holds.

    Args:
        carried: What the predecessor left
        orders_to: Where this session's orders go
        new_deployment: Whether this start began a new deployment (`--new-deployment`)
        persist_baseline: The profile's `safety.persist_baseline` — whether risk history may
            survive a restart at all

    Returns:
        The verdict; only CONTINUED restores the readings
    """
    if not persist_baseline:
        return ReadingsVerdict.SWITCHED_OFF
    if carried.risk_baseline is None and carried.account_drawdown is None:
        return ReadingsVerdict.NONE_CARRIED
    if new_deployment:
        return ReadingsVerdict.NEW_DEPLOYMENT
    if carried.readings_orders_to is None:
        return ReadingsVerdict.NOT_RECORDED
    if carried.readings_orders_to is not orders_to:
        return ReadingsVerdict.RUN_KIND_CHANGED
    return ReadingsVerdict.CONTINUED


def may_write_readings(carried: ColdStartPayload, orders_to: OrdersTo, new_deployment: bool) -> bool:
    """
    Whether this session may write its readings over the ones its carry-over holds.

    Only a rehearsal over readings real money wrote may not: they wait for the next session that
    places real orders, which continues them. A new deployment ends them either way.

    Args:
        carried: What the predecessor left
        orders_to: Where this session's orders go
        new_deployment: Whether this start began a new deployment

    Returns:
        True when the session's own readings are written
    """
    return not (orders_to is OrdersTo.SIMULATED
                and carried.readings_orders_to is OrdersTo.VENUE
                and not new_deployment)


def describe_readings_verdict(
    verdict: ReadingsVerdict,
    carried: ColdStartPayload,
    orders_to: OrdersTo,
) -> str:
    """
    Say why a session starts its own readings, for the log of its start and its record.

    Args:
        verdict: What the session does with the carried readings
        carried: What the predecessor left
        orders_to: Where this session's orders go

    Returns:
        The sentence; '' for a session that continues the readings, has none to continue, or
        runs with `persist_baseline` off — the first says so where it restores them
    """
    if verdict is ReadingsVerdict.NEW_DEPLOYMENT:
        return ('The drawdown curve and the risk baseline start fresh with the new deployment '
                '(--new-deployment).')
    if verdict is ReadingsVerdict.NOT_RECORDED:
        return ('The drawdown curve and the risk baseline start fresh: the carry-over does not say '
                'whether a rehearsal or real money wrote them — it was written before that was '
                'recorded.')
    if verdict is not ReadingsVerdict.RUN_KIND_CHANGED or carried.readings_orders_to is None:
        return ''
    consequence = ('Simulated and real results are never linked.'
                   if orders_to is OrdersTo.VENUE
                   else 'They stay as they are for the next session that places real orders.')
    return (f'The drawdown curve and the risk baseline start fresh: the carried ones were written '
            f'by {_WRITER[carried.readings_orders_to]}, and this session '
            f'{_SESSION[orders_to]}. {consequence}')
