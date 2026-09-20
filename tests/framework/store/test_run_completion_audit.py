"""
Which runs started and never reached their close.

A run registers in the run index from its header, written before anything can fail, while its
ledger row is the last step at close. A process killed between the two exists in one store and
not the other — measured 2026-09-18 on a live session stopped from the debugger, and again on
a deliberately SIGKILLed mock session.

The derivation must stay ONE-DIRECTIONAL: the ledger predates the run index, so it legitimately
holds rows for runs the index never saw, and reporting that direction would flood the answer
with normal history.
"""

from python.framework.reporting.store.run_completion_audit import (
    unfinished_by_group,
    unfinished_runs,
)
from python.framework.types.api.report_types import RunInfo, RunResultRow


def _run(run_id: str, group: str = 'live', start: str = '2026-09-18T10:00:00+00:00',
         parent: str = None) -> RunInfo:
    """
    A run index entry.

    Args:
        run_id: The run's id
        group: 'live' or 'simulation'
        start: Start stamp, which is also the sort key
        parent: Deployment or sweep this run belongs to

    Returns:
        The entry
    """
    return RunInfo(run_id=run_id, group=group, name='profile', start_time=start,
                   parent_id=parent)


def _row(run_id: str) -> RunResultRow:
    """
    A ledger row for a run that reached its close.

    Args:
        run_id: The run's id

    Returns:
        The row
    """
    return RunResultRow(run_id=run_id, param_hash='h', run_timestamp='2026-09-18T10:00:00+00:00')


class TestTheUnfinishedAreTheDifference:

    def test_a_run_with_no_ledger_row_is_unfinished(self):
        runs = [_run('a'), _run('b')]
        assert [r.run_id for r in unfinished_runs(runs, [_row('a')])] == ['b']

    def test_every_run_accounted_for_yields_nothing(self):
        runs = [_run('a'), _run('b')]
        assert unfinished_runs(runs, [_row('a'), _row('b')]) == []

    def test_a_ledger_row_without_an_index_entry_is_not_reported(self):
        # The ledger predates the run index, so this direction is normal and numerous. A
        # two-directional check would bury the one finding under ordinary history.
        assert unfinished_runs([_run('a')], [_row('a'), _row('older')]) == []

    def test_the_oldest_comes_first(self):
        runs = [_run('late', start='2026-09-18T12:00:00+00:00'),
                _run('early', start='2026-09-18T08:00:00+00:00')]
        assert [r.run_id for r in unfinished_runs(runs, [])] == ['early', 'late']


class TestScopingToOneParent:

    def test_only_that_deployments_runs_are_returned(self):
        runs = [_run('mine', parent='deploy_1'), _run('theirs', parent='deploy_2'),
                _run('orphan')]
        found = unfinished_runs(runs, [], parent_id='deploy_1')
        assert [r.run_id for r in found] == ['mine']

    def test_without_a_parent_every_unfinished_run_is_returned(self):
        runs = [_run('mine', parent='deploy_1'), _run('orphan')]
        assert len(unfinished_runs(runs, [])) == 2


class TestGroupingSeparatesTheConsequence:

    def test_two_runs_of_one_group_both_survive(self):
        # A dict comprehension keyed on the group keeps only the last entry — the defect this
        # pins, caught in review before it shipped.
        runs = [_run('a', group='live'), _run('b', group='live'),
                _run('c', group='simulation')]
        grouped = unfinished_by_group(runs, [])
        assert [r.run_id for r in grouped['live']] == ['a', 'b']
        assert [r.run_id for r in grouped['simulation']] == ['c']

    def test_a_group_with_nothing_unfinished_is_omitted(self):
        grouped = unfinished_by_group([_run('a', group='live')], [_row('a')])
        assert grouped == {}
