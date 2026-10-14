"""
One-time migration for the rename of 2026-10-09 (#582): a run header's `origin.person` becomes
`origin.principal`.

Single-use, like every migration in this tree: it rewrites what is on disk to the name the code reads
now, and is not a code path anything imports.

`person` named on whose behalf a run was started — `operator` at the console, otherwise the account
an API token acts for. In the API's account vocabulary `person` is a KIND of account, beside
`service`, so a run a service started would have read as though a person had asked for it. The
identity is the run's PRINCIPAL, the word the API's account rules already use for the console
operator.

What it rewrites: every `header.json` under the configured run roots — the key in the TEXT, so a file
keeps its formatting; each result is parsed back and checked before it is written. Then the run index
is rebuilt from what is on disk. Nothing else stores the field: the ledger, the release certificates
and the test fixtures carry no origin.

Usage:
    python python/experiments/migrate_run_origin_principal/migrate_run_origin_principal.py --preview
    python python/experiments/migrate_run_origin_principal/migrate_run_origin_principal.py
"""

import argparse
import json
from pathlib import Path
from typing import List, Optional

from python.configuration.app_config_manager import AppConfigManager
from python.framework.config_directory.config_directory import declared_run_purposes
from python.framework.config_directory.config_directory_discovery import json_files_under
from python.framework.reporting.io.run_header_io import RUN_HEADER_ARTIFACT
from python.framework.reporting.store.run_index import RunIndex

_OLD_KEY = '"person": '
_NEW_KEY = '"principal": '


def _headers(roots: List[Path]) -> List[Path]:
    """
    Every run header under the run roots.

    Args:
        roots: The configured run roots

    Returns:
        The header files
    """
    return [path for root in roots for path in json_files_under(Path(root))
            if path.name == RUN_HEADER_ARTIFACT]


def _migrated(path: Path, text: str) -> Optional[str]:
    """
    A header's text with its origin's `person` renamed, checked by parsing it back.

    Args:
        path: The header, named in a refusal
        text: Its text

    Returns:
        The new text, or None when there is nothing to rename
    """
    origin = json.loads(text).get('origin')
    if not isinstance(origin, dict) or 'person' not in origin:
        return None
    if text.count(_OLD_KEY) != 1:
        raise ValueError(f'{path}: "person" occurs {text.count(_OLD_KEY)} times — expected once')
    new = text.replace(_OLD_KEY, _NEW_KEY)
    renamed = json.loads(new)['origin']
    if 'person' in renamed or renamed.get('principal') != origin['person']:
        raise ValueError(f'{path}: the rename did not land on the origin block')
    return new


def main() -> int:
    """
    Rename the key in every stored header, then rebuild the run index.

    Returns:
        Process exit code
    """
    parser = argparse.ArgumentParser(description='Rename origin.person to origin.principal')
    parser.add_argument('--preview', action='store_true', help='Count, change nothing')
    args = parser.parse_args()

    file_logging = AppConfigManager().get_file_logging_config_object()
    run_logs = file_logging.run_logs
    headers = _headers([run_logs.simulation, run_logs.autotrader])
    rewritten = 0
    for path in headers:
        new = _migrated(path, path.read_text(encoding='utf-8'))
        if new is None:
            continue
        rewritten += 1
        if not args.preview:
            path.write_text(new, encoding='utf-8')
    verb = 'to rewrite' if args.preview else 'rewritten'
    print(f'{len(headers)} header(s) found, {rewritten} {verb}')
    if args.preview or not rewritten:
        return 0
    count = RunIndex(file_logging.run_index, run_logs,
                     declared_purposes=declared_run_purposes).rebuild()
    print(f'run index rebuilt: {count} run(s)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
