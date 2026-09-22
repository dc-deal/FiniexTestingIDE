"""
FiniexTestingIDE - Run Completion Console Block

Prints the runs that started and never reached their close, so an abnormal end is visible
where somebody already looks instead of being discovered from a hole in a later report.
"""

from typing import Dict, List

from python.framework.types.api.report_types import RunInfo, RunResultRow

# A run whose group is not one of these still prints — the label just falls back to the raw
# group name, because an unknown group is a reason to show MORE rather than to hide one.
_GROUP_LABEL = {
    'live': 'live session — traded, and left no record of what it did',
    'simulation': 'backtest — a batch that did not finish',
}


def render_unfinished_runs(grouped: Dict[str, List[RunInfo]], indent: str = '  ') -> None:
    """
    Print the unfinished runs, or one line confirming there are none.

    The clean case prints too: an absent block cannot be told apart from a check that never
    ran, and this one is read before a thirty-day deployment.

    Args:
        grouped: Group name -> its unfinished runs, as `unfinished_by_group` returns them
        indent: Left padding for every line, so the block nests under a caller's heading
    """
    total = sum(len(runs) for runs in grouped.values())
    if not total:
        print(f'{indent}✅ Every registered run reached its close — no missing ledger row.')
        return

    print(f'{indent}⚠️  {total} run(s) started and never completed — no ledger row was written.')
    print(f'{indent}   A run registers at START; its ledger row is the LAST step at close. So')
    print(f'{indent}   these ended abnormally — or one of them is running right now.')
    for group in sorted(grouped):
        print(f'{indent}   {_GROUP_LABEL.get(group, group)}')
        for run in grouped[group]:
            # The kind is printed WITH the id: both kinds are a timestamp plus a hash, so the
            # id alone leaves the reader guessing whether they are looking at a sweep or a
            # live deployment (#386).
            _kind = f'{run.parent_kind}=' if run.parent_kind else 'parent='
            parent = f'  {_kind}{run.parent_id}' if run.parent_id else ''
            print(f'{indent}     {run.run_id}  {run.start_time}  {run.name}{parent}')


def render_missing_records(rows: List[RunResultRow], indent: str = '  ') -> None:
    """
    Print the ledger rows whose records vanished without a prune having said so.

    The other half of the two-store audit. Silent in the clean case, deliberately unlike the
    block above: "every run reached its close" is a fact worth confirming before a thirty-day
    deployment, while "nobody deleted anything behind our back" is the ordinary state and a line
    that fires on every run stops being read.

    Args:
        rows: The rows `rows_with_missing_records` returned
        indent: Left padding, so the block nests under a caller's heading
    """
    if not rows:
        return
    runs = {row.run_id for row in rows}
    print(f'{indent}⚠️  {len(rows)} ledger row(s) across {len(runs)} run(s) can no longer be '
          f'checked:')
    print(f'{indent}   their run directory is gone and no prune recorded removing it. The '
          f'figures stand,')
    print(f'{indent}   the records behind them do not — so nothing can re-derive them (§48).')
    for run_id in sorted(runs):
        row = next(r for r in rows if r.run_id == run_id)
        print(f'{indent}     {run_id}  {row.run_timestamp}  {row.scenario_set_name}')
    print(f'{indent}   A deliberate prune stamps `records_pruned_at` instead; these were not '
          f'stamped.')
