"""
FiniexTestingIDE - Safety Circuit Breaker Integration Tests

End-to-end tests through the AutoTrader mock pipeline.
Validates that safety checks use equity (spot) and balance (margin)
correctly during a real tick loop session.

Uses btcusd_mock_fast.json as base profile, overrides safety config
programmatically. max_ticks=15000 (profile default) ensures warmup
completes and algo produces trades.
"""

import shutil

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.types.autotrader_types.autotrader_config_types import SafetyConfig
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult


# Base profile: spot, 15K ticks, display off, INSTANT_FILL mock adapter
BASE_PROFILE = 'configs/autotrader_profiles/backtesting/btcusd_mock_fast.json'


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
    if trader._run_dir and trader._run_dir.exists():
        shutil.rmtree(trader._run_dir)
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
            w for w in safe_session.warning_messages
            if 'circuit breaker' in w.lower()
        ]
        assert len(safety_warnings) == 0, (
            f"Safety falsely triggered: {safety_warnings}"
        )

    def test_trades_executed(self, safe_session):
        """Algo should produce trades — safety must not have blocked them."""
        assert len(safe_session.trade_history) > 0, (
            'No trades executed — safety may have falsely blocked'
        )


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
            w for w in trigger_session.warning_messages
            if 'circuit breaker triggered' in w.lower()
        ]
        assert len(safety_warnings) >= 1, (
            'Expected safety trigger warning — aggressive thresholds should have fired'
        )


class TestSafetyDisabledNoInterference:
    """Safety disabled (default) must not interfere with normal trading."""

    def test_session_completes_normally(self, disabled_session):
        assert disabled_session.shutdown_mode == 'normal'

    def test_trades_executed(self, disabled_session):
        assert len(disabled_session.trade_history) > 0

    def test_no_safety_warnings(self, disabled_session):
        safety_warnings = [
            w for w in disabled_session.warning_messages
            if 'circuit breaker' in w.lower()
        ]
        assert len(safety_warnings) == 0
