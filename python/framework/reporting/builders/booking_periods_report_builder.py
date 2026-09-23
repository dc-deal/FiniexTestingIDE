"""
Booking-periods report builder (#537) — the Hauptbuch as a table, and what its check can prove.

A run's booking periods are already derived; this maps them to renderable rows and answers the
one question the table exists for: **did every closed trade reach exactly one period?**

That is a COMPLETENESS check over the partition, and it is deliberately stated that way rather
than as a check of the arithmetic — because the two figures it compares are NOT two derivations
of the money. They are one value carried two ways, and the divergence point is a single local
variable inside the close (measured 2026-09-22):

    portfolio_manager.py:504 / :516
        realized_pnl = position.unrealized_pnl
                   |
         +---------+---------+
         v                   v
    _create_trade_record   _update_statistics(position, realized_pnl)
      net_pnl=...(:777)       _total_profit += ...   (:1474)
         |                   |
         v                   v
    bounded deque ->      RunSummary.net_pnl
    exit-time window ->
    period sum

So the LEFT side has been through retention (the trade deque is capped), windowing (exit_time in
`[opened, closed)`) and transport (the sim's process bridge); the RIGHT side is an unbounded
counter that forgets nothing. A disagreement therefore means a record was LOST on the way — the
deque evicted it, no period's window contains it, a segment list did not survive. It can never
mean a wrong P&L, a wrong fee or a wrong close formula: both sides inherit the identical float,
and an injected 30 % arithmetic defect moves them together with a delta of exactly zero.

The genuinely independent second derivation exists one method away and nothing compares it yet:
at SPOT the balance moves by `lots * price +/- fee` (portfolio_manager.py:484-497) while the P&L
comes from mark-to-market. Two arithmetics over one event — that pair would be a real check.

A mismatch is REPORTED, never raised (§12: reports calculate and render, they do not judge). It
is the finding the table exists to surface.
"""

from typing import List

from python.framework.reporting.builders.run_unit import RunUnit
from python.framework.types.api.report_types import (
    BookingPeriodRow,
    BookingPeriodsReport,
    DeploymentBookingPeriodRow,
    RunResultRow,
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
        run_summary: The run's own KPI summary — the counter side of the completeness check
            (see the module docstring for what that check can and cannot prove)
        currency: Which account currency the TOTALS are about; empty takes the first one the
            periods carry, which is every single-currency run

    Returns:
        The report; empty when the run booked no period, which is every run before this feature
        and every simulation until the sim books too
    """
    booked = [segment for unit in units for segment in unit.booking_segments]
    if not booked:
        return BookingPeriodsReport(run_id=run_id, currency=currency)

    # ONE table is ONE account currency — a P&L column in two units is not a column. The
    # filter existed for that and did not fire: `if not currency or …` admitted EVERY segment
    # when `currency` was empty, which is what both coordinators pass, so a two-currency run
    # summed EUR and USD together and compared the result against the figure of ONE of them.
    # Measured: 140.0 against 100.0, `reconciles` false for a run in which nothing was wrong
    # (#539 audit). `currencies` then says the table is one of several — the periods of the
    # others are not lost, they are ledger rows like these and the deployment route serves
    # every one of them.
    present = sorted({segment.figures.currency for segment in booked})
    reported = currency or present[0]
    rows = [_row(segment) for segment in sorted(
        (s for s in booked if s.figures.currency == reported),
        key=lambda s: (s.unit_name, s.segment_no))]
    if not rows:
        return BookingPeriodsReport(
            run_id=run_id, currency=reported, currencies=present)

    total_net_pnl = sum(row.net_pnl for row in rows)
    # No figure for this currency means there is nothing to check against — NOT that the
    # check passed. A default of 0.0 here made a small period sum agree with a figure that
    # was never reported (measured on a two-currency run, #539 audit).
    run_figures = _run_figures(run_summary, reported)
    run_net_pnl = run_figures.net_pnl if run_figures else None

    return BookingPeriodsReport(
        run_id=run_id,
        currency=reported,
        currencies=present,
        periods=rows,
        total_net_pnl=total_net_pnl,
        total_fees=sum(row.total_fees for row in rows),
        total_trades=sum(row.trade_count for row in rows),
        run_net_pnl=run_net_pnl,
        run_total_trades=run_figures.total_trades if run_figures else None,
        reconciles=(None if run_net_pnl is None
                    else abs(total_net_pnl - run_net_pnl) <= RECONCILE_TOLERANCE),
        # The deepest SINGLE-period decline, which is what this table's column holds. Not the
        # run's drawdown: a fall that runs across a period boundary is deeper than any one
        # period's own, and the run summary is where that figure lives.
        deepest_period_drawdown=max((row.max_drawdown for row in rows), default=0.0),
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


def booking_periods_from_ledger_rows(
    rows: List[RunResultRow],
) -> List[DeploymentBookingPeriodRow]:
    """
    Ledger rows read back as booking-period rows — the same shape the run report renders.

    The deployment-wide counterpart of the report above, and deliberately NOT a report: it
    returns ROWS and no reconciliation. Across many runs there is no second, independently
    derived figure to check the sum against, so a check here could only compare the rows with
    themselves (§48). The caller states that absence rather than printing a total that cannot
    fail.

    Rows that book no period are skipped, not defaulted. Every row written before the booking
    journal is one of those — an aggregate per currency with no period at all — and an empty
    `segment_opened_at` is what says so. A made-up period zero would put a bar with no start
    on a chart.

    Args:
        rows: Ledger rows, in any order, usually of ONE deployment

    Returns:
        One row per booked period, ordered by currency and then by when the period opened —
        a currency's bars stay contiguous and read forwards inside it. Each carries its
        `run_id`, which across a deployment is the only thing that tells two periods apart:
        `segment_no` restarts wherever a session wrote no carry-over floor
    """
    periods = [
        DeploymentBookingPeriodRow(
            run_id=row.run_id,
            unit_name=row.unit_name,
            segment_no=row.segment_no or 0,
            opened_at=row.segment_opened_at,
            closed_at=row.segment_closed_at,
            reason=row.segment_close_reason,
            currency=row.currency,
            trade_count=row.total_trades,
            net_pnl=row.net_pnl,
            total_fees=row.total_fees,
            win_rate=row.win_rate,
            profit_factor=row.profit_factor,
            final_equity=row.final_equity,
            # The period's OWN band and decline, never the cumulative trio beside them on the
            # row: on this table the question is what each period did, and the running figure
            # would repeat the same number down the column.
            min_equity=row.segment_min_equity or 0.0,
            max_equity=row.segment_max_equity or 0.0,
            max_drawdown=row.segment_max_drawdown or 0.0,
        )
        for row in rows if row.segment_opened_at
    ]
    return sorted(periods, key=lambda p: (p.currency, p.opened_at))


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
