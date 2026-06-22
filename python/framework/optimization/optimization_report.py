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
from python.framework.optimization.optimization_analysis import rank, sensitivity, summarize_sweeps
from python.framework.reporting.store.run_results_ledger import RunResultsLedger
from python.framework.types.api.report_types import RunResultRow

# Where the human-facing ranked CSV lands (run output, not the ledger dir).
SWEEP_REPORT_DIR = Path('logs/sweeps')


def render_sweep_list() -> None:
    """Print every recorded sweep as an informative one-liner (most recent last)."""
    ledger = RunResultsLedger(Path(AppConfigManager().get_run_results_path()))
    summaries = summarize_sweeps(ledger.read_rows())

    print('\n' + '=' * 80)
    print(f"🎛 PARAMETER OPTIMIZATION — Sweeps ({len(summaries)})")
    print('=' * 80)
    if not summaries:
        print("No sweeps recorded yet. Run one: optimization_cli.py run <spec>.json")
        print('=' * 80 + '\n')
        return

    for s in summaries:
        started = f"{s.started:%Y-%m-%d %H:%M}" if s.started else '—'
        duration = '~' + _fmt_duration(s.duration_s)
        runs = f"{s.run_count} runs ({s.ok_count} ok" \
               + (f", {s.error_count} err" if s.error_count else '') + ')'
        objective = f"{s.objective}{'↑' if s.maximize else '↓'}" if s.objective else '—'
        symbols = ','.join(s.symbols) if s.symbols else '—'
        print(f"{s.sweep_id}  {started} UTC  {duration:>8}  {runs:<18}  "
              f"obj={objective:<14}  {s.decision_logic_type} v{s.decision_version}  "
              f"{s.base_config}·{symbols}")
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
    ledger = RunResultsLedger(Path(AppConfigManager().get_run_results_path()))
    rows = ledger.read_rows(sweep_id=sweep_id)

    print('\n' + '=' * 80)
    print(f"🎛 PARAMETER OPTIMIZATION — Sweep {sweep_id}")
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
    print(f"Objective: {objective} ({direction})"
          + (f" | currency: {objective_currency}" if objective_currency else ''))
    print(f"Combinations: {len(rows)} ({len(rows) - len(error_rows)} ok, {len(error_rows)} errored)")
    _print_header_meta(rows)

    ranked = rank(rows, objective, maximize, objective_currency)
    _print_ranking(ranked, objective, top_n)
    _print_sensitivity(rows, objective, objective_currency)
    _print_errors(error_rows)

    csv_path = _write_csv(ranked, sweep_id)
    print(f"\n📄 Ranked table → {csv_path}")
    print('=' * 80 + '\n')


def _print_header_meta(rows: List[RunResultRow]) -> None:
    """Print sweep-level provenance from the ledger header columns (config, versions, span)."""
    r = rows[0]
    base = r.scenario_set_name.split('__', 1)[0]   # strip the per-combo sweep tag
    symbols = ', '.join(r.symbols) if r.symbols else '—'
    workers = ', '.join(f"{n} v{v}" for n, v in sorted(r.worker_versions.items())) or '—'
    code = (r.git_commit[:7] if r.git_commit else '—') + (' (dirty)' if r.git_dirty else '')
    print(f"Base config:  {base}  ·  symbols: {symbols}  ·  broker: {r.data_broker_type}")
    print(f"Decision:     {r.decision_logic_type} v{r.decision_version}")
    print(f"Workers:      {workers}")
    print(f"Code:         git {code}")

    # Sweep span from the per-run start timestamps (the ledger has no per-run end), so the
    # duration is first-start → last-start — the total minus the final run's own runtime.
    stamps = sorted(datetime.fromisoformat(x.run_timestamp) for x in rows if x.run_timestamp)
    if stamps:
        span = (stamps[-1] - stamps[0]).total_seconds()
        print(f"Sweep:        {len(rows)} runs  ·  {stamps[0]:%Y-%m-%d %H:%M:%S} → "
              f"{stamps[-1]:%H:%M:%S} UTC  ·  ~{_fmt_duration(span)} (across run starts)")


def _fmt_duration(seconds: float) -> str:
    """Compact h/m/s duration string (e.g. '3m 24s')."""
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _print_ranking(ranked: List[RunResultRow], objective: str, top_n: int) -> None:
    """Print the best combinations with their objective + key KPIs + grid point."""
    print('\n' + '-' * 80)
    print(f"🏆 BEST COMBINATIONS (top {min(top_n, len(ranked))})")
    print('-' * 80)
    print(f"{'#':>2} | {objective:>12} | {'net_pnl':>10} | {'win_rate':>8} | "
          f"{'trades':>6} | {'param_hash':>10} | parameters")
    print('-' * 80)
    for i, row in enumerate(ranked[:top_n], start=1):
        params = row.sweep_params or {}
        params_str = ', '.join(f"{_short_path(k)}={v}" for k, v in sorted(params.items()))
        print(f"{i:>2} | {getattr(row, objective):>12.4f} | {row.net_pnl:>10.2f} | "
              f"{row.win_rate * 100:>7.1f}% | {row.total_trades:>6} | "
              f"{row.param_hash[:10]:>10} | {params_str}")


def _print_sensitivity(
    rows: List[RunResultRow], objective: str, objective_currency: Optional[str]) -> None:
    """Print the one-factor marginal effect per swept parameter (ranked by influence)."""
    sens = sensitivity(rows, objective, objective_currency)
    if not sens:
        return
    print('\n' + '-' * 80)
    print(f"📈 PARAMETER SENSITIVITY (influence on {objective}, one-factor)")
    print('-' * 80)
    for entry in sens:
        levels = ' | '.join(
            f"{level}: {mean:.4f}" for level, mean in sorted(entry.level_means.items()))
        print(f"  {_short_path(entry.param):<28} influence {entry.influence:>10.4f}")
        print(f"      {levels}")


def _print_errors(error_rows: List[RunResultRow]) -> None:
    """Warn about combinations that errored — recorded in the ledger but excluded from ranking."""
    if not error_rows:
        return
    print('\n' + '-' * 80)
    print(f"⚠️  ERRORED COMBINATIONS ({len(error_rows)}) — excluded from ranking, operator action needed")
    print('-' * 80)
    for row in error_rows:
        params = row.sweep_params or {}
        params_str = ', '.join(f"{_short_path(k)}={v}" for k, v in sorted(params.items())) or '(base)'
        print(f"  {params_str}")
        print(f"      {row.error}")


def _short_path(dotted_path: str) -> str:
    """Drop the section prefix for compact display (decision_logic_config.x → x)."""
    return dotted_path.split('.', 1)[-1]


def _write_csv(ranked: List[RunResultRow], sweep_id: str) -> Path:
    """Write the ranked typed rows to logs/sweeps/<sweep_id>_ranked.csv."""
    SWEEP_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = SWEEP_REPORT_DIR / f'{sweep_id}_ranked.csv'
    flat = [_flat(row) for row in ranked]
    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(RunResultRow.model_fields))
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
