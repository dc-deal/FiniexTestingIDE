"""
FiniexTestingIDE - Batch Exit Code Tests
The simulation batch outcome reaches the process exit code (#372).

Built from real ProcessResult / BatchExecutionSummary objects rather than stand-ins: the
classification reads fields whose names a stand-in would silently accept.
"""

from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.process_data_types import LOGGED_ERRORS_TYPE, ProcessResult
from python.framework.types.run_outcome_types import RunOutcome


def _batch(results) -> BatchExecutionSummary:
    """
    Build a summary carrying the given process results.

    Args:
        results: The per-scenario results the batch produced

    Returns:
        The summary under test
    """
    return BatchExecutionSummary(
        batch_execution_time=0.0, batch_warmup_time=0.0, batch_tickrun_time=0.0,
        process_result_list=results)


def _ok(name='s1') -> ProcessResult:
    """
    A scenario that completed.

    Args:
        name: Scenario name

    Returns:
        A successful result
    """
    return ProcessResult(success=True, scenario_name=name, scenario_index=0)


def _crashed(name='s2') -> ProcessResult:
    """
    A scenario whose tick loop raised.

    Args:
        name: Scenario name

    Returns:
        A failed result carrying a real exception type
    """
    return ProcessResult(success=False, scenario_name=name, scenario_index=1,
                         error_type='ValueError', error_message='boom')


def _logged_errors(name='s3') -> ProcessResult:
    """
    A scenario that finished but logged errors (the §35 error pot).

    Args:
        name: Scenario name

    Returns:
        A failed result of the LoggedErrors kind
    """
    return ProcessResult(success=False, scenario_name=name, scenario_index=2,
                         error_type=LOGGED_ERRORS_TYPE,
                         error_message='Scenario logged 2 ERROR(s)')


class TestBatchExitCode:
    """The batch outcome projects onto the process exit code (#372)."""

    def test_all_scenarios_completed_exits_zero(self):
        """Every scenario succeeded — a clean run."""
        batch = _batch([_ok('a'), _ok('b')])
        assert batch.get_outcome() == RunOutcome.SUCCESS
        assert batch.get_exit_code() == 0

    def test_a_crashed_scenario_exits_two(self):
        """A real failure is a failed run, not a clean one."""
        batch = _batch([_ok('a'), _crashed('b')])
        assert batch.get_outcome() == RunOutcome.FAILED
        assert batch.get_exit_code() == 2

    def test_only_logged_errors_exits_three(self):
        """
        Nothing crashed, but errors were logged.

        Distinct from a crash: the run produced results, and the operator's question is
        'what did it log', not 'why did it die'.
        """
        batch = _batch([_ok('a'), _logged_errors('b')])
        assert batch.get_outcome() == RunOutcome.FINISHED_WITH_ERRORS
        assert batch.get_exit_code() == 3

    def test_a_crash_outranks_logged_errors(self):
        """With both kinds present the harder failure wins."""
        batch = _batch([_logged_errors('a'), _crashed('b')])
        assert batch.get_outcome() == RunOutcome.FAILED
        assert batch.get_exit_code() == 2

    def test_an_empty_batch_is_not_a_success(self):
        """
        No scenario ran at all.

        A batch that produced no results did not complete — reporting success here is the
        exact blindness #372 exists to remove.
        """
        batch = _batch([])
        assert batch.get_outcome() == RunOutcome.CRASHED
        assert batch.get_exit_code() == 1
