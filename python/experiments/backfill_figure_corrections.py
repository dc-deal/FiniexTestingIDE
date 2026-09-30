"""
Carry the contract-18 corrections into the runs recorded before them — exactly, or not at all.

A single-use migration (§27), not a permanent code path: nothing imports it. Each value comes
from what the run itself stored, and only where the stored records are COMPLETE — a unit whose
trade rows number fewer than its `total_trades` (the bounded trade history dropped some) keeps
its figures as they were, and the run is named:

- a unit's `losing_trades`: a trade that realised exactly nothing is neither a winner nor a
  loser — recounted from its trade rows (`net_pnl < 0`).
- a unit's `total_fees` is the fees of its CLOSED trades, summed from its trade rows; what it was
  until now — everything the run charged, open positions included — moves to `fees_charged`.
- the portfolio aggregates, the run summary and the aggregated portfolio are then REBUILT by the
  builders themselves (which also applies the per-account streaks, the first-account tie rule of
  the drawdown trio and the stamped spot currency split), and must reproduce the stored net P&L.
- the trade history's own analytics and scenario totals are recomputed from its rows.
- `booking-periods.unit_totals` are refolded from the ledger rows with the corrected `FIRST`.

NOT back-filled, because the records cannot answer it: a spot trade's excursion between its entry
and its close — that needs the ticks replayed, and those runs keep the values they were written
with.

Usage:
    python python/experiments/backfill_figure_corrections.py --preview
    python python/experiments/backfill_figure_corrections.py
"""

import argparse
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

from python.configuration.app_config_manager import AppConfigManager
from python.framework.reporting.builders.aggregated_portfolio_report_builder import (
    build_aggregated_portfolio_report,
)
from python.framework.reporting.builders.booking_periods_report_builder import (
    unit_totals_from_ledger_rows,
)
from python.framework.reporting.builders.report_aggregators import (
    aggregate_portfolio_by_currency,
    aggregate_trade_analytics,
)
from python.framework.reporting.io.artifact_specs import (
    AGGREGATED_PORTFOLIO_ARTIFACT,
    BOOKING_PERIODS_ARTIFACT,
    EXECUTION_STATS_ARTIFACT,
    PENDING_ORDERS_ARTIFACT,
    PORTFOLIO_ARTIFACT,
    RUN_SUMMARY_ARTIFACT,
    TRADE_HISTORY_ARTIFACT,
)
from python.framework.reporting.io.report_artifact_io import read_artifact, write_artifact
from python.framework.reporting.io.report_filters import filter_trade_history_report
from python.framework.reporting.store.run_results_ledger import RunResultsLedger
from python.framework.types.api.report_types import PortfolioReport, RunResultRow, TradeHistoryReport
from python.framework.types.log_layout_types import IO_SUBDIR

# A stored float carried through JSON: equal to this, or it is not the same figure.
_TOLERANCE = 1e-6


def _corrected_units(portfolio: PortfolioReport,
                     trades: TradeHistoryReport) -> Tuple[PortfolioReport, List[str]]:
    """
    Each unit's losing count and fee total, recounted from its complete trade rows.

    Args:
        portfolio: The stored portfolio report
        trades: The run's stored trade history

    Returns:
        The corrected report, and the units left as they were because their rows are incomplete
    """
    by_unit: Dict[str, list] = defaultdict(list)
    for row in trades.trades:
        by_unit[row.scenario_name].append(row)
    units, incomplete = [], []
    for unit in portfolio.units:
        rows = by_unit.get(unit.name, [])
        if len(rows) != unit.total_trades:
            incomplete.append(unit.name)
            units.append(unit)
            continue
        units.append(unit.model_copy(update={
            'losing_trades': sum(1 for r in rows if r.net_pnl < 0),
            'fees_charged': unit.total_fees if not unit.fees_charged else unit.fees_charged,
            'total_fees': sum(r.total_fees for r in rows),
        }))
    return portfolio.model_copy(update={'units': units}), incomplete


def _run(io: Path, run_id: str, rows: List[RunResultRow], preview: bool) -> List[str]:
    """
    Correct one run's artifacts.

    Args:
        io: The run's io directory
        run_id: The run
        rows: The run's ledger rows
        preview: Write nothing

    Returns:
        What was done, for the report line
    """
    done: List[str] = []
    trade_path = io / TRADE_HISTORY_ARTIFACT.filename
    portfolio_path = io / PORTFOLIO_ARTIFACT.filename
    if not trade_path.exists() or not portfolio_path.exists():
        return ['no artifacts']
    trades = read_artifact(trade_path, TRADE_HISTORY_ARTIFACT)
    stored = read_artifact(portfolio_path, PORTFOLIO_ARTIFACT)
    portfolio, incomplete = _corrected_units(stored, trades)
    aggregates = aggregate_portfolio_by_currency(portfolio.units)
    before = {a.currency: a.net_profit for a in stored.aggregates}
    for new in aggregates:
        if abs(before.get(new.currency, float('nan')) - new.net_profit) > _TOLERANCE:
            raise ValueError(f'{run_id}: rebuilt {new.currency} aggregate does not reproduce the '
                             f'stored net profit — refusing to write')
    portfolio = portfolio.model_copy(update={'aggregates': aggregates})
    if incomplete:
        done.append(f'left as stored (rows incomplete): {", ".join(incomplete)}')

    summary_path = io / RUN_SUMMARY_ARTIFACT.filename
    summary = read_artifact(summary_path, RUN_SUMMARY_ARTIFACT) if summary_path.exists() else None
    if summary is not None:
        by_currency = {a.currency: a for a in aggregates}
        streaks = {a.currency: a for a in aggregate_trade_analytics(trades.trades)}
        currencies = []
        for c in summary.currencies:
            agg, analytics = by_currency.get(c.currency), streaks.get(c.currency)
            update = {}
            if agg is not None:
                update.update(
                    losing_trades=agg.losing_trades, total_fees=agg.total_fees,
                    fees_charged=agg.fees_charged, max_equity=agg.max_equity,
                    account_max_dd_pct=agg.account_max_dd_pct,
                    account_max_drawdown_unit=agg.account_max_drawdown_unit)
            if analytics is not None:
                update.update(max_consecutive_wins=analytics.max_consecutive_wins,
                              max_consecutive_losses=analytics.max_consecutive_losses)
            currencies.append(c.model_copy(update=update))
        summary = summary.model_copy(update={'currencies': currencies})
        done.append('run_summary')

    aggregated_path = io / AGGREGATED_PORTFOLIO_ARTIFACT.filename
    aggregated = None
    if aggregated_path.exists():
        aggregated = build_aggregated_portfolio_report(
            run_id, portfolio,
            read_artifact(io / EXECUTION_STATS_ARTIFACT.filename, EXECUTION_STATS_ARTIFACT),
            read_artifact(io / PENDING_ORDERS_ARTIFACT.filename, PENDING_ORDERS_ARTIFACT))
        done.append('aggregated_portfolio')

    # Recomputed from the rows by the same function the route applies on every read.
    trades = filter_trade_history_report(trades)
    done.append('trade_history')

    booking_path = io / BOOKING_PERIODS_ARTIFACT.filename
    booking = None
    if booking_path.exists():
        booking = read_artifact(booking_path, BOOKING_PERIODS_ARTIFACT)
        period_rows = [r for r in rows if r.period_opened_at and r.currency == booking.currency]
        unit_totals = unit_totals_from_ledger_rows(period_rows)
        closing = [t.final_equity for t in unit_totals]
        booking = booking.model_copy(update={
            'unit_totals': unit_totals,
            'total_final_equity': (None if not closing or any(v is None for v in closing)
                                   else sum(closing))})
        done.append('booking_periods')

    if not preview:
        write_artifact(portfolio, io, PORTFOLIO_ARTIFACT)
        if summary is not None:
            write_artifact(summary, io, RUN_SUMMARY_ARTIFACT)
        if aggregated is not None:
            write_artifact(aggregated, io, AGGREGATED_PORTFOLIO_ARTIFACT)
        write_artifact(trades, io, TRADE_HISTORY_ARTIFACT)
        if booking is not None:
            write_artifact(booking, io, BOOKING_PERIODS_ARTIFACT)
    return ['portfolio'] + done


def main() -> None:
    """Parse arguments and back-fill."""
    parser = argparse.ArgumentParser(description='One-time contract-18 back-fill')
    parser.add_argument('--preview', action='store_true', help='List and write nothing')
    args = parser.parse_args()

    config = AppConfigManager()
    index = pd.read_parquet(Path(config.get_file_logging_config_object().run_index))
    rows_by_run: Dict[str, List[RunResultRow]] = defaultdict(list)
    for row in RunResultsLedger(Path(config.get_run_ledger_path())).read_rows():
        rows_by_run[row.run_id].append(row)

    totals = Counter()
    for run_id, run_dir in sorted(zip(index.run_id, index.run_dir)):
        done = _run(Path(run_dir) / IO_SUBDIR, run_id, rows_by_run.get(run_id, []), args.preview)
        totals['runs' if done != ['no artifacts'] else 'without artifacts'] += 1
        totals['runs with incomplete units'] += any(d.startswith('left as stored') for d in done)
        print(f'  {"would correct" if args.preview else "corrected"} {run_id}: {", ".join(done)}')
    print()
    for label, count in totals.items():
        print(f'  {label:<28}: {count}')


if __name__ == '__main__':
    main()
