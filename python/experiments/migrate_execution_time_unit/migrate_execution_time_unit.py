"""
One-time migration for the scenario execution time's unit (2026-09-29).

Single-use, like every migration in this tree. `execution_time_ms` was recorded in SECONDS under
its millisecond name until the process measured it on the monotonic clock in milliseconds. This
rewrites the stored `scenario_details.json` artifacts of the runs that STARTED before that fix,
multiplying the value by 1000 — and only those, because a multiplication applied twice is not
undone by anything. A file is rewritten only when it still carries EVIDENCE of seconds: a unit with
ticks whose value is below one microsecond per tick, which no Python tick loop reaches. After the
first run no file carries that evidence, so a second run changes nothing. Each file is rewritten in
the layout the report writer uses (two-space JSON, UTF-8 as is).

Usage:
    python python/experiments/migrate_execution_time_unit/migrate_execution_time_unit.py --preview
    python python/experiments/migrate_execution_time_unit/migrate_execution_time_unit.py
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from python.configuration.app_config_manager import AppConfigManager

# Every run that started before this instant recorded seconds. Nothing ran with the fix earlier.
_FIX_INSTANT = datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc)

# Below this many milliseconds per tick a value can only be seconds (one microsecond per tick).
_SECONDS_EVIDENCE_MS_PER_TICK = 0.001


def _started_before_fix(run_dir: Path) -> bool:
    """
    Whether this run began before the unit was fixed.

    Args:
        run_dir: The run's directory, holding its header.json

    Returns:
        True when the header's start time precedes the fix
    """
    header = json.loads((run_dir / 'header.json').read_text(encoding='utf-8'))
    started = datetime.fromisoformat(header['start_time'])
    return started < _FIX_INSTANT


def _is_in_seconds(unit: dict) -> bool:
    """
    Whether one unit's value can only be seconds.

    Args:
        unit: One unit row of a scenario_details artifact

    Returns:
        True for a unit with ticks and a value below one microsecond per tick
    """
    ticks = unit.get('ticks_processed') or 0
    value = unit.get('execution_time_ms') or 0.0
    return ticks > 0 and value > 0 and value / ticks < _SECONDS_EVIDENCE_MS_PER_TICK


def main() -> None:
    """Parse arguments and migrate."""
    parser = argparse.ArgumentParser(description='One-time execution time unit migration')
    parser.add_argument('--preview', action='store_true', help='List and write nothing')
    args = parser.parse_args()

    runs_root = Path(AppConfigManager().get_file_logging_config_object().run_index).parent
    changed = 0
    for artifact in sorted(runs_root.rglob('io/scenario_details.json')):
        run_dir = artifact.parent.parent
        if not _started_before_fix(run_dir):
            continue
        report = json.loads(artifact.read_text(encoding='utf-8'))
        units = report.get('units') or []
        if not any(_is_in_seconds(unit) for unit in units):
            continue
        for unit in units:
            unit['execution_time_ms'] = (unit.get('execution_time_ms') or 0.0) * 1000.0
        changed += 1
        print(f'  {"would rewrite" if args.preview else "rewrote"} {artifact}')
        if not args.preview:
            artifact.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'\n  {changed} artifact(s)')


if __name__ == '__main__':
    main()
