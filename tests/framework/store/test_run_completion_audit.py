"""
Which runs started and never reached their close.

A run registers in the run index from its header, written before anything can fail, while its
ledger row is the last step at close. A process killed between the two exists in one store and
not the other — measured 2026-09-18 on a live session stopped from the debugger, and again on
a deliberately SIGKILLed mock session.

The plain reverse direction must stay OUT: the ledger predates the run index, so it
legitimately holds rows for runs the index never saw, and reporting that would flood the answer
with normal history. The second question here is a narrower one — a row the index DOES know
whose directory is gone, with no prune having said so — and these tests pin exactly where that
line runs.
"""

from python.framework.reporting.store.run_completion_audit import (
    rows_with_missing_records,
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


def _booked(run_id: str, pruned_at: str = '', currency: str = 'USD',
            stamp: str = '2026-09-18T10:00:00+00:00') -> RunResultRow:
    """
    A ledger row that may or may not still have its records.

    Args:
        run_id: The run's id
        pruned_at: When a prune removed its directory; empty when none did
        currency: The row's account currency — a run books one row per currency
        stamp: The run timestamp, which is also the sort key

    Returns:
        The row
    """
    return RunResultRow(run_id=run_id, param_hash='h', run_timestamp=stamp,
                        currency=currency, records_pruned_at=pruned_at,
                        scenario_set_name='set')


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


class TestABookingCanOutliveItsRecords:
    """
    The other direction, and it is deliberately not the mirror image.

    A prune STAMPS the rows it orphans, so a deliberate deletion is accounted for. What this
    reports is the absence nobody recorded — a directory removed by hand, a restored backup, a
    half-finished move — because that is the case where a figure quietly stops being checkable.
    """

    @staticmethod
    def _gone(_run_dir: str) -> bool:
        """Every directory is missing."""
        return False

    @staticmethod
    def _present(_run_dir: str) -> bool:
        """Every directory is there."""
        return True

    def test_an_unrecorded_absence_is_reported(self):
        rows = rows_with_missing_records([_booked('a')], {'a': '/runs/a'}, exists=self._gone)
        assert [r.run_id for r in rows] == ['a']

    def test_a_directory_that_is_there_reports_nothing(self):
        assert rows_with_missing_records([_booked('a')], {'a': '/runs/a'}, exists=self._present) == []

    def test_a_pruned_row_is_accounted_for_and_stays_silent(self):
        # The stamp IS the account. Reporting it would fire on every ordinary prune, and a
        # warning that fires on the normal case is a warning nobody reads.
        rows = rows_with_missing_records(
            [_booked('a', pruned_at='2026-09-21T18:00:00+00:00')], {'a': '/runs/a'}, exists=self._gone)
        assert rows == []

    def test_a_row_the_index_never_knew_is_not_reported(self):
        # The ledger predates the run index by hundreds of rows. Without this the answer would
        # be history rather than a finding.
        rows = rows_with_missing_records([_booked('older')], {'a': '/runs/a'}, exists=self._gone)
        assert rows == []

    def test_every_currency_row_is_returned_not_one_per_run(self):
        # A run books one row per account currency. Collapsing them here would repeat the quiet
        # loss already measured in the deployment history, where the first row per run wins.
        rows = rows_with_missing_records(
            [_booked('a', currency='USD'), _booked('a', currency='JPY')],
            {'a': '/runs/a'}, exists=self._gone)
        assert sorted(r.currency for r in rows) == ['JPY', 'USD']

    def test_the_oldest_run_comes_first(self):
        rows = rows_with_missing_records(
            [_booked('late', stamp='2026-09-18T12:00:00+00:00'),
             _booked('early', stamp='2026-09-18T08:00:00+00:00')],
            {'late': '/runs/late', 'early': '/runs/early'}, exists=self._gone)
        assert [r.run_id for r in rows] == ['early', 'late']
