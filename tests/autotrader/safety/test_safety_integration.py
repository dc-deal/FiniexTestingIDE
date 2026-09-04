"""
FiniexTestingIDE - Safety Circuit Breaker Integration Tests

End-to-end tests through the AutoTrader mock pipeline.
Validates that safety checks use equity (spot) and balance (margin)
correctly during a real tick loop session.

Uses btcusd_mock_safety.json as base profile, overrides safety config
programmatically. max_ticks=15000 (profile default) ensures warmup
completes and algo produces trades.
"""


import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.types.autotrader_types.autotrader_config_types import SafetyConfig
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.log_level import LogLevel
from tests.shared.fixture_helpers import logged_messages, remove_run_dir

# Base profile: spot, 15K ticks, display off, INSTANT_FILL mock adapter
BASE_PROFILE = 'configs/autotrader_profiles/backtesting/btcusd_mock_safety.json'


def _run_with_safety(safety: SafetyConfig) -> AutoTraderResult:
    """
    Run a mock AutoTrader session with overridden safety config.

    Args:
        safety: SafetyConfig to inject

    Returns:
        AutoTraderResult from the session
    """
    config = load_autotrader_config(BASE_PROFILE)
    config.safety = safety
    trader = AutotraderMain(config)
    result = trader.run()
    # Clean up log directory
    remove_run_dir(trader._run_dir)
    return result


# =============================================================================
# Shared session fixtures (scope=module: one run per scenario, shared by tests)
# =============================================================================

@pytest.fixture(scope='module')
def safe_session():
    """Safety enabled with generous thresholds — no false positive expected."""
    return _run_with_safety(SafetyConfig(
        enabled=True,
        min_equity=100.0,       # well below 10000 initial
        max_drawdown_pct=50.0,  # generous threshold
    ))


@pytest.fixture(scope='module')
def trigger_session():
    """Safety enabled with aggressive thresholds — trigger expected."""
    return _run_with_safety(SafetyConfig(
        enabled=True,
        min_equity=9999.0,      # just below 10000 initial — spread cost triggers
        max_drawdown_pct=0.01,  # 0.01% — nearly zero tolerance
    ))


@pytest.fixture(scope='module')
def disabled_session():
    """Safety disabled (default) — no interference expected."""
    return _run_with_safety(SafetyConfig(enabled=False))


# =============================================================================
# Tests
# =============================================================================

class TestSpotSafetyNoFalsePositive:
    """
    Spot mode with reasonable safety thresholds must NOT false-trigger.

    With initial balance 10000 USD and reasonable min_equity / max_drawdown_pct,
    normal trading should not activate the circuit breaker.
    """

    def test_session_completes_normally(self, safe_session):
        assert safe_session.shutdown_mode == 'normal'

    def test_no_safety_warnings(self, safe_session):
        """No circuit breaker trigger messages in session warnings."""
        safety_warnings = [
            w for w in logged_messages(safe_session, LogLevel.WARNING)
            if 'circuit breaker' in w.lower()
        ]
        assert len(safety_warnings) == 0, (
            f'Safety falsely triggered: {safety_warnings}'
        )

    def test_the_algo_was_allowed_to_act(self, safe_session):
        """
        Positions were opened — the circuit breaker did not block entries.

        The proxy used to be a COMPLETED trade, and in this profile every completed trade
        came from the end-of-session force-close: the algo does not exit within the tick
        budget and the MockAdapter monitors no SL/TP. With that exit gone (#492) the proof
        that safety allowed trading is the position itself, which is also the more direct
        observation — a soft stop blocks ENTRIES, so an entry disproves it.
        """
        acted = len(safe_session.trade_history) + len(safe_session.open_positions)
        assert acted > 0, 'Nothing was opened or closed — safety may have falsely blocked'


class TestSpotSafetyTriggers:
    """
    Spot mode with aggressive threshold triggers the circuit breaker.

    min_equity set to 9999 on a 10000 account — any spread cost triggers.
    max_drawdown_pct=0.01% — nearly zero tolerance.
    """

    def test_session_completes_normally(self, trigger_session):
        """Session should still complete — safety is a soft stop, not a crash."""
        assert trigger_session.shutdown_mode == 'normal'

    def test_safety_triggered(self, trigger_session):
        """Circuit breaker warning must appear in session log."""
        safety_warnings = [
            w for w in logged_messages(trigger_session, LogLevel.WARNING)
            if 'circuit breaker triggered' in w.lower()
        ]
        assert len(safety_warnings) >= 1, (
            'Expected safety trigger warning — aggressive thresholds should have fired'
        )


class TestSafetyDisabledNoInterference:
    """Safety disabled (default) must not interfere with normal trading."""

    def test_session_completes_normally(self, disabled_session):
        assert disabled_session.shutdown_mode == 'normal'

    def test_the_algo_was_allowed_to_act(self, disabled_session):
        """Same reasoning as TestSpotSafetyNoFalsePositive: an entry disproves a block."""
        acted = len(disabled_session.trade_history) + len(disabled_session.open_positions)
        assert acted > 0

    def test_no_safety_warnings(self, disabled_session):
        safety_warnings = [
            w for w in logged_messages(disabled_session, LogLevel.WARNING)
            if 'circuit breaker' in w.lower()
        ]
        assert len(safety_warnings) == 0
