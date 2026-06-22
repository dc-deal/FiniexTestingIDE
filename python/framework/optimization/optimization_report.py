"""
Optimization report (#390) — present a sweep's ranking + sensitivity.

Thin presenter over the run-results ledger: reads a sweep's typed rows, ranks them by the
objective, and prints the best combinations + the one-factor sensitivity (which parameter
moves the objective most). Also writes the ranked table as CSV. Pure presentation — the
ranking/sensitivity calculation lives in optimization_analysis.
"""

import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from python.configuration.app_config_manager import AppConfigManager
from python.framework.optimization.optimization_analysis import rank, sensitivity
from python.framework.reporting.io.run_results_ledger import RunResultsLedger
from python.framework.types.api.report_types import RunResultRow

# Where the human-facing ranked CSV lands (run output, not the ledger dir).
SWEEP_REPORT_DIR = Path('logs/sweeps')


def render_sweep_report(
    sweep_id: str,
    objective: str = 'expectancy',
    maximize: bool = True,
    objective_currency: Optional[str] = None,
    top_n: int = 10,
) -> None:
    """
    Print a sweep's ranked combinations + parameter sensitivity and write the ranked CSV.

    Args:
        sweep_id: The sweep to report on
        objective: Ledger KPI field to rank by
        maximize: Rank direction (False e.g. for max_drawdown)
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

    error_rows = [r for r in rows if r.status == 'error']
    direction = 'maximize' if maximize else 'minimize'
    print(f"Objective: {objective} ({direction})"
          + (f" | currency: {objective_currency}" if objective_currency else ''))
    print(f"Combinations: {len(rows)} ({len(rows) - len(error_rows)} ok, {len(error_rows)} errored)")

    ranked = rank(rows, objective, maximize, objective_currency)
    _print_ranking(ranked, objective, top_n)
    _print_sensitivity(rows, objective, objective_currency)
    _print_errors(error_rows)

    csv_path = _write_csv(ranked, sweep_id)
    print(f"\n📄 Ranked table → {csv_path}")
    print('=' * 80 + '\n')


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
