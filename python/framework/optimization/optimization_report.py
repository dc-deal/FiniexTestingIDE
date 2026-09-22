"""
Optimization report (#390) — present a sweep's ranking + sensitivity.

Thin presenter over the run-results ledger: reads a sweep's typed rows, ranks them by the
objective, and prints the best combinations + the one-factor sensitivity (which parameter
moves the objective most). Also writes the ranked table as CSV. Pure presentation — the
ranking/sensitivity calculation lives in optimization_analysis.
"""

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from python.configuration.app_config_manager import AppConfigManager
from python.framework.optimization.optimization_analysis import (
    DegenerateRankingAdvisory,
    MixedLogicVersionAdvisory,
    degenerate_ranking_advisory,
    mixed_logic_version_advisory,
    rank,
    sensitivity,
    summarize_sweeps,
)
from python.framework.reporting.store.run_results_ledger import RunResultsLedger
from python.framework.types.api.report_types import RunResultRow



def render_sweep_list() -> None:
    """Print every recorded sweep as an informative one-liner (most recent last)."""
    ledger = RunResultsLedger(Path(AppConfigManager().get_run_ledger_path()))
    summaries = summarize_sweeps(ledger.read_rows())

    print('\n' + '=' * 80)
    print(f'🎛 PARAMETER OPTIMIZATION — Sweeps ({len(summaries)})')
    print('=' * 80)
    if not summaries:
        print('No sweeps recorded yet. Run one: optimization_cli.py run <spec>.json')
        print('=' * 80 + '\n')
        return

    for s in summaries:
        started = f'{s.started:%Y-%m-%d %H:%M}' if s.started else '—'
        duration = '~' + _fmt_duration(s.duration_s)
        runs = f'{s.run_count} runs ({s.ok_count} ok' \
               + (f', {s.error_count} err' if s.error_count else '') + ')'
        objective = f"{s.objective}{'↑' if s.maximize else '↓'}" if s.objective else '—'
        symbols = ','.join(s.symbols) if s.symbols else '—'
        print(f'{s.sweep_id}  {started} UTC  {duration:>8}  {runs:<18}  '
              f'obj={objective:<14}  {s.decision_logic_type} v{s.decision_version}  '
              f'{s.base_config}·{symbols}')
    print('=' * 80 + '\n')


def render_sweep_report(
    sweep_id: str,
    objective: Optional[str] = None,
    maximize: Optional[bool] = None,
    objective_currency: Optional[str] = None,
    top_n: int = 10,
) -> None:
    """
    Print a sweep's ranked combinations + parameter sensitivity and write the ranked CSV.

    Args:
        sweep_id: The sweep to report on
        objective: Ledger KPI field to rank by (None = the sweep spec's own objective)
        maximize: Rank direction (None = the sweep spec's own direction; False e.g. for max_drawdown)
        objective_currency: Restrict to this currency (needed when > 1 currency)
        top_n: How many top combinations to print
    """
    ledger = RunResultsLedger(Path(AppConfigManager().get_run_ledger_path()))
    rows = ledger.read_rows(sweep_id=sweep_id)

    print('\n' + '=' * 80)
    print(f'🎛 PARAMETER OPTIMIZATION — Sweep {sweep_id}')
    print('=' * 80)

    if not rows:
        print(f"⚠️  No ledger rows for sweep '{sweep_id}'.")
        print('=' * 80 + '\n')
        return

    # Default objective + direction to what the sweep's spec declared (recorded in the ledger),
    # so `report <sweep_id>` ranks by the spec, not a hardcoded fallback. Explicit args override.
    if objective is None:
        objective = next((r.sweep_objective for r in rows if r.sweep_objective), None) or 'expectancy'
    if maximize is None:
        spec_maximize = next((r.sweep_maximize for r in rows if r.sweep_maximize is not None), None)
        maximize = spec_maximize if spec_maximize is not None else True

    error_rows = [r for r in rows if r.status == 'error']
    direction = 'maximize' if maximize else 'minimize'
    print(f'Objective: {objective} ({direction})'
          + (f' | currency: {objective_currency}' if objective_currency else ''))
    print(f'Combinations: {len(rows)} ({len(rows) - len(error_rows)} ok, {len(error_rows)} errored)')
    _print_header_meta(rows)

    ranked = rank(rows, objective, maximize, objective_currency)
    _print_mixed_logic_version(mixed_logic_version_advisory(ranked))
    _print_degenerate_ranking(degenerate_ranking_advisory(ranked, objective))
    _print_ranking(ranked, objective, top_n)
    _print_sensitivity(rows, objective, objective_currency)
    _print_errors(error_rows)

    csv_path = _write_csv(ranked, sweep_id)
    print(f'\n📄 Ranked table → {csv_path}')
    print('=' * 80 + '\n')


def _print_header_meta(rows: List[RunResultRow]) -> None:
    """Print sweep-level provenance from the ledger header columns (config, versions, span)."""
    r = rows[0]
    base = r.scenario_set_name.split('__', 1)[0]   # strip the per-combo sweep tag
    symbols = ', '.join(r.symbols) if r.symbols else '—'
    workers = ', '.join(f'{n} v{v}' for n, v in sorted(r.worker_versions.items())) or '—'
    code = (r.git_commit[:7] if r.git_commit else '—') + (' (dirty)' if r.git_dirty else '')
    print(f'Base config:  {base}  ·  symbols: {symbols}  ·  broker: {r.data_broker_type}')
    print(f'Decision:     {r.decision_logic_type} v{r.decision_version}')
    print(f'Workers:      {workers}')
    print(f'Code:         git {code}')

    # Sweep span from the per-run start timestamps (the ledger has no per-run end), so the
    # duration is first-start → last-start — the total minus the final run's own runtime.
    stamps = sorted(datetime.fromisoformat(x.run_timestamp) for x in rows if x.run_timestamp)
    if stamps:
        span = (stamps[-1] - stamps[0]).total_seconds()
        print(f'Sweep:        {len(rows)} runs  ·  {stamps[0]:%Y-%m-%d %H:%M:%S} → '
              f'{stamps[-1]:%H:%M:%S} UTC  ·  ~{_fmt_duration(span)} (across run starts)')


def _fmt_duration(seconds: float) -> str:
    """Compact h/m/s duration string (e.g. '3m 24s')."""
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f'{h}h {m}m {s}s'
    if m:
        return f'{m}m {s}s'
    return f'{s}s'


def _print_mixed_logic_version(advisory: Optional[MixedLogicVersionAdvisory]) -> None:
    """
    Warn when the ranked rows were not all produced by the same logic.

    Args:
        advisory: The analyzer's result, None when every row agrees

    Returns:
        None — prints to the console
    """
    if advisory is None:
        return

    print('\n' + '-' * 80)
    print('⚠️  THIS RANKING SPANS SEVERAL LOGIC VERSIONS')
    print('-' * 80)
    for version, count in zip(advisory.versions, advisory.counts):
        label = 'unknown (written before rows carried a version)' if version is None else f'v{version}'
        print(f'  {count:>5} rows  ·  {label}')
    print('\nA ledger column can keep its name while the measure behind it changes — the '
          'account\ndrawdown did exactly that. Rows from different versions therefore answer '
          'different\nquestions, and a best-first list over them looks like a valid ranking. '
          'Restrict the\nsweep to one version, or re-run the older combinations.')


def _print_degenerate_ranking(advisory: Optional[DegenerateRankingAdvisory]) -> None:
    """
    Warn when the ranking's winner never traded — printed BEFORE the table it describes.

    Deliberately concrete rather than a sentence of caution: it says how many combinations
    are empty, shows the ones standing at the top, and names the best one that actually
    traded with its rank, so the reader can act on the ranking instead of distrusting all
    of it.

    Args:
        advisory: The analyzer's result, None when the winner did trade

    Returns:
        None — prints to the console
    """
    if advisory is None:
        return

    print('\n' + '-' * 80)
    print(f'⚠️  THE BEST-RANKED COMBINATION MADE NO TRADE — '
          f'{advisory.zero_trade_count} of {advisory.total_ranked} traded nothing')
    print('-' * 80)
    print(f"A combination that never opened a position scores perfectly on any measure of "
          f"loss or risk:\nit has no drawdown because it took none. Ranking by "
          f"'{advisory.objective}' therefore puts\ndoing nothing first, and that is a "
          f"property of the objective, not a result.")
    print('\nRanked at the top, with no trades:')
    for i, row in enumerate(advisory.zero_trade_leaders, start=1):
        params = ', '.join(
            f'{_short_path(k)}={v}' for k, v in sorted((row.sweep_params or {}).items()))
        print(f'  {i:>2}. {getattr(row, advisory.objective):>12.4f}  |  {params}')

    if advisory.best_trading_row is None:
        print('\nNo combination in this sweep traded at all — the grid, the data window or '
              'the\nentry condition is what to look at, not the ranking.')
    else:
        row = advisory.best_trading_row
        params = ', '.join(
            f'{_short_path(k)}={v}' for k, v in sorted((row.sweep_params or {}).items()))
        print(f'\nBest combination that actually traded — rank '
              f'{advisory.best_trading_rank} of {advisory.total_ranked}:')
        print(f'  {getattr(row, advisory.objective):>12.4f}  |  {row.total_trades} trades  '
              f'|  net {row.net_pnl:.2f}  |  {params}')

    print('\nA risk measure is a THRESHOLD — reject what exceeds it — not a criterion to '
          'rank on\nalone. Pair it with a return objective, or rank on return and cut by '
          'this one.')


def _print_ranking(ranked: List[RunResultRow], objective: str, top_n: int) -> None:
    """Print the best combinations with their objective + key KPIs + grid point."""
    print('\n' + '-' * 80)
    print(f'🏆 BEST COMBINATIONS (top {min(top_n, len(ranked))})')
    print('-' * 80)
    print(f"{'#':>2} | {objective:>12} | {'net_pnl':>10} | {'win_rate':>8} | "
          f"{'trades':>6} | {'param_hash':>10} | parameters")
    print('-' * 80)
    for i, row in enumerate(ranked[:top_n], start=1):
        params = row.sweep_params or {}
        params_str = ', '.join(f'{_short_path(k)}={v}' for k, v in sorted(params.items()))
        print(f'{i:>2} | {getattr(row, objective):>12.4f} | {row.net_pnl:>10.2f} | '
              f'{row.win_rate * 100:>7.1f}% | {row.total_trades:>6} | '
              f'{row.param_hash[:10]:>10} | {params_str}')


def _print_sensitivity(
    rows: List[RunResultRow], objective: str, objective_currency: Optional[str]) -> None:
    """Print the one-factor marginal effect per swept parameter (ranked by influence)."""
    sens = sensitivity(rows, objective, objective_currency)
    if not sens:
        return
    print('\n' + '-' * 80)
    print(f'📈 PARAMETER SENSITIVITY (influence on {objective}, one-factor)')
    print('-' * 80)
    for entry in sens:
        levels = ' | '.join(
            f'{level}: {mean:.4f}' for level, mean in sorted(entry.level_means.items()))
        print(f'  {_short_path(entry.param):<28} influence {entry.influence:>10.4f}')
        print(f'      {levels}')


def _print_errors(error_rows: List[RunResultRow]) -> None:
    """Warn about combinations that errored — recorded in the ledger but excluded from ranking."""
    if not error_rows:
        return
    print('\n' + '-' * 80)
    print(f'⚠️  ERRORED COMBINATIONS ({len(error_rows)}) — excluded from ranking, operator action needed')
    print('-' * 80)
    for row in error_rows:
        params = row.sweep_params or {}
        params_str = ', '.join(f'{_short_path(k)}={v}' for k, v in sorted(params.items())) or '(base)'
        print(f'  {params_str}')
        print(f'      {row.error}')


def _short_path(dotted_path: str) -> str:
    """Drop the section prefix for compact display (decision_logic_config.x → x)."""
    return dotted_path.split('.', 1)[-1]


def _write_csv(ranked: List[RunResultRow], sweep_id: str) -> Path:
    """
    Write the ranked typed rows into the sweep's OWN directory, beside the combination
    runs they rank.

    The root comes from config (file_logging.run_logs.sweeps). A fourth hardcoded tree is
    how this evaluation used to land somewhere the run index does not scan, and therefore
    stayed invisible to the API.

    Args:
        ranked: The ranked rows to write
        sweep_id: The sweep whose directory receives the file

    Returns:
        The written CSV path
    """
    sweep_dir = Path(
        AppConfigManager().get_file_logging_config_object().run_logs.sweeps) / sweep_id
    sweep_dir.mkdir(parents=True, exist_ok=True)
    path = sweep_dir / 'ranked.csv'
    flat = [_flat(row) for row in ranked]
    # The header comes from a FLATTENED row, not from `model_fields`. `model_dump()` includes
    # COMPUTED fields and `model_fields` does not, so `run_kind` was in every dict and in no
    # header — and csv.DictWriter refuses a dict carrying a key it was not told about. The
    # export raised on every non-empty sweep; no test ever called this with a real row, which
    # is why it stood. Falling back to the declared fields keeps the empty case writing a
    # header-only file rather than an empty one.
    fieldnames = list(flat[0]) if flat else list(RunResultRow.model_fields)
    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(flat)
    return path


def _flat(row: RunResultRow) -> Dict[str, Any]:
    """Flatten a row for CSV — the structured columns are JSON-encoded back to strings."""
    data = row.model_dump()
    data['worker_versions'] = json.dumps(data['worker_versions'])
    data['symbols'] = json.dumps(data['symbols'])
    data['sweep_params'] = json.dumps(data['sweep_params']) if data['sweep_params'] is not None else ''
    return data
