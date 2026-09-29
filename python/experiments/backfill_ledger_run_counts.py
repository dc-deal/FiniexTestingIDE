"""
Fill `run_outcome` and the three channel counts into ledger fragments written before the
columns existed (contract 15).

A single-use migration (§27), not a permanent code path: nothing imports it. The values come
from the one place a finished run already recorded them — its own `io/warnings_errors.json` —
and a run without that artifact (pruned, or never reported) keeps None: not recorded, never a
guessed zero.

The artifact's ROWS are counted, with one exception that is the reason the counts moved onto
the outcome in the first place: a backtest summarizes its whole Tier-2 log pot in ONE row, so
its number is read from that row's own text, "N warning(s) in M scenario log(s)", which this
project writes in exactly one place (`warnings_errors_report_builder._batch_warnings`). An
artifact that already carries the counts on its outcome is taken as it stands.

Usage:
    python python/experiments/backfill_ledger_run_counts.py --preview
    python python/experiments/backfill_ledger_run_counts.py
"""

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from python.configuration.app_config_manager import AppConfigManager
from python.framework.reporting.store.run_ledger_index import RunLedgerIndex
from python.framework.reporting.store.run_results_ledger import LEDGER_COLUMNS
from python.framework.types.log_layout_types import IO_SUBDIR, RUN_TYPE_SIMULATION

_COLUMNS = ('run_outcome', 'error_count', 'warning_count', 'log_warning_count')
_POT_SUMMARY = re.compile(r'^(\d+) warning\(s\) in \d+ scenario log\(s\)')


def _counts(report: Dict[str, Any], run_type: str) -> Dict[str, Any]:
    """
    The four values of one stored warnings-errors artifact.

    Args:
        report: The parsed artifact
        run_type: 'simulation' or 'autotrader' — they shape their Tier-2 rows differently

    Returns:
        Column → value
    """
    outcome = report.get('outcome') or {}
    warnings = report.get('warnings') or []
    minor = [w for w in warnings if w.get('tier') == 'minor']
    if outcome.get('log_warning_count') is not None:
        log_warnings = outcome['log_warning_count']
    elif run_type == RUN_TYPE_SIMULATION:
        matches = [_POT_SUMMARY.match(w.get('message', '')) for w in minor]
        log_warnings = sum(int(m.group(1)) for m in matches if m)
    else:
        log_warnings = len(minor)
    return {
        'run_outcome': outcome.get('run_outcome') or None,
        'error_count': outcome.get('error_count') if outcome.get('error_count') is not None else
        sum(len(e.get('logged_errors') or []) for e in report.get('errors') or []),
        'warning_count': outcome.get('warning_count') if outcome.get('warning_count') is not None
        else sum(1 for w in warnings if w.get('tier') == 'major'),
        'log_warning_count': log_warnings,
    }


def _artifact(run_dirs: Dict[str, str], run_id: str) -> Optional[Dict[str, Any]]:
    """
    One run's stored warnings-errors artifact.

    Args:
        run_dirs: run id → run directory, out of the run index
        run_id: The run

    Returns:
        The parsed artifact, or None when the run or the artifact is gone
    """
    run_dir = run_dirs.get(run_id)
    if not run_dir:
        return None
    path = Path(run_dir) / IO_SUBDIR / 'warnings_errors.json'
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def main() -> None:
    """Parse arguments and backfill."""
    parser = argparse.ArgumentParser(description='One-time ledger run-count backfill')
    parser.add_argument('--preview', action='store_true', help='List and write nothing')
    args = parser.parse_args()

    ledger = Path(AppConfigManager().get_run_ledger_path())
    index_path = Path(AppConfigManager().get_file_logging_config_object().run_index)
    index = pd.read_parquet(index_path) if index_path.exists() else pd.DataFrame()
    run_dirs = dict(zip(index.get('run_id', []), index.get('run_dir', [])))
    ledger_index = RunLedgerIndex(ledger, LEDGER_COLUMNS)

    filled, already, missing = 0, 0, 0
    for fragment in ledger_index.fragments():
        frame = pd.read_parquet(fragment)
        if all(c in frame.columns for c in _COLUMNS) and frame['run_outcome'].notna().all():
            already += 1
            continue
        run_id = str(frame['run_id'].iloc[0])
        report = _artifact(run_dirs, run_id)
        if report is None:
            missing += 1
            continue
        run_type = str(frame['run_type'].iloc[0]) if 'run_type' in frame.columns else ''
        for column, value in _counts(report, run_type).items():
            frame[column] = value
        filled += 1
        print(f'  {"would fill" if args.preview else "filled"} {fragment.name}: '
              f'{_counts(report, run_type)}')
        if not args.preview:
            temporary = fragment.with_suffix('.parquet.tmp')
            frame.to_parquet(temporary, index=False)
            temporary.replace(fragment)

    print(f'\n  already carrying the counts : {already} fragment(s)')
    print(f'  {"would fill" if args.preview else "filled"}                  : {filled} fragment(s)')
    print(f'  no artifact, left None      : {missing} fragment(s)')
    if filled and not args.preview:
        print(f'  rebuilt the ledger index ({ledger_index.rebuild()} rows)')


if __name__ == '__main__':
    main()
