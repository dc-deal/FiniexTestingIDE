"""
Venue-account report builder (#362) — the broker-truth postprocessor.

Maps a live session's broker-truth lines onto the `VenueAccountReport` model: what the venue held
when the session started and when it ended, and what the reconciliation recorded in between — with
the reconciliation's totals over the whole session (#631), which no line carries.
AutoTrader only — a backtest's venue is its own book, so a simulation has no lines to map.

Counted here and nowhere else: the open orders and positions of a read, the reconcile lines and
the divergent ones among them. The console, the API and a CSV then read the same number instead of
each counting the read's lists (#391). The read itself, order by order, stays on the order-event
stream, which is what each snapshot's `seq` points at.
"""

from collections.abc import Sized
from dataclasses import asdict
from typing import List, Optional

from python.framework.reporting.builders.run_unit import RunUnit
from python.framework.types.api.report_types import (
    ReconcileDivergenceRow,
    VenueAccountReport,
    VenueAccountRow,
    VenueReconciliation,
    VenueSnapshotRow,
)
from python.framework.types.live_types.broker_truth_types import (
    BrokerTruthReadReason,
    BrokerTruthRecord,
)
from python.framework.types.live_types.reconciliation_types import ReconcileState


def build_venue_account_report(run_id: str, units: List[RunUnit]) -> VenueAccountReport:
    """
    Build the venue-account report for a run.

    Args:
        run_id: The run this report belongs to
        units: The run's units; one that recorded no broker-truth line gets no row

    Returns:
        The report; its `units` is empty when no unit asked its venue anything
    """
    return VenueAccountReport(
        run_id=run_id,
        units=[_venue_account_row(unit) for unit in units if asked_its_venue(unit)])


def asked_its_venue(unit: RunUnit) -> bool:
    """
    Whether a unit asked its venue anything — a read it recorded, or a reconcile cycle, even one
    that could not reach the venue: a session whose every cycle was skipped is exactly the one
    whose record has to say so.

    Args:
        unit: The unit

    Returns:
        True when the unit has a venue-account row
    """
    totals = unit.reconcile_totals
    return bool(unit.broker_truth) or (
        totals is not None and totals.cycles_compared + totals.cycles_skipped > 0)


def _venue_account_row(unit: RunUnit) -> VenueAccountRow:
    """
    One session's account as its venue reported it.

    Args:
        unit: The session's unit — its broker-truth lines in the order they were written, and
            what its reconciliation did

    Returns:
        The row — the first start read, the last end read, the reconcile lines between, and the
        reconciliation's totals
    """
    records = unit.broker_truth
    starts = [record for record in records
              if record.read_reason is BrokerTruthReadReason.SESSION_START]
    ends = [record for record in records
            if record.read_reason is BrokerTruthReadReason.SESSION_END]
    reconciles = [record for record in records
                  if record.read_reason is BrokerTruthReadReason.RECONCILE]
    divergent = [record for record in reconciles
                 if record.reconcile_state is ReconcileState.DIVERGENT]
    last_divergence = divergent[-1].divergence if divergent else None
    return VenueAccountRow(
        name=unit.name,
        at_start=_snapshot_row(starts[0]) if starts else None,
        at_end=_snapshot_row(ends[-1]) if ends else None,
        reconcile_lines=len(reconciles),
        divergent_lines=len(divergent),
        last_reconcile_state=reconciles[-1].reconcile_state if reconciles else None,
        last_divergence=(ReconcileDivergenceRow(**asdict(last_divergence))
                         if last_divergence is not None else None),
        reconciliation=_reconciliation_of(unit),
    )


def _reconciliation_of(unit: RunUnit) -> Optional[VenueReconciliation]:
    """
    What a session's reconciliation did, as its row serves it.

    Args:
        unit: The session's unit

    Returns:
        The totals — counts null when the comparison was switched off; None when the session's
        record does not say whether it reconciled
    """
    if unit.reconcile_enabled is None:
        return None
    totals = unit.reconcile_totals
    if totals is None:
        return VenueReconciliation(enabled=unit.reconcile_enabled)
    return VenueReconciliation(
        enabled=unit.reconcile_enabled, cycles_compared=totals.cycles_compared,
        cycles_skipped=totals.cycles_skipped, divergences_seen=totals.divergences_seen)


def _snapshot_row(record: BrokerTruthRecord) -> VenueSnapshotRow:
    """
    One read, counted.

    Args:
        record: A start or end line

    Returns:
        The snapshot — a part the read gave up on, or did not take, stays null
    """
    snapshot = record.snapshot
    return VenueSnapshotRow(
        seq=record.seq,
        venue_order_count=_count(snapshot.venue_orders),
        venue_balances=(dict(snapshot.venue_balances)
                        if snapshot.venue_balances is not None else None),
        venue_position_count=_count(snapshot.venue_positions),
        unread_parts=list(snapshot.unread_parts),
    )


def _count(items: Optional[Sized]) -> Optional[int]:
    """
    A part's length, or None for a part that was not read.

    Args:
        items: The part as the read returned it

    Returns:
        Its length; None when the part is None
    """
    return len(items) if items is not None else None
