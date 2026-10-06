"""
Carry the contract-21 worker-decision shape into the runs recorded before it — exactly, or not at all.

A single-use migration, not a permanent code path: nothing imports it. Contract 21 serves a unit
whose decisions nobody counted with null counters and timings and `worker_decision_tracked: false`,
where a stored artifact still says it decided zero times. Each value comes from the run's own record:

- a unit whose `decision_logic_type` is empty was not tracked — the tracker stamps the type on
  every unit it counts, so an empty type means no tracker ran (or the unit produced no decision
  statistics at all). Its counters and timings become null, its empty type and name become null,
  and `worker_decision_tracked` is false;
- a unit with a type was tracked: `worker_decision_tracked` becomes true and its figures stay;
- a worker row's `compute_ratio_pct` and `ticks_idle` are recomputed from the row's own call count
  and last compute tick and the unit's tick count, the way the builder derives them, and are null
  where they are not defined — no tick processed, or a worker that never computed. A defined value
  that does not reproduce the stored one refuses the run;
- the run-level worker totals have no tick count of their own, so their two cadence fields are null.

NOT back-filled: the logic's type and name on an untracked unit. A new run names them; an old one
would have to reopen its frozen run configuration, and stays null.

Whether a run still needs it is decided on the RAW keys, not by the model: the model fills a
missing `worker_decision_tracked` with its default, so an old tracked unit would read as untracked.

Usage:
    python python/experiments/backfill_worker_decision_untracked.py --preview
    python python/experiments/backfill_worker_decision_untracked.py
"""

import argparse
import json
from pathlib import Path
from typing import Dict, Optional, Tuple

import pandas as pd
from pydantic import ValidationError

from python.configuration.app_config_manager import AppConfigManager
from python.framework.reporting.io.artifact_specs import WORKER_DECISION_ARTIFACT
from python.framework.reporting.io.report_artifact_io import write_artifact
from python.framework.types.api.report_types import WorkerDecisionReport
from python.framework.types.log_layout_types import IO_SUBDIR

# Counted only by a tracker; null on an untracked unit.
_COUNTED = ('decision_count', 'buy_signals', 'sell_signals', 'flat_signals', 'trades_requested',
            'decision_total_time_ms', 'decision_avg_time_ms', 'decision_min_time_ms',
            'decision_max_time_ms')
# A stored float carried through JSON: equal to this, or it is not the same figure.
_TOLERANCE = 1e-6


def _cadence(worker: Dict, ticks: int) -> Tuple[Optional[float], Optional[int]]:
    """
    A worker row's compute ratio and idle distance, derived the way the builder derives them.

    Args:
        worker: The stored worker row
        ticks: The unit's processed ticks

    Returns:
        (compute_ratio_pct, ticks_idle), each None where it is not defined
    """
    last = worker.get('last_compute_tick', -1)
    ratio = (worker.get('call_count', 0) / ticks * 100) if ticks > 0 else None
    idle = (ticks - last) if ticks > 0 and last >= 0 else None
    return ratio, idle


def _carried_over(raw: Dict) -> Tuple[Dict, Optional[str], Dict[str, int]]:
    """
    The stored report in the contract-21 shape.

    Args:
        raw: The stored artifact as parsed JSON

    Returns:
        (the carried-over JSON, the reason the run is refused or None, counts of what changed)
    """
    counts = {'units': 0, 'untracked': 0, 'tracked': 0}
    for unit in raw.get('units', []):
        counts['units'] += 1
        tracked = bool(unit.get('decision_logic_type'))
        unit['worker_decision_tracked'] = tracked
        if tracked:
            counts['tracked'] += 1
        else:
            counts['untracked'] += 1
            for field in _COUNTED:
                unit[field] = None
            for field in ('decision_logic_type', 'decision_logic_name'):
                unit[field] = unit.get(field) or None
        ticks = unit.get('ticks_processed', 0)
        for worker in unit.get('workers', []):
            ratio, idle = _cadence(worker, ticks)
            stored_ratio, stored_idle = worker.get('compute_ratio_pct'), worker.get('ticks_idle')
            if ratio is not None and (stored_ratio is None or abs(ratio - stored_ratio) > _TOLERANCE):
                return raw, f'unit {unit["name"]!r}: compute ratio {stored_ratio} does not reproduce', counts
            if idle is not None and idle != stored_idle:
                return raw, f'unit {unit["name"]!r}: idle distance {stored_idle} does not reproduce', counts
            worker['compute_ratio_pct'], worker['ticks_idle'] = ratio, idle
    for total in raw.get('worker_totals', []):
        total['compute_ratio_pct'], total['ticks_idle'] = None, None
    return raw, None, counts


def _is_current(raw: Dict) -> bool:
    """
    Whether a stored artifact already has the contract-21 shape.

    Args:
        raw: The stored artifact as parsed JSON

    Returns:
        True when every unit row carries `worker_decision_tracked` and the model accepts it
    """
    if any('worker_decision_tracked' not in unit for unit in raw.get('units', [])):
        return False
    try:
        WorkerDecisionReport.model_validate(raw)
    except ValidationError:
        return False
    return True


def main() -> None:
    """Carry every stored worker-decision artifact over, or name why a run was left as it was."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument('--preview', action='store_true', help='report only, write nothing')
    args = parser.parse_args()

    index = pd.read_parquet(Path(AppConfigManager().get_file_logging_config_object().run_index))
    carried, untouched, refused = 0, 0, []
    totals = {'units': 0, 'untracked': 0, 'tracked': 0}
    for run_dir in sorted(index.run_dir):
        io_dir = Path(run_dir) / IO_SUBDIR
        path = io_dir / WORKER_DECISION_ARTIFACT.filename
        if not path.exists():
            continue
        raw = json.loads(path.read_text(encoding='utf-8'))
        if _is_current(raw):
            untouched += 1
            continue
        fixed, reason, counts = _carried_over(raw)
        if reason is None:
            try:
                report = WorkerDecisionReport.model_validate(fixed)
            except ValidationError as e:
                reason = f'the carried-over artifact is still refused: {e.errors()[0]["msg"]}'
        if reason is not None:
            refused.append((str(io_dir.parent), reason))
            continue
        for key in totals:
            totals[key] += counts[key]
        carried += 1
        if not args.preview:
            write_artifact(report, io_dir, WORKER_DECISION_ARTIFACT)

    verb = 'would carry over' if args.preview else 'carried over'
    print(f'{verb}: {carried} run(s), {totals["units"]} unit(s) — '
          f'{totals["untracked"]} untracked (counters now null), {totals["tracked"]} tracked')
    print(f'already in the contract-21 shape: {untouched} run(s)')
    for run, reason in refused:
        print(f'REFUSED {run}: {reason}')


if __name__ == '__main__':
    main()
