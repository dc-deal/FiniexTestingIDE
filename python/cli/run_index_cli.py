"""
FiniexTestingIDE - Run Index CLI
Command-line tools for the derived run index

Usage:
    python python/cli/run_index_cli.py rebuild
    python python/cli/run_index_cli.py status
    python python/cli/run_index_cli.py prune [--orphans] [--keep-last N] [--apply]
"""

import argparse
import sys
from typing import List

from python.configuration.app_config_manager import AppConfigManager
from python.framework.reporting.store.run_index import RunIndex
from python.framework.reporting.store.run_tree_pruner import RunTreePruner
from python.framework.types.run_prune_types import PruneCandidate, PruneSelectors


class RunIndexCli:
    """Command-line interface for the run index."""

    def __init__(self):
        """Initialize CLI with paths from AppConfigManager."""
        self._file_logging = AppConfigManager().get_file_logging_config_object()
        self._index = RunIndex(self._file_logging.run_index, self._file_logging.run_logs)

    def cmd_rebuild(self) -> int:
        """
        Rebuild the index from the run headers on disk.

        Returns:
            Process exit code
        """
        print('\n' + '=' * 80)
        print('🔄 Rebuilding Run Index')
        print('=' * 80 + '\n')
        count = self._index.rebuild()
        print(f'✅ {count} run(s) indexed → {self._file_logging.run_index}')
        duplicates = self._index.duplicate_ids()
        if duplicates:
            print(f'\n⚠️  {len(duplicates)} id(s) appear more than once. A minted id cannot '
                  f'collide, so a duplicate means two directories carry the same header — a '
                  f'copy, or a hand-edited one. Every report route resolves the FIRST:')
            for run_id in duplicates:
                print(f'      {run_id}')
            print('    Remove one of each pair to clear it.')
        print()
        return 0

    def cmd_status(self) -> int:
        """
        Show what the index currently holds.

        Returns:
            Process exit code
        """
        runs = self._index.list_runs()
        print('\n' + '=' * 80)
        print(f'📇 Run Index — {len(runs)} run(s) · {self._file_logging.run_index}')
        print('=' * 80 + '\n')
        for run in runs[:20]:
            reports = f'{len(run.artifacts):>2} artifact(s)' if run.artifacts else '  logs only  '
            print(f'  {run.run_id}  {run.group:<10}  {reports}  {run.name}')
        if len(runs) > 20:
            print(f'  … and {len(runs) - 20} more')
        print()
        return 0

    def cmd_prune(self, orphans: bool, keep_last: int, apply: bool) -> int:
        """
        Remove what the run tree no longer needs — showing it first, deleting only on request.

        Args:
            orphans: Also remove directories that are not runs
            keep_last: Keep only the N newest complete runs per family (0 = selector off)
            apply: Actually delete; without it nothing is touched

        Returns:
            Process exit code
        """
        selectors = PruneSelectors(
            keep_last=keep_last if keep_last > 0 else None, orphans=orphans)
        pruner = RunTreePruner()
        report = pruner.plan(selectors)

        mode = 'APPLY' if apply else 'DRY RUN (nothing deleted; add --apply)'
        print('\n' + '=' * 80)
        print(f'🧹 Prune Run Tree — {mode}')
        print('=' * 80 + '\n')

        self._print_group('DELETE', report.to_delete_orphans,
                          'not runs (no header, not indexed)')
        self._print_group('DELETE', report.to_delete_redundant,
                          f'older than the {keep_last} newest of their family')
        self._print_group('DELETE', report.to_delete_uncommissioned,
                          'reporting=none, produced nothing')
        self._print_group('DELETE', report.emptied_sweep_dirs,
                          'sweep directories left without a single combination')
        self._print_group('KEEP', report.kept_incomplete,
                          'reporting=expected, no artifacts — crashed or still running',
                          names=False)
        self._print_group('KEEP', report.kept_field_study,
                          'hold field_study.jsonl (evidence behind a release gate)', names=False)
        self._print_group('KEEP', report.kept_complete, 'complete', names=False)
        self._print_group('SKIP', report.skipped_sweep_dirs,
                          'sweep directories — not runs, deliberately header-less', names=False)
        self._print_group('STALE', report.stale_rows,
                          'index rows whose directory is gone — the rebuild drops them')

        print(f'\n  The run-results ledger is untouched: {report.ledger_rows} fragment(s) remain, '
              f'including those of the runs above.\n')

        if not apply:
            return 0

        result = pruner.apply(report)
        print(f'  🗑️  {len(result.deleted)} director(ies) removed')
        for failure in result.failed:
            print(f'  ❌ {failure}')
        print(f'  📇 Index rebuilt — {result.indexed_after_rebuild} run(s)')
        for run_id in result.duplicate_ids:
            print(f'  ⚠️  duplicate id: {run_id}')
        print()
        return 1 if result.failed else 0

    @staticmethod
    def _print_group(verb: str, candidates: List[PruneCandidate], reason: str,
                     names: bool = True) -> None:
        """
        Render one classification group.

        Args:
            verb: DELETE / KEEP / SKIP
            candidates: The group's entries
            reason: Why they are in this group
            names: Whether to list the entries; a kept group is a count, not a list
        """
        if not candidates:
            return
        size = sum(c.size_bytes for c in candidates) / 1_048_576
        print(f'  {verb:<8} {len(candidates):>4} · {reason}   {size:.1f} MB')
        if not names:
            return
        for candidate in candidates[:5]:
            label = f'{candidate.run_id}  {candidate.run_type:<10}  {candidate.run_name}' \
                if candidate.run_id else str(candidate.path)
            print(f'             {label}')
        if len(candidates) > 5:
            print(f'             … {len(candidates) - 5} more')


def main() -> int:
    """
    Parse arguments and dispatch.

    Returns:
        Process exit code
    """
    parser = argparse.ArgumentParser(
        description='Run Index CLI (the derived index the API reads)')
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    subparsers.add_parser(
        'rebuild', help='Rebuild the index from the run headers on disk')
    subparsers.add_parser('status', help='Show what the index holds')

    prune_parser = subparsers.add_parser(
        'prune', help='Remove what the run tree no longer needs (shows first, deletes on --apply)')
    prune_parser.add_argument(
        '--orphans', action='store_true', default=False,
        help='Also remove directories that are not runs (no header, not indexed)')
    prune_parser.add_argument(
        '--keep-last', type=int, default=0, metavar='N',
        help='Keep only the N newest complete runs per scenario set / profile / sweep')
    prune_parser.add_argument(
        '--apply', action='store_true', default=False,
        help='Actually delete. Without it nothing is touched — a run directory is the only '
             'copy of its logs')

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    cli = RunIndexCli()
    if args.command == 'prune':
        return cli.cmd_prune(args.orphans, args.keep_last, args.apply)
    return {'rebuild': cli.cmd_rebuild, 'status': cli.cmd_status}[args.command]()


if __name__ == '__main__':
    sys.exit(main())
