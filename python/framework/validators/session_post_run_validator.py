"""
FiniexTestingIDE - Session Post-Run Validator

The live counterpart of `PostRunValidator`: produces the advisory Tier-1 warnings that can only
be known AFTER a session has run, and appends them to
`AutoTraderResult.session_validation_result`. Runs once before the report coordinator, mirroring
the sim order (batch_orchestrator → PostRunValidator → reporting).

Only the checks a single live session can honestly answer live here. Most of the sim's post-run
checks need inputs a session does not have (profiling / coordination statistics, a tick budget,
several scenarios, several currencies) — see docs/architecture/warnings_errors_tiers.md. One
check is live-only in the other direction: clipping, which the sim judges against a CONFIGURED
budget while a session has only what it observed.

Observed feed outages are deliberately NOT a check: they are facts and belong to the
feed-stability section, which states experience where this states intent.
"""

from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.validation_types import (
    Severity,
    ValidationDomain,
    ValidationFinding,
    ValidationResult,
)
from python.framework.validators.shared_advisory_checks import check_stress_test

# Scope of a session-global finding — it concerns the run, not one unit.
_RUN_SCOPE = 'run'
# What a unit is called in the shared stress-test message (sim says 'Scenarios').
_SESSION_UNIT_LABEL = 'Session'
# Stable id of the clipping advisory (live-only — sim judges against a configured budget).
_CLIPPING_CHECK = 'clipping'


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
        self._check_clipping()

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

    def _check_clipping(self) -> None:
        """
        Warn when too many ticks arrived while the previous one was still being processed.

        Live-only, and the one performance verdict a session can honestly make: the clipping
        ratio is measured against REAL tick arrival, so it says how often the algo failed to
        keep up — unlike an absolute per-component threshold, which cannot know the tick rate.
        The sim's counterpart is the tick-budget family, which has a configured budget to
        judge against; live has none, only what it observed.
        """
        clipping = self._result.clipping_summary
        if clipping.total_ticks == 0:
            return
        limit = self._config.clipping_monitor.warn_above_ratio
        if clipping.clipping_ratio <= limit:
            return

        self._add_finding(ValidationFinding(
            severity=Severity.WARNING, check=_CLIPPING_CHECK,
            domain=ValidationDomain.PROFILING, scope=_RUN_SCOPE,
            message=(
                f'Clipping ratio {clipping.clipping_ratio:.1%} exceeds {limit:.1%} — '
                f'{clipping.ticks_clipped}/{clipping.total_ticks} ticks arrived while the '
                f'previous one was still being processed, so those decisions ran on data up '
                f'to {clipping.max_stale_ms:.1f}ms old (avg processing '
                f'{clipping.avg_processing_ms:.2f}ms). The algo is not keeping up with this '
                f'tick rate.')))
