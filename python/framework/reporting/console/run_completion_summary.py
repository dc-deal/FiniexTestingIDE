"""
FiniexTestingIDE - Run Completion Console Block

Prints the runs that started and never reached their close, so an abnormal end is visible
where somebody already looks instead of being discovered from a hole in a later report.
"""

from typing import Dict, List

from python.framework.types.api.report_types import RunInfo

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
            parent = f'  parent={run.parent_id}' if run.parent_id else ''
            print(f'{indent}     {run.run_id}  {run.start_time}  {run.name}{parent}')
