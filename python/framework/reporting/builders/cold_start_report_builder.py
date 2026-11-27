"""
Cold-start report builder (#355 / #493) — the boot-situation postprocessor.

Maps what the boot step found (`ColdStartSituation`) plus what the decision logic answered
(`ColdStartVerdict`) onto the `ColdStartReport` model. Live-only: a simulation has no venue
holding anything, and a dry run never queried one.

Nothing is computed here that the run itself did not already know — the situation is captured
at boot and this is the projection. The one thing worth stating is why the record exists at
all: no case may disappear through a yes. An algo that accounts for its inherited orders
changes the VERDICT, never the record, so "the algo said it was fine" cannot look the same as
"nothing was found" when somebody reads thirty restarts back.
"""

from typing import Any, Dict, Optional

from python.framework.types.api.report_types import (
    ColdStartBootFillRow,
    ColdStartOrderRow,
    ColdStartPositionRow,
    ColdStartReport,
    ColdStartSkippedRow,
)
from python.framework.types.autotrader_types.cold_start_types import (
    ColdStartSituation,
    ColdStartVerdict,
)


def build_cold_start_report_from_session(
    run_id: str,
    situation: ColdStartSituation,
    verdict: Optional[ColdStartVerdict] = None,
    decision_logic_class: str = '',
) -> ColdStartReport:
    """
    Build the cold-start report for one AutoTrader session.

    Args:
        run_id: The run this report belongs to
        situation: What the boot step found at the venue
        verdict: What the decision logic answered, when it was asked
        decision_logic_class: The decision logic's class name, empty when none was asked

    Returns:
        The report, ready to persist and to render
    """
    return ColdStartReport(
        run_id=run_id,
        symbol=situation.symbol,
        applied=situation.applied,
        skipped_reasons=sorted({order.reason.value for order in situation.skipped}),
        adopted=[
            ColdStartOrderRow(
                order_id=order.order_id,
                client_order_id=order.client_order_id or '',
                broker_ref=order.broker_ref,
                direction=order.direction.value,
                order_type=order.order_type.value,
                lots=order.lots,
                filled_lots=order.filled_lots,
                price=order.price,
            )
            for order in situation.adopted
        ],
        skipped=[
            ColdStartSkippedRow(
                reason=order.reason.value,
                client_order_id=order.client_order_id or '',
                broker_ref=order.broker_ref,
                symbol=order.symbol,
                order_type=order.order_type.value,
                lots=order.lots,
                price=order.price,
                key_is_ours=order.key_is_ours,
            )
            for order in situation.skipped
        ],
        restored_positions=[
            ColdStartPositionRow(
                position_id=record.position_id,
                direction=record.direction,
                lots=record.lots,
                entry_price=record.entry_price,
                entry_time=record.entry_time,
                status=record.status,
            )
            for record in situation.restored_positions
        ],
        book_shortfall=situation.book_shortfall,
        adoption_mode=situation.adoption_mode,
        attended=situation.attended,
        carry_over_present=situation.carry_over_present,
        carry_over_saved_at=situation.carry_over_saved_at or '',
        decision_logic_class=decision_logic_class,
        decision_logic_accounted_for=verdict.accounted_for if verdict is not None else None,
        decision_logic_note=verdict.note if verdict is not None else '',
        **_boot_read_fields(situation),
    )


def _boot_read_fields(situation: ColdStartSituation) -> Dict[str, Any]:
    """
    The boot read's part of the report (#652): its range, whether it completed, every fill it filed.

    Args:
        situation: What the boot step found at the venue

    Returns:
        The report fields; the unread defaults where the start read none
    """
    boot_read = situation.boot_read
    if boot_read is None:
        return {}
    return {
        'boot_read_done': True,
        'boot_read_written_at': boot_read.written_at.isoformat(),
        'boot_read_from': boot_read.read_from.isoformat(),
        'boot_read_to': boot_read.read_to.isoformat(),
        'boot_read_complete': boot_read.complete,
        'boot_read_incomplete_reason': boot_read.incomplete_reason,
        'boot_fills': [
            ColdStartBootFillRow(
                client_order_id=fill.client_order_id,
                broker_ref=fill.broker_ref,
                symbol=fill.symbol,
                direction=fill.direction.value,
                lots=fill.lots,
                filled_lots=fill.filled_lots,
                closed_at=fill.closed_at.isoformat() if fill.closed_at is not None else '',
                fill_class=fill.fill_class.value,
                reason=fill.reason,
            )
            for fill in boot_read.fills
        ],
    }
