"""
Holds the test runner's two declarations to their subjects.

`SuiteOutcome` says how a suite's run ended, and the runner decides on that name rather than on
an exit code — so a member nothing produces, or a code the map does not answer, would be a
silent hole: the suite would be reported as something it was not. The configuration model owns
the runner's defaults, and a default that disagrees with the file the operator reads is a
setting nobody can see.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from python.framework.types.config_types.suite_runner_config_types import SuiteRunnerConfig
from python.framework.types.test_runner_types import SuiteOutcome

# The two outcomes a finished process cannot report about itself: one is the runner giving up on
# a process still running, the other a suite the run never reached.
NOT_FROM_EXIT_CODE = {SuiteOutcome.TIMED_OUT, SuiteOutcome.NOT_STARTED}

CONFIG_PATH = Path('configs/test_config.json')


class TestOutcomeClassification:
    """Every outcome is produced by something, and every exit code yields one."""

    def test_every_outcome_is_reachable(self):
        """No member exists that nothing can ever produce."""
        from_codes = {SuiteOutcome.from_exit_code(code) for code in range(-9, 16)}
        assert from_codes | NOT_FROM_EXIT_CODE == set(SuiteOutcome)

    def test_nothing_reachable_is_declared_unreachable(self):
        """The two outcomes declared as not coming from an exit code really do not."""
        from_codes = {SuiteOutcome.from_exit_code(code) for code in range(-9, 16)}
        assert not (from_codes & NOT_FROM_EXIT_CODE)

    @pytest.mark.parametrize('exit_code,expected', [
        (0, SuiteOutcome.PASSED),
        (1, SuiteOutcome.TESTS_FAILED),
        (2, SuiteOutcome.INTERRUPTED),
        (3, SuiteOutcome.INTERNAL_ERROR),
        (4, SuiteOutcome.USAGE_ERROR),
        (5, SuiteOutcome.PASSED),
    ])
    def test_pytest_documented_codes(self, exit_code, expected):
        """Each code pytest documents is read as what pytest says it means."""
        assert SuiteOutcome.from_exit_code(exit_code) is expected

    def test_a_signal_is_a_kill_not_a_test_failure(self):
        """A process ended by a signal reports a dead process, never a defect in the code."""
        assert SuiteOutcome.from_exit_code(-9) is SuiteOutcome.KILLED

    def test_an_undocumented_code_is_unknown(self):
        """A code nothing maps becomes UNKNOWN rather than the nearest guess."""
        assert SuiteOutcome.from_exit_code(42) is SuiteOutcome.UNKNOWN

    def test_only_a_finished_suite_has_counts_worth_reading(self):
        """Exactly the two outcomes that ran to the end carry usable pass and fail counts."""
        with_verdict = {o for o in SuiteOutcome if o.reached_a_verdict()}
        assert with_verdict == {SuiteOutcome.PASSED, SuiteOutcome.TESTS_FAILED}

    def test_a_suite_never_started_does_not_fail_the_run_on_its_own(self):
        """A suite the run never reached is not a verdict about that suite."""
        assert not SuiteOutcome.NOT_STARTED.is_failure()
        assert not SuiteOutcome.PASSED.is_failure()
        assert all(outcome.is_failure() for outcome in SuiteOutcome
                   if outcome not in (SuiteOutcome.PASSED, SuiteOutcome.NOT_STARTED))


class TestSuiteRunnerConfiguration:
    """The model owns the defaults, and the file the operator reads shows the same values."""

    def test_defaults_are_mirrored_in_the_config_file(self):
        """A default exists twice — on the model and in the file — and the two agree."""
        raw = json.loads(CONFIG_PATH.read_text())
        defaults = SuiteRunnerConfig()
        assert raw['lanes'] == defaults.lanes
        assert raw['suite_timeout_seconds'] == defaults.suite_timeout_seconds
        assert raw['fail_fast'] == defaults.fail_fast

    def test_the_file_is_accepted_as_it_stands(self):
        """The shipped file builds the model, so no key in it is one the model forbids."""
        assert SuiteRunnerConfig(**json.loads(CONFIG_PATH.read_text()))

    def test_a_misspelled_key_is_refused(self):
        """A typo is a hard error rather than a setting that silently does nothing."""
        with pytest.raises(ValidationError):
            SuiteRunnerConfig(failfast=True)

    def test_a_run_always_has_at_least_one_lane(self):
        """Zero lanes would start nothing, which is worse than running one at a time."""
        with pytest.raises(ValidationError):
            SuiteRunnerConfig(lanes=0)
