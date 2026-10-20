"""
A scenario set opens two log files of its own — its run log and its summary — and a sweep builds one
set per combination in one process. So every set closes its files once nothing writes to them:
after its report, and after a run that failed.
"""

import pytest

from python.scenario import scenario_strategy_runner as runner_module
from python.scenario.scenario_set import ScenarioSet


class _Logger:
    """Stands in for a scenario logger: remembers that it was closed."""

    def __init__(self):
        self.closed = False

    def close(self, flush_buffer: bool = False):
        self.closed = True


def _set_with(run_log, summary):
    scenario_set = ScenarioSet.__new__(ScenarioSet)
    scenario_set.logger = run_log
    scenario_set.printed_summary_logger = summary
    return scenario_set


class TestASetClosesItsOwnFiles:

    def test_the_run_log_and_the_summary_close(self):
        run_log, summary = _Logger(), _Logger()
        _set_with(run_log, summary).close_loggers()
        assert run_log.closed and summary.closed

    def test_a_mount_build_has_no_summary_to_close(self):
        run_log = _Logger()
        _set_with(run_log, None).close_loggers()
        assert run_log.closed


class _FakeSet:
    """Stands in for the scenario set the runner builds."""

    built = []

    def __init__(self, *args, **kwargs):
        self.closed = False
        _FakeSet.built.append(self)

    def write_scenario_system_info_log(self):
        pass

    def close_loggers(self):
        self.closed = True


class TestTheRunClosesTheSetsFiles:

    @pytest.fixture(autouse=True)
    def fake_set(self, monkeypatch):
        _FakeSet.built = []
        monkeypatch.setattr(runner_module, 'ScenarioSet', _FakeSet)

    def test_after_its_report(self, monkeypatch):
        open_while_reporting = []

        class _Orchestrator:
            def __init__(self, *args):
                pass

            def run(self, mount=None):
                return 'summary'

        class _Coordinator:
            def __init__(self, **kwargs):
                pass

            def generate_and_log(self):
                open_while_reporting.append(not _FakeSet.built[-1].closed)

        monkeypatch.setattr(runner_module, 'BatchOrchestrator', _Orchestrator)
        monkeypatch.setattr(runner_module, 'BatchReportCoordinator', _Coordinator)

        assert runner_module.initialize_batch_and_run(None, None) == 'summary'
        assert open_while_reporting == [True], 'the report writes to the open summary first'
        assert _FakeSet.built[-1].closed

    def test_after_a_run_that_failed(self, monkeypatch):
        """An unexpected error ends the process (exit 1); the files close on the way out."""
        class _Orchestrator:
            def __init__(self, *args):
                pass

            def run(self, mount=None):
                raise RuntimeError('the run broke')

        monkeypatch.setattr(runner_module, 'BatchOrchestrator', _Orchestrator)

        with pytest.raises(SystemExit):
            runner_module.initialize_batch_and_run(None, None)
        assert _FakeSet.built[-1].closed
