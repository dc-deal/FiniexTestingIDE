"""
FiniexTestingIDE - Run Index CLI
Command-line tools for the derived run index

Usage:
    python python/cli/run_index_cli.py rebuild
    python python/cli/run_index_cli.py status
    python python/cli/run_index_cli.py prune [--orphans] [--keep-last N] [--apply]
    python python/cli/run_index_cli.py deployments [--id <deployment>]
"""

import argparse
import sys
import time
from typing import List

from python.configuration.app_config_manager import AppConfigManager
from python.framework.reporting.console.deployment_history_summary import (
    build_deployment_histories,
    deployment_comparability_advisory,
    render_deployment_history,
    render_deployment_list,
    summarize_deployments,
)
from python.framework.reporting.console.run_completion_summary import (
    render_missing_records,
    render_unfinished_runs,
)
from python.framework.reporting.store.run_completion_audit import (
    rows_with_missing_records,
    unfinished_by_group,
)
from python.framework.reporting.store.run_index import RunIndex
from python.framework.reporting.store.run_results_ledger import RunResultsLedger
from python.framework.reporting.store.run_tree_pruner import RunTreePruner
from python.framework.types.api.report_types import ParentKind
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
        ledger = RunResultsLedger(AppConfigManager().get_run_ledger_path())
        ledger_rows = ledger.read_rows()
        render_unfinished_runs(unfinished_by_group(runs, ledger_rows))
        # The opposite direction: a booking whose run directory is gone with no prune behind it.
        # Silent unless it finds something — here the clean case IS the ordinary one.
        run_ids = [run.run_id for run in runs]
        render_missing_records(rows_with_missing_records(
            ledger_rows, dict(zip(run_ids, self._index.run_dirs_of(run_ids)))))
        print()
        return 0

    def cmd_deployments(self, deployment: str) -> int:
        """
        Show a live bot's sessions as one history (#497).

        The ledger's only other reader filters on `sweep_id`, which a live session does not
        have — so before this command a session's row was written and unreachable (§44: a
        store with no read path). This is that path.

        Args:
            deployment: Show only this deployment, or '' for all of them

        Returns:
            Process exit code — 0 even when nothing is grouped, because "no deployment has
            been declared yet" is a state, not a failure
        """
        ledger = RunResultsLedger(AppConfigManager().get_run_ledger_path())
        rows = ledger.read_rows()
        histories = build_deployment_histories(rows)
        if deployment:
            histories = {k: v for k, v in histories.items() if k == deployment}

        if not histories:
            print('No deployment found in the ledger.')
            print('A session joins one only when its profile declares '
                  '`deployment.continuous: true` — until then every start stands alone.')
            return 0

        advisories = {
            name: deployment_comparability_advisory(
                [r for r in rows if r.deployment_id == name])
            for name in histories
        }

        # Two halves, and the split is the same one a sweep already has: the LIST answers
        # "which one do I open", the DETAIL answers "what happened inside it". Printing every
        # deployment's full table was neither — it buried the overview in the detail.
        if not deployment:
            render_deployment_list(summarize_deployments(histories, advisories))
            return 0

        # The sessions ABOVE come from the ledger, so a session that never reached its close
        # is missing from that table by construction — which is exactly the shape a hard kill
        # leaves. The run index registered it at start, so it is recoverable from there.
        runs = self._index.list_runs()
        for name in sorted(histories):
            # Resolved BEFORE rendering: the table's own heading counts the sessions it can
            # show, so it has to be told how many it cannot.
            # DEPLOYMENT explicitly: this table is built from deployment ids, and a sweep
            # id is the same shape, so the kind is what makes the filter exact (#386).
            missing = unfinished_by_group(
                runs, rows, parent_id=name, parent_kind=ParentKind.DEPLOYMENT)
            render_deployment_history(
                name, histories[name], advisories[name],
                unfinished=sum(len(group) for group in missing.values()))
            if missing:
                render_unfinished_runs(missing, indent='')
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

        # PREVIEW, not 'dry run': in this project `dry_run` names exactly one thing — a
        # session that places no real orders — and reusing the words for 'shows what it
        # would delete' puts an arming term on a housekeeping command.
        mode = 'APPLY' if apply else 'PREVIEW (nothing deleted; add --apply)'
        print('\n' + '=' * 80)
        print(f'🧹 Prune Run Tree — {mode}')
        print('=' * 80 + '\n')
        print('  Reading the run tree — every step here is a filesystem call, which costs')
        print('  65-616x on this mount, so give it a moment.')

        if not pruner.size_figures_available():
            print('  ⚠ The run index predates the size column — every MB below reads 0.0.')
            print('    Run `python python/cli/store_cli.py rebuild runs` once to fill it in.')

        started = time.monotonic()
        report = pruner.plan(selectors, progress=self._show_progress)
        self._clear_progress()
        print(f'  Plan ready in {time.monotonic() - started:.1f} s.\n')

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

        print(f'\n  The run-results ledger KEEPS its rows: {report.ledger_rows} fragment(s) remain, '
              f'including those of the runs above.')
        print('  What changes is what they claim — a row whose run directory goes is stamped '
              '`records_pruned_at`,')
        print('  so it stops implying its figures can still be checked against the records '
              'behind them.\n')

        if not apply:
            print(f'  Total {time.monotonic() - started:.1f} s.\n')
            return 0

        print('  Deleting, then rebuilding the index — this walks the tree once more.')
        result = pruner.apply(report)
        print(f'  🗑️  {len(result.deleted)} director(ies) removed')
        for failure in result.failed:
            print(f'  ❌ {failure}')
        print(f'  📇 Index rebuilt — {result.indexed_after_rebuild} run(s)')
        if result.ledger_rows_marked:
            print(f'  📕 {result.ledger_rows_marked} ledger fragment(s) stamped — their records '
                  f'are gone, their figures stay')
        for run_id in result.duplicate_ids:
            print(f'  ⚠️  duplicate id: {run_id}')
        print(f'  Total {time.monotonic() - started:.1f} s.\n')
        return 1 if result.failed else 0

    @staticmethod
    def _show_progress(done: int, total: int) -> None:
        """
        Overwrite one line with how far the classification has come.

        A bar rather than a stream of lines, because the interesting output is the report
        that follows and a scrolled-away progress log would push it off the screen.

        Args:
            done: Runs classified so far
            total: Runs to classify
        """
        # Only where a carriage return actually overwrites. Piped or redirected, the bar
        # becomes one enormous line and buries the report it was meant to introduce. This
        # asks about the OUTPUT DEVICE, which isatty answers reliably — unlike "is a human
        # watching", which it does not and which this project resolves by declaration.
        if not sys.stdout.isatty():
            return
        width = 30
        filled = int(width * done / total) if total else width
        print(f'\r  [{"█" * filled}{"·" * (width - filled)}] {done}/{total} runs',
              end='', flush=True)

    @staticmethod
    def _clear_progress() -> None:
        """Wipe the progress line so the report starts on a clean one."""
        if not sys.stdout.isatty():
            return
        print('\r' + ' ' * 60 + '\r', end='', flush=True)

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

    deployments_parser = subparsers.add_parser(
        'deployments',
        help="Show a live bot's sessions as one history — the ledger's only live read path")
    deployments_parser.add_argument(
        '--id', default='', metavar='DEPLOYMENT',
        help='Show only this deployment (default: all)')

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    cli = RunIndexCli()
    if args.command == 'prune':
        return cli.cmd_prune(args.orphans, args.keep_last, args.apply)
    if args.command == 'deployments':
        return cli.cmd_deployments(args.id)
    return {'rebuild': cli.cmd_rebuild, 'status': cli.cmd_status}[args.command]()


if __name__ == '__main__':
    sys.exit(main())
