"""
Booking-periods report builder (#537) — the Hauptbuch as a table, and its reconciliation.

A run's booking periods are already derived; this maps them to renderable rows and answers the
one question the table exists for: **do the periods add up to the run?**

That check is not decoration. A period summary is trusted because it can be recomputed from its
records, and a COLUMN of period summaries is trusted because it agrees with the figure the run
reports by its own, independent path — the portfolio aggregate. Two derivations of one number
meeting is evidence; one derivation printed twice is not. So the sum is computed here and stated,
rather than left to a reader adding up a column by eye.

A mismatch is REPORTED, never raised (§12: reports calculate and render, they do not judge). It
is the finding the table exists to surface.
"""

from typing import List

from python.framework.reporting.builders.run_unit import RunUnit
from python.framework.types.api.report_types import (
    BookingPeriodRow,
    BookingPeriodsReport,
    RunSummary,
)

# How far the period sum and the run's own figure may differ and still count as agreeing.
# Floating-point addition over thirty periods does not land on the same last bit as one
# aggregate over the same trades, and a table that cried mismatch over 1e-10 would be a table
# nobody reads. One hundredth of a currency unit is below what any report prints.
RECONCILE_TOLERANCE = 0.01


def build_booking_periods_report(
    run_id: str,
    units: List[RunUnit],
    run_summary: RunSummary,
    currency: str = '',
) -> BookingPeriodsReport:
    """
    Build the booking-periods table for one account currency.

    Args:
        run_id: The run this report belongs to
        units: The run's units, each carrying its sealed periods
        run_summary: The run's own KPI summary — the INDEPENDENT figure the sum is checked
            against, derived from the portfolio aggregate rather than from these periods
        currency: Which account currency to report; empty takes the first one the periods carry,
            which is every single-currency run

    Returns:
        The report; empty when the run booked no period, which is every run before this feature
        and every simulation until the sim books too
    """
    periods = [
        segment for unit in units for segment in unit.booking_segments
        if not currency or segment.figures.currency == currency
    ]
    if not periods:
        return BookingPeriodsReport(run_id=run_id, currency=currency)

    reported = periods[0].figures.currency
    rows = [_row(segment) for segment in sorted(
        periods, key=lambda s: (s.unit_name, s.segment_no))]

    total_net_pnl = sum(row.net_pnl for row in rows)
    run_figures = _run_figures(run_summary, reported)
    run_net_pnl = run_figures.net_pnl if run_figures else 0.0

    return BookingPeriodsReport(
        run_id=run_id,
        currency=reported,
        periods=rows,
        total_net_pnl=total_net_pnl,
        total_fees=sum(row.total_fees for row in rows),
        total_trades=sum(row.trade_count for row in rows),
        run_net_pnl=run_net_pnl,
        run_total_trades=run_figures.total_trades if run_figures else 0,
        reconciles=abs(total_net_pnl - run_net_pnl) <= RECONCILE_TOLERANCE,
        # The deepest SINGLE-period decline, which is what this table's column holds. Not the
        # run's drawdown: a fall that runs across a period boundary is deeper than any one
        # period's own, and the run summary is where that figure lives.
        deepest_period_drawdown=min((row.max_drawdown for row in rows), default=0.0),
        final_equity=rows[-1].final_equity,
    )


def _row(segment) -> BookingPeriodRow:
    """
    One sealed period as a renderable row.

    Args:
        segment: The BookingSegment

    Returns:
        The row
    """
    f = segment.figures
    return BookingPeriodRow(
        unit_name=segment.unit_name,
        segment_no=segment.segment_no,
        opened_at=segment.opened_at.isoformat(),
        closed_at=segment.closed_at.isoformat(),
        reason=segment.reason.value,
        currency=f.currency,
        trade_count=segment.trade_count,
        net_pnl=f.net_pnl,
        total_fees=f.total_fees,
        win_rate=f.win_rate,
        profit_factor=f.profit_factor,
        final_equity=f.final_equity,
        min_equity=segment.segment_min_equity,
        max_equity=segment.segment_max_equity,
        max_drawdown=segment.segment_max_drawdown,
    )


def _run_figures(run_summary: RunSummary, currency: str):
    """
    The run's own figures for this currency — the independent side of the reconciliation.

    Args:
        run_summary: The run's KPI summary
        currency: The account currency

    Returns:
        The currency row, or None when the run reports none
    """
    for row in run_summary.currencies:
        if row.currency == currency:
            return row
    return None
