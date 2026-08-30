"""
FiniexTestingIDE - One-time migration: write headers for runs that predate them (#475)

A run is now identified by its `header.json`, and the derived index is built from those headers.
Every run that existed before that change has none — so it is absent from the index, absent from
`GET /api/v1/reports/runs`, and unresolvable by every report route. Measured before writing this:
117 of 128 run directories.

This is the §27 case, and only that case: persistent data with an outdated shape, migrated ONCE
by a script that is not a code path. There is deliberately no compatibility layer in the framework
— a run either has a header or it does not.

What it does NOT do: rename directories. Old ids keep their `<date>_<time>` form, new runs get
`<date>_<time>_<hash>`. Both sort correctly beside each other, so nothing reorders, and no link
that names an old id stops working.

    python python/experiments/migrate_run_headers/migrate_run_headers.py [--dry-run]
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from python.configuration.app_config_manager import AppConfigManager
from python.framework.reporting.io.run_header_io import RUN_HEADER_ARTIFACT, write_run_header
from python.framework.reporting.store.run_index import RunIndex
from python.framework.types.api.report_types import RunHeader
from python.framework.types.log_layout_types import (
    AUTOTRADER_GROUP,
    SINGLE_RUNS_GROUP,
    SWEEPS_GROUP,
)

# The shape the old directory names carry. Parsing an id is exactly what the header exists to
# END — it is done here, once, because this is the only moment the information lives nowhere else.
_LEGACY_STAMP = '%Y%m%d_%H%M%S'


def _start_time(run_id: str, run_dir: Path) -> datetime:
    """
    The run's start, from its legacy id — falling back to the directory's own mtime.

    Args:
        run_id: The legacy directory name
        run_dir: The run directory, for the fallback

    Returns:
        A tz-aware UTC datetime
    """
    try:
        return datetime.strptime(run_id[:15], _LEGACY_STAMP).replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.fromtimestamp(run_dir.stat().st_mtime, tz=timezone.utc)


def _snapshot_name(run_dir: Path) -> str:
    """The config snapshot this run carries, if it carries one."""
    for candidate in ('scenario_config.json', 'autotrader_config.json'):
        if (run_dir / candidate).exists():
            return candidate
    return ''


def _parent_id(run_type: str, run_dir: Path) -> Optional[str]:
    """A sweep combination names the sweep it belongs to; everything else stands alone."""
    return run_dir.parent.parent.name if run_type == SWEEPS_GROUP else None


def migrate(dry_run: bool) -> int:
    """
    Write a header for every run directory that lacks one, then rebuild the index.

    Args:
        dry_run: Report what would happen and change nothing

    Returns:
        Process exit code
    """
    file_logging = AppConfigManager().get_file_logging_config_object()
    roots = file_logging.run_logs

    written = skipped = 0
    for run_type, root in ((AUTOTRADER_GROUP, roots.autotrader),
                           (SINGLE_RUNS_GROUP, roots.single_runs),
                           (SWEEPS_GROUP, roots.sweeps)):
        pattern = '*/*/*' if run_type == SWEEPS_GROUP else '*/*'
        for run_dir in sorted(Path(root).glob(pattern)):
            if not run_dir.is_dir():
                continue
            if (run_dir / RUN_HEADER_ARTIFACT).exists():
                skipped += 1
                continue
            header = RunHeader(
                run_id=run_dir.name,
                start_time=_start_time(run_dir.name, run_dir),
                run_type=run_type,
                run_name=run_dir.parent.name,
                parent_id=_parent_id(run_type, run_dir),
                config_snapshot=_snapshot_name(run_dir),
                # Unknowable after the fact, and left empty rather than guessed: a header that
                # invents its provenance is worse than one that admits it does not have it.
                app_version='',
                git_commit=None,
            )
            print(f'  {"would write" if dry_run else "wrote"}  {run_dir}')
            if not dry_run:
                write_run_header(header, run_dir)
            written += 1

    print(f'\n{"Would write" if dry_run else "Wrote"} {written} header(s); '
          f'{skipped} already had one.')
    if not dry_run:
        count = RunIndex(file_logging.run_index).rebuild(roots)
        print(f'Index rebuilt: {count} run(s).')
    return 0


def main() -> int:
    """
    Parse arguments and run the migration.

    Returns:
        Process exit code
    """
    parser = argparse.ArgumentParser(description='Write run headers for runs that predate them')
    parser.add_argument('--dry-run', action='store_true',
                        help='Report what would happen and change nothing')
    return migrate(parser.parse_args().dry_run)


if __name__ == '__main__':
    sys.exit(main())
