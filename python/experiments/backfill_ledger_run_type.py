"""
Fill `run_type` into ledger fragments written before the column existed (#390).

A single-use migration (§27), not a permanent code path: nothing imports it and it answers a
question that only exists once. Measured 2026-09-18 before it ran: 520 of 616 rows could not
say which pipeline produced them, because the only thing that hinted at it — `input_plane` —
arrived with #518 and answers a different question anyway.

Three sources, in descending order of how directly they know:

    1. the RUN INDEX          — it carries `run_type` per run id, straight from the header
    2. `sweep_id`             — a sweep is a backtest by construction
    3. the NAME               — `scenario_set_name` is the scenario set for a backtest and the
                                PROFILE name for a live session, so the tracked config
                                directories settle it

A row none of the three can place is REMOVED rather than guessed. Those are runs of scenario
sets that no longer exist anywhere — a wrong pipeline label on a row nobody can check is worse
than no row, because every later count would inherit it silently.

Usage:
    python python/experiments/backfill_ledger_run_type.py --dry-run
    python python/experiments/backfill_ledger_run_type.py --apply
"""

import argparse
import json
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

import pandas as pd

from python.configuration.app_config_manager import AppConfigManager
from python.framework.types.log_layout_types import RUN_TYPE_LIVE, RUN_TYPE_SIMULATION


def _text(value) -> str:
    """
    Read a parquet cell as a string, tolerating the NaN a missing column produces.

    Args:
        value: The raw cell

    Returns:
        The string, or '' when it is absent or not a string
    """
    return value if isinstance(value, str) and value else ''


def _known_names() -> Tuple[Set[str], Set[str]]:
    """
    The tracked profile names and scenario-set names, as the ledger would have recorded them.

    Returns:
        (live profile names, simulation scenario-set names)
    """
    root = Path(__file__).resolve().parents[2]
    profiles = {json.loads(p.read_text()).get('name', '')
                for p in (root / 'configs' / 'autotrader_profiles').rglob('*.json')}
    sets = {json.loads(p.read_text()).get('scenario_set_name', p.stem)
            for p in (root / 'configs' / 'scenario_sets').rglob('*.json')}
    profiles.discard('')
    sets.discard('')
    return profiles, sets


def _resolve(row, from_index: Dict[str, str],
             profiles: Set[str], sets: Set[str]) -> Optional[str]:
    """
    Decide which pipeline produced one ledger row.

    Args:
        row: The row as a namedtuple from `itertuples`
        from_index: run id → run type, out of the run index
        profiles: Tracked live profile names
        sets: Tracked scenario-set names

    Returns:
        The run type, or None when no source can place it
    """
    indexed = from_index.get(_text(row.run_id))
    if indexed:
        return indexed
    if _text(row.sweep_id):
        return RUN_TYPE_SIMULATION
    name = _text(row.scenario_set_name)
    if '__sweep_' in name:
        return RUN_TYPE_SIMULATION
    if name in profiles:
        return RUN_TYPE_LIVE
    if name in sets:
        return RUN_TYPE_SIMULATION
    return None


def main() -> int:
    """
    Backfill every fragment, and delete the ones that cannot be placed.

    Returns:
        Process exit code
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true',
                        help='Write. Without it nothing is touched')
    args = parser.parse_args()

    ledger = Path(AppConfigManager().get_run_ledger_path())
    index_path = Path(AppConfigManager().get_file_logging_config_object().run_index)
    index = pd.read_parquet(index_path) if index_path.exists() else pd.DataFrame()
    from_index = {k: v for k, v in zip(index.get('run_id', []), index.get('run_type', []))
                  if isinstance(v, str) and v}
    profiles, sets = _known_names()

    filled, already, removed = 0, 0, []
    for fragment in sorted(ledger.glob('*.parquet')):
        if fragment.name.endswith('_index.parquet'):
            continue
        frame = pd.read_parquet(fragment)
        if 'run_type' in frame.columns and frame['run_type'].map(
                lambda v: isinstance(v, str) and bool(v)).all():
            already += len(frame)
            continue

        resolved = [_resolve(row, from_index, profiles, sets) for row in frame.itertuples()]
        if any(r is None for r in resolved):
            removed.append(fragment)
            if args.apply:
                fragment.unlink()
            continue
        frame['run_type'] = resolved
        filled += len(frame)
        if args.apply:
            frame.to_parquet(fragment, index=False)

    print(f'  already typed : {already} row(s)')
    print(f'  backfilled    : {filled} row(s)')
    print(f'  unplaceable   : {len(removed)} fragment(s) '
          f'{"removed" if args.apply else "would be removed"}')
    for fragment in removed:
        print(f'      {fragment.name}')
    if not args.apply:
        print('\n  Nothing written. Re-run with --apply.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
