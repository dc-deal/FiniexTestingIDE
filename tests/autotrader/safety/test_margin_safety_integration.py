"""
FiniexTestingIDE - Margin Safety Circuit Breaker Integration Tests

End-to-end tests through the AutoTrader mock pipeline in margin mode.
Validates that safety checks use balance (not equity) correctly during
a real tick loop session with mt5/EURUSD.

Uses margin_safety_test.json as base profile (backtesting_margin_stress
decision logic with deterministic trade_sequence), overrides safety config
programmatically. max_ticks=15000 ensures warmup completes and algo
produces trades.

Counterpart to test_safety_integration.py (spot mode).
"""

import shutil

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.types.autotrader_types.autotrader_config_types import SafetyConfig
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult


# Base profile: margin (mt5), 15K ticks, display off, INSTANT_FILL mock adapter
BASE_PROFILE = 'configs/autotrader_profiles/backtesting/margin_safety_test.json'


def _run_with_margin_safety(safety: SafetyConfig) -> AutoTraderResult:
    """
    Run a mock AutoTrader session in margin mode with overridden safety config.

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
def margin_safe_session():
    """Safety enabled with generous thresholds — no false positive expected."""
    return _run_with_margin_safety(SafetyConfig(
        enabled=True,
        min_balance=100.0,       # well below 10000 initial
        max_drawdown_pct=50.0,   # generous threshold
    ))


@pytest.fixture(scope='module')
def margin_trigger_session():
    """Safety enabled with aggressive min_balance — trigger expected immediately."""
    return _run_with_margin_safety(SafetyConfig(
        enabled=True,
        min_balance=10001.0,     # above 10000 initial → triggers on first tick
        max_drawdown_pct=50.0,   # generous — only min_balance should trigger
    ))


@pytest.fixture(scope='module')
def margin_disabled_session():
    """Safety disabled (default) — no interference expected."""
    return _run_with_margin_safety(SafetyConfig(enabled=False))


# =============================================================================
# Tests
# =============================================================================

class TestMarginSafetyNoFalsePositive:
    """
    Margin mode with reasonable safety thresholds must NOT false-trigger.

    With initial balance 10000 USD and reasonable min_balance / max_drawdown_pct,
    normal trading should not activate the circuit breaker.
    """

    def test_session_completes_normally(self, margin_safe_session):
        assert margin_safe_session.shutdown_mode == 'normal'

    def test_no_safety_warnings(self, margin_safe_session):
        """No circuit breaker trigger messages in session warnings."""
        safety_warnings = [
            w for w in margin_safe_session.warning_messages
            if 'circuit breaker' in w.lower()
        ]
        assert len(safety_warnings) == 0, (
            f"Safety falsely triggered: {safety_warnings}"
        )

    def test_trades_executed(self, margin_safe_session):
        """Algo should produce trades — safety must not have blocked them."""
        assert len(margin_safe_session.trade_history) > 0, (
            'No trades executed — safety may have falsely blocked'
        )


class TestMarginSafetyTriggers:
    """
    Margin mode with aggressive min_balance triggers the circuit breaker.

    min_balance set to 10001 (above 10000 initial) — triggers immediately
    since balance starts at 10000 which is below the threshold.
    """

    def test_session_completes_normally(self, margin_trigger_session):
        """Session should still complete — safety is a soft stop, not a crash."""
        assert margin_trigger_session.shutdown_mode == 'normal'

    def test_safety_triggered(self, margin_trigger_session):
        """Circuit breaker warning must appear in session log."""
        safety_warnings = [
            w for w in margin_trigger_session.warning_messages
            if 'circuit breaker triggered' in w.lower()
        ]
        assert len(safety_warnings) >= 1, (
            'Expected safety trigger warning — aggressive min_balance should have fired'
        )

    def test_trigger_uses_min_balance(self, margin_trigger_session):
        """Warning must reference min_balance (not min_equity) in margin mode."""
        safety_warnings = [
            w for w in margin_trigger_session.warning_messages
            if 'circuit breaker triggered' in w.lower()
        ]
        assert any('min_balance' in w for w in safety_warnings), (
            f"Expected 'min_balance' in warning, got: {safety_warnings}"
        )

    def test_no_trades_after_trigger(self, margin_trigger_session):
        """Circuit breaker blocks all entries — no trades expected."""
        assert len(margin_trigger_session.trade_history) == 0, (
            f"Expected no trades after safety trigger, got {len(margin_trigger_session.trade_history)}"
        )


class TestMarginSafetyDisabledNoInterference:
    """Safety disabled (default) must not interfere with normal margin trading."""

    def test_session_completes_normally(self, margin_disabled_session):
        assert margin_disabled_session.shutdown_mode == 'normal'

    def test_trades_executed(self, margin_disabled_session):
        assert len(margin_disabled_session.trade_history) > 0

    def test_no_safety_warnings(self, margin_disabled_session):
        safety_warnings = [
            w for w in margin_disabled_session.warning_messages
            if 'circuit breaker' in w.lower()
        ]
        assert len(safety_warnings) == 0
