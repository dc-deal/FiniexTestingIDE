"""
Fill the contract-17 figures into the runs recorded before them — each exactly, or not at all.

A single-use migration (§27), not a permanent code path: nothing imports it. Every value comes
from what the run itself already stored, and a value that cannot be derived exactly stays None
— not recorded, never guessed:

- `trade_history`: a partial close's `commission_cost` gains the exit fee it left out
  (`total_fees - swap_cost`, exact: `total_fees` already carried it), and every execution says
  how many rows of its unit share it (`shared_by`).
- the ledger's period rows and `booking_periods`: `opening_equity` is the previous period's close
  — exactly what the recorder now stamps. A unit's FIRST period opens with its initial balance
  in a backtest that started holding nothing but its account currency, and stays None
  otherwise (an AutoTrader session starts from whatever it carried). The cost split is summed
  over the trades the period closed, with the builder's own window, and kept only where its
  control totals hold — the same trade count and `commission + swap == total_fees`.
- `unit_totals` and `total_final_equity`, by the same fold the report now runs.
- `portfolio` / `run_summary` / `aggregated_portfolio`: rebuilt by the builders themselves from
  the stored unit rows, and checked against the stored figures they must reproduce.
- the run's market time: its units' tick timespans covered together (backtests; an AutoTrader
  session recorded none).
- `run_meta`: the three declared-window hours it no longer carries.

Usage:
    python python/experiments/backfill_account_figures.py --preview
    python python/experiments/backfill_account_figures.py
"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from python.configuration.app_config_manager import AppConfigManager
from python.framework.reporting.builders.aggregated_portfolio_report_builder import (
    build_aggregated_portfolio_report,
)
from python.framework.reporting.builders.booking_periods_report_builder import (
    unit_totals_from_ledger_rows,
)
from python.framework.reporting.builders.report_aggregators import aggregate_portfolio_by_currency
from python.framework.reporting.io.artifact_specs import (
    AGGREGATED_PORTFOLIO_ARTIFACT,
    BOOKING_PERIODS_ARTIFACT,
    EXECUTION_STATS_ARTIFACT,
    PENDING_ORDERS_ARTIFACT,
    PORTFOLIO_ARTIFACT,
    RUN_META_ARTIFACT,
    RUN_SUMMARY_ARTIFACT,
    SCENARIO_DETAILS_ARTIFACT,
    TRADE_HISTORY_ARTIFACT,
)
from python.framework.reporting.io.report_artifact_io import read_artifact, write_artifact
from python.framework.reporting.store.run_ledger_index import RunLedgerIndex
from python.framework.reporting.store.run_results_ledger import LEDGER_COLUMNS, RunResultsLedger
from python.framework.types.api.report_types import RunResultRow, TradeHistoryReport
from python.framework.types.log_layout_types import IO_SUBDIR, RUN_TYPE_SIMULATION
from python.framework.utils.time_utils import covered_seconds

# A stored float carried through JSON and parquet: equal to this, or it is not the same figure.
_TOLERANCE = 1e-6

PeriodKey = Tuple[str, str, int]


def _fix_trade_history(report: TradeHistoryReport) -> Tuple[TradeHistoryReport, int]:
    """
    A partial close's commission with its exit fee, and every execution's sibling count.

    Args:
        report: The stored trade history

    Returns:
        The corrected report, and how many rows' commission moved
    """
    fixed = 0
    shared: Dict[str, Counter] = defaultdict(Counter)
    for row in report.trades:
        for ex in row.entry_executions + row.exit_executions:
            shared[row.scenario_name][ex.trade_id] += 1
    trades = []
    for row in report.trades:
        update = {}
        if row.commission_cost + row.swap_cost < row.total_fees - _TOLERANCE:
            update['commission_cost'] = row.total_fees - row.swap_cost
            fixed += 1
        counts = shared[row.scenario_name]
        update['entry_executions'] = [ex.model_copy(update={'shared_by': counts[ex.trade_id]})
                                      for ex in row.entry_executions]
        update['exit_executions'] = [ex.model_copy(update={'shared_by': counts[ex.trade_id]})
                                     for ex in row.exit_executions]
        trades.append(row.model_copy(update=update))
    return report.model_copy(update={'trades': trades}), fixed


def _period_values(
    rows: List[RunResultRow],
    trades: TradeHistoryReport,
    first_openings: Dict[str, Optional[float]],
) -> Dict[PeriodKey, Dict[str, Optional[float]]]:
    """
    The opening equity and the cost split of every booked period of one run.

    Args:
        rows: The run's ledger rows
        trades: Its corrected trade history
        first_openings: unit → what its FIRST period opened with, None where not exact

    Returns:
        (unit, currency, period_no) → the four values
    """
    values: Dict[PeriodKey, Dict[str, Optional[float]]] = {}
    booked = sorted((r for r in rows if r.period_opened_at),
                    key=lambda r: (r.unit_name, r.currency, r.period_no or 0))
    previous: Dict[Tuple[str, str], RunResultRow] = {}
    for row in booked:
        before = previous.get((row.unit_name, row.currency))
        opening = before.final_equity if before else first_openings.get(row.unit_name)
        opened = datetime.fromisoformat(row.period_opened_at)
        closed = datetime.fromisoformat(row.period_closed_at)
        # The builder's own window: realised in [opened, closed), exit basis.
        window = [t for t in trades.trades
                  if t.scenario_name == row.unit_name and t.currency == row.currency
                  and opened <= datetime.fromisoformat(t.exit_time) < closed]
        commission = sum(t.commission_cost for t in window)
        swap = sum(t.swap_cost for t in window)
        holds = (len(window) == row.total_trades
                 and abs(commission + swap - row.total_fees) <= _TOLERANCE)
        values[(row.unit_name, row.currency, row.period_no or 0)] = {
            'period_opening_equity': opening,
            'period_commission_cost': commission if holds else None,
            'period_swap_cost': swap if holds else None,
            'period_spread_cost': sum(t.spread_cost for t in window) if holds else None,
        }
        previous[(row.unit_name, row.currency)] = row
    return values


def _first_openings(io: Path, run_type: str) -> Dict[str, Optional[float]]:
    """
    What each unit's first period opened with, where that is exact.

    Args:
        io: The run's io directory
        run_type: 'simulation' or 'autotrader'

    Returns:
        unit → its initial balance for a backtest holding only its account currency at the start
    """
    if run_type != RUN_TYPE_SIMULATION or not (io / PORTFOLIO_ARTIFACT.filename).exists():
        return {}
    portfolio = read_artifact(io / PORTFOLIO_ARTIFACT.filename, PORTFOLIO_ARTIFACT)
    return {unit.name: unit.initial_balance for unit in portfolio.units
            if not unit.spot_mode or not any(
                amount for currency, amount in unit.initial_balances.items()
                if currency != unit.currency)}


def _tick_timespan(io: Path, run_type: str) -> Optional[float]:
    """
    The run's units' tick timespans covered together — backtests only.

    Args:
        io: The run's io directory
        run_type: 'simulation' or 'autotrader'

    Returns:
        The covered seconds, or None where no unit recorded its span
    """
    path = io / SCENARIO_DETAILS_ARTIFACT.filename
    if run_type != RUN_TYPE_SIMULATION or not path.exists():
        return None
    details = read_artifact(path, SCENARIO_DETAILS_ARTIFACT)
    spans = [(datetime.fromisoformat(u.first_tick_time), datetime.fromisoformat(u.last_tick_time))
             for u in details.units if u.first_tick_time and u.last_tick_time]
    return covered_seconds(spans) if spans else None


def _tick_timespan_total(io: Path) -> Optional[float]:
    """
    The units' tick timespans summed — the work.

    Args:
        io: The run's io directory

    Returns:
        The summed seconds, or None where none was recorded
    """
    path = io / SCENARIO_DETAILS_ARTIFACT.filename
    if not path.exists():
        return None
    details = read_artifact(path, SCENARIO_DETAILS_ARTIFACT)
    spans = [u.tick_timespan_seconds for u in details.units if u.first_tick_time]
    return sum(spans) if spans else None


def _rebuild_reports(io: Path, run_id: str, run_type: str, preview: bool) -> List[str]:
    """
    The portfolio aggregates, the run summary and the aggregated portfolio, rebuilt.

    Args:
        io: The run's io directory
        run_id: The run
        run_type: 'simulation' or 'autotrader'
        preview: Write nothing

    Returns:
        What was rebuilt, for the report line
    """
    done = []
    portfolio = read_artifact(io / PORTFOLIO_ARTIFACT.filename, PORTFOLIO_ARTIFACT)
    aggregates = aggregate_portfolio_by_currency(portfolio.units)
    old = {a.currency: a for a in portfolio.aggregates}
    for new in aggregates:
        stored = old.get(new.currency)
        if stored is None or abs(stored.net_profit - new.net_profit) > _TOLERANCE:
            raise ValueError(f'{run_id}: rebuilt {new.currency} aggregate does not reproduce the '
                             f'stored net profit — refusing to write')
    if not preview:
        write_artifact(portfolio.model_copy(update={'aggregates': aggregates}), io,
                       PORTFOLIO_ARTIFACT)
    done.append('portfolio')

    by_currency = {a.currency: a for a in aggregates}
    summary_path = io / RUN_SUMMARY_ARTIFACT.filename
    if summary_path.exists():
        summary = read_artifact(summary_path, RUN_SUMMARY_ARTIFACT)
        currencies = []
        for c in summary.currencies:
            agg = by_currency.get(c.currency)
            currencies.append(c if agg is None else c.model_copy(update={
                'final_equity': agg.final_equity,
                'total_final_equity': agg.total_final_equity,
                'total_initial_balance': agg.total_initial_balance,
                'unit_count': agg.unit_count,
                'account_max_drawdown_unit': agg.account_max_drawdown_unit,
            }))
        update = {'currencies': currencies}
        if run_type == RUN_TYPE_SIMULATION:
            update['tick_timespan_seconds'] = _tick_timespan(io, run_type)
            update['tick_timespan_total_seconds'] = _tick_timespan_total(io)
        if not preview:
            write_artifact(summary.model_copy(update=update), io, RUN_SUMMARY_ARTIFACT)
        done.append('run_summary')

    aggregated_path = io / AGGREGATED_PORTFOLIO_ARTIFACT.filename
    if aggregated_path.exists():
        stored = read_artifact(aggregated_path, AGGREGATED_PORTFOLIO_ARTIFACT)
        rebuilt = build_aggregated_portfolio_report(
            run_id, portfolio.model_copy(update={'aggregates': aggregates}),
            read_artifact(io / EXECUTION_STATS_ARTIFACT.filename, EXECUTION_STATS_ARTIFACT),
            read_artifact(io / PENDING_ORDERS_ARTIFACT.filename, PENDING_ORDERS_ARTIFACT))
        for before, after in zip(stored.currencies, rebuilt.currencies):
            if abs(before.combined.balance_pnl - after.combined.balance_pnl) > _TOLERANCE:
                raise ValueError(f'{run_id}: rebuilt aggregated portfolio does not reproduce the '
                                 f'stored balance P&L — refusing to write')
        if not preview:
            write_artifact(rebuilt, io, AGGREGATED_PORTFOLIO_ARTIFACT)
        done.append('aggregated_portfolio')

    meta_path = io / RUN_META_ARTIFACT.filename
    if meta_path.exists():
        # Read through the model, which no longer declares the three hours, so they fall away.
        if not preview:
            write_artifact(read_artifact(meta_path, RUN_META_ARTIFACT), io, RUN_META_ARTIFACT)
        done.append('run_meta')
    return done


def main() -> None:
    """Parse arguments and back-fill."""
    parser = argparse.ArgumentParser(description='One-time contract-17 back-fill')
    parser.add_argument('--preview', action='store_true', help='List and write nothing')
    args = parser.parse_args()

    config = AppConfigManager()
    ledger_dir = Path(config.get_run_ledger_path())
    index = pd.read_parquet(Path(config.get_file_logging_config_object().run_index))
    runs = {rid: (Path(rd) / IO_SUBDIR, rt)
            for rid, rd, rt in zip(index.run_id, index.run_dir, index.run_type)}
    rows_by_run: Dict[str, List[RunResultRow]] = defaultdict(list)
    for row in RunResultsLedger(ledger_dir).read_rows():
        rows_by_run[row.run_id].append(row)

    period_updates: Dict[str, Dict[PeriodKey, Dict[str, Optional[float]]]] = {}
    timespans: Dict[str, Optional[float]] = {}
    totals = Counter()
    for run_id, (io, run_type) in sorted(runs.items()):
        trade_path = io / TRADE_HISTORY_ARTIFACT.filename
        if not trade_path.exists():
            totals['without artifacts'] += 1
            continue
        trades, fixed = _fix_trade_history(read_artifact(trade_path, TRADE_HISTORY_ARTIFACT))
        totals['partial-close commissions corrected'] += fixed
        values = _period_values(rows_by_run.get(run_id, []), trades, _first_openings(io, run_type))
        period_updates[run_id] = values
        timespans[run_id] = _tick_timespan(io, run_type)
        split = sum(1 for v in values.values() if v['period_commission_cost'] is not None)
        opened = sum(1 for v in values.values() if v['period_opening_equity'] is not None)
        totals['periods'] += len(values)
        totals['periods with a cost split'] += split
        totals['periods with an opening equity'] += opened
        rebuilt = _rebuild_reports(io, run_id, run_type, args.preview)

        booking_path = io / BOOKING_PERIODS_ARTIFACT.filename
        if booking_path.exists():
            booking = read_artifact(booking_path, BOOKING_PERIODS_ARTIFACT)
            periods = [p.model_copy(update={
                'opening_equity': values.get((p.unit_name, p.currency, p.period_no), {}).get(
                    'period_opening_equity'),
                'commission_cost': values.get((p.unit_name, p.currency, p.period_no), {}).get(
                    'period_commission_cost'),
                'swap_cost': values.get((p.unit_name, p.currency, p.period_no), {}).get(
                    'period_swap_cost'),
                'spread_cost': values.get((p.unit_name, p.currency, p.period_no), {}).get(
                    'period_spread_cost'),
            }) for p in booking.periods]
            ledger_rows = [r.model_copy(update=values.get(
                (r.unit_name, r.currency, r.period_no or 0), {}))
                for r in rows_by_run.get(run_id, [])
                if r.period_opened_at and r.currency == booking.currency]
            unit_totals = unit_totals_from_ledger_rows(ledger_rows)
            closing = [t.final_equity for t in unit_totals]
            booking = booking.model_copy(update={
                'periods': periods, 'unit_totals': unit_totals,
                'total_final_equity': (None if not closing or any(v is None for v in closing)
                                       else sum(closing))})
            if not args.preview:
                write_artifact(booking, io, BOOKING_PERIODS_ARTIFACT)
            rebuilt.append('booking_periods')
        if not args.preview:
            write_artifact(trades, io, TRADE_HISTORY_ARTIFACT)
        print(f'  {"would fill" if args.preview else "filled"} {run_id} ({run_type}): '
              f'{len(values)} period(s), {split} split, {opened} opening(s), '
              f'{fixed} commission fix(es), market time '
              f'{"—" if timespans[run_id] is None else f"{timespans[run_id] / 3600:.1f} h"} · '
              f'{", ".join(rebuilt)}')

    written = 0
    for fragment in RunLedgerIndex(ledger_dir, LEDGER_COLUMNS).fragments():
        frame = pd.read_parquet(fragment)
        run_id = str(frame['run_id'].iloc[0])
        if run_id not in period_updates:
            continue
        values = period_updates[run_id]
        for column in ('period_opening_equity', 'period_commission_cost',
                       'period_swap_cost', 'period_spread_cost'):
            frame[column] = [
                values.get((u, c, int(n) if pd.notna(n) else 0), {}).get(column)
                for u, c, n in zip(frame['unit_name'], frame['currency'], frame['period_no'])]
        frame['tick_timespan_seconds'] = timespans.get(run_id)
        frame = frame.reindex(columns=LEDGER_COLUMNS)
        written += 1
        if not args.preview:
            temporary = fragment.with_suffix('.parquet.tmp')
            frame.to_parquet(temporary, index=False)
            temporary.replace(fragment)

    print()
    for label, count in totals.items():
        print(f'  {label:<38}: {count}')
    print(f'  {"ledger fragments " + ("to write" if args.preview else "written"):<38}: {written}')
    if written and not args.preview:
        print(f'  rebuilt the ledger index ({RunLedgerIndex(ledger_dir, LEDGER_COLUMNS).rebuild()} rows)')


if __name__ == '__main__':
    main()
