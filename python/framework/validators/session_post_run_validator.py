"""
FiniexTestingIDE - Session Post-Run Validator

The live counterpart of `PostRunValidator`: produces the advisory Tier-1 warnings that can only
be known AFTER a session has run, and appends them to
`AutoTraderResult.session_validation_result`. Runs once before the report coordinator, mirroring
the sim order (batch_orchestrator → PostRunValidator → reporting).

Only the checks that a single live session can actually answer live here. Most of the sim's
post-run checks need inputs a session does not have (profiling / coordination statistics, a tick
budget, several scenarios, several currencies) — see docs/architecture/warnings_errors_tiers.md.
Observed feed outages are deliberately NOT a check: they are facts and belong to the
feed-stability section, which states experience where this states intent.
"""

from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.validation_types import ValidationFinding, ValidationResult
from python.framework.validators.shared_advisory_checks import (
    check_slow_components,
    check_stress_test,
)

# Scope of a session-global finding — it concerns the run, not one unit.
_RUN_SCOPE = 'run'
# What a unit is called in the shared stress-test message (sim says 'Scenarios').
_SESSION_UNIT_LABEL = 'Session'


class SessionPostRunValidator:
    """Emits the post-run advisory warnings of a live session into its validation channel."""

    def __init__(self, result: AutoTraderResult, config: AutoTraderConfig):
        """
        Initialize the session post-run validator.

        Args:
            result: The collected session result (worker / decision statistics)
            config: The profile config the session ran with (stress config, unit name)
        """
        self._result = result
        self._config = config

    def validate(self) -> None:
        """Run all post-run advisory checks; append a run-scoped ValidationResult per finding."""
        self._check_stress_test()
        self._check_slow_components()

    def _add_finding(self, finding: ValidationFinding) -> None:
        """
        Append one already-built advisory finding to the session's validation channel.

        Args:
            finding: The advisory finding to record (from a shared check)
        """
        self._result.add_session_validation_result(ValidationResult(_RUN_SCOPE, [finding]))

    def _check_stress_test(self) -> None:
        """Warn when the profile has active stress tests (shared with the sim batch check)."""
        settings = self._config.scenario_settings
        if settings is None:
            return
        name = self._config.name or self._config.symbol
        finding = check_stress_test(
            [(name, settings.stress_test_config)], _SESSION_UNIT_LABEL)
        if finding is not None:
            self._add_finding(finding)

    def _check_slow_components(self) -> None:
        """Warn when a worker or decision logic is slow (shared with the sim batch check)."""
        worker_times = {
            w.worker_name: [w.worker_avg_time_ms] for w in self._result.worker_statistics}
        logic_times = {}
        stats = self._result.decision_statistics
        if stats and stats.decision_logic_name:
            logic_times[stats.decision_logic_name] = [stats.decision_avg_time_ms]

        for finding in check_slow_components(worker_times, logic_times):
            self._add_finding(finding)
