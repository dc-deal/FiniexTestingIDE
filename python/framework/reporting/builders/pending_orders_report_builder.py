"""
Pending-orders report builder (#391) — the order-pipeline postprocessor.

Derives each run unit's in-flight counters from its order-event stream (#362): per submission, the
first event that ends its in-flight phase, as `IN_FLIGHT_ENDING_BY_EVENT` declares it — accepted,
rejected, never confirmed, or expired on the way. The counters are a fold over the stream, so they
cannot disagree with the record they summarise, and both pipelines are counted by one rule. The
active orders at the unit's end come from its snapshot. Pure + fixture-testable.
"""

from typing import Dict, List, Optional, Set

from python.framework.reporting.builders.run_unit import RunUnit
from python.framework.types.api.report_types import (
    ActiveOrderRow,
    NeverConfirmedOrderRow,
    PendingOrdersReport,
    PendingOrdersUnitRow,
)
from python.framework.types.trading_env_types.active_orders_snapshot_types import (
    ActiveOrderSnapshot,
    ActiveOrdersSnapshot,
)
from python.framework.types.trading_env_types.order_event_types import (
    IN_FLIGHT_ENDING_BY_EVENT,
    InFlightEnding,
    OrderEvent,
    OrderEventType,
)


def build_pending_orders_report(run_id: str, units: List[RunUnit]) -> PendingOrdersReport:
    """
    Build the report from the run's units — one row per unit with pending activity.

    Args:
        run_id: The run this report belongs to
        units: The run's units (sim: scenarios; live: the session)

    Returns:
        PendingOrdersReport with one row per unit that submitted an order or holds active ones
    """
    rows: List[PendingOrdersUnitRow] = []
    for unit in units:
        row = pending_orders_row(unit.name, unit.symbol, unit.order_events, unit.active_orders)
        if row is not None:
            rows.append(row)
    return PendingOrdersReport(run_id=run_id, units=rows)


def submission_count(events: List[OrderEvent]) -> int:
    """
    How many orders the unit handed to its venue — its `submitted` events.

    Args:
        events: One unit's events

    Returns:
        The count
    """
    return sum(1 for event in events if event.event_type is OrderEventType.SUBMITTED)


def first_in_flight_endings(events: List[OrderEvent]) -> Dict[int, OrderEvent]:
    """
    Each submission's first event that ends its in-flight phase.

    Only a submission's FIRST such event counts: a resting order the venue took and that later
    expires was accepted, not expired. An adopted order has no submission here and is left out.

    Args:
        events: One unit's events, in the order they were recorded

    Returns:
        The ending event, keyed by the `seq` of the submission it ends
    """
    submitted: Set[int] = set()
    endings: Dict[int, OrderEvent] = {}
    for event in events:
        if event.event_type is OrderEventType.SUBMITTED:
            submitted.add(event.seq)
            continue
        if IN_FLIGHT_ENDING_BY_EVENT[event.event_type] is None:
            continue
        if event.submitted_seq in submitted:
            endings.setdefault(event.submitted_seq, event)
    return endings


def pending_orders_row(
    name: str,
    symbol: str,
    events: List[OrderEvent],
    active_orders: Optional[ActiveOrdersSnapshot],
) -> Optional[PendingOrdersUnitRow]:
    """
    One unit's in-flight counters, answer durations and the orders it still held.

    Args:
        name: The unit
        symbol: Its instrument
        events: Its order events, in the order they were recorded
        active_orders: What was still resting when it ended, or None

    Returns:
        The row, or None for a unit that submitted nothing and holds nothing
    """
    submissions = submission_count(events)
    limits = active_orders.active_limit_orders if active_orders else []
    stops = active_orders.active_stop_orders if active_orders else []
    if not submissions and not limits and not stops:
        return None

    endings = first_in_flight_endings(events)
    counts = {ending: 0 for ending in InFlightEnding}
    for event in endings.values():
        counts[IN_FLIGHT_ENDING_BY_EVENT[event.event_type]] += 1
    # Only an ANSWER carries a duration — a late acceptance learned by asking has none (#362)
    durations = [e.in_flight_ms for e in endings.values() if e.in_flight_ms is not None]

    return PendingOrdersUnitRow(
        name=name,
        symbol=symbol,
        total_submitted=submissions,
        **{f'total_{ending.value}': count for ending, count in counts.items()},
        avg_in_flight_ms=sum(durations) / len(durations) if durations else None,
        min_in_flight_ms=min(durations, default=None),
        max_in_flight_ms=max(durations, default=None),
        in_flight_count=len(durations),
        never_confirmed_orders=[
            NeverConfirmedOrderRow(
                order_id=event.order_id,
                submitted_seq=submitted_seq,
                event_type=event.event_type,
                end_reason=event.end_reason,
                message=event.message,
            )
            for submitted_seq, event in endings.items()
            if IN_FLIGHT_ENDING_BY_EVENT[event.event_type] is InFlightEnding.NEVER_CONFIRMED
        ],
        active_limit_orders=_active_rows(limits),
        active_stop_orders=_active_rows(stops),
    )


def _active_rows(snapshots: List[ActiveOrderSnapshot]) -> List[ActiveOrderRow]:
    """Map active-order snapshots to renderable rows."""
    return [
        ActiveOrderRow(
            order_id=s.order_id,
            order_type=s.order_type,
            direction=s.direction,
            lots=s.lots,
            entry_price=s.entry_price,
            limit_price=s.limit_price,
            stop_loss=s.stop_loss,
            take_profit=s.take_profit,
        )
        for s in snapshots
    ]
