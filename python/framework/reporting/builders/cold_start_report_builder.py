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

from typing import Optional

from python.framework.types.api.report_types import (
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
    algo_name: str = '',
) -> ColdStartReport:
    """
    Build the cold-start report for one live session.

    Args:
        run_id: The run this report belongs to
        situation: What the boot step found at the venue
        verdict: What the decision logic answered, when it was asked
        algo_name: The decision logic's class name, empty when none was asked

    Returns:
        The report, ready to persist and to render
    """
    return ColdStartReport(
        run_id=run_id,
        symbol=situation.symbol,
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
        algo_name=algo_name,
        algo_accounted_for=verdict.accounted_for if verdict is not None else None,
        algo_note=verdict.note if verdict is not None else '',
    )
