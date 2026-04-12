"""
FiniexTestingIDE - Safety Circuit Breaker Unit Tests

Isolated tests for _check_safety logic — no executor, no tick loop, no scenario.
Uses a lightweight stub that mirrors the instance attributes _check_safety reads/writes.

Covers:
- Spot mode: min_equity triggers/clears, min_balance inert
- Margin mode: min_balance triggers/clears, min_equity inert
- Drawdown: equity-based (spot) vs balance-based (margin), no phantom drawdown
- OR-combined conditions
- Disabled thresholds (0.0) and disabled safety (enabled=False)
"""

import logging

from python.framework.autotrader.autotrader_tick_loop import AutotraderTickLoop
from python.framework.types.autotrader_types.autotrader_config_types import (
    AutoTraderConfig,
    SafetyConfig,
)
from python.framework.types.market_types.market_config_types import TradingModel


class _SafetyStub:
    """
    Minimal stub exposing only the attributes that _check_safety reads/writes.

    Avoids constructing a full AutotraderTickLoop (requires executor, tick source, etc.).
    We bind the real _check_safety method to this stub so the logic under test is
    exactly the production code — no reimplementation.
    """

    def __init__(self, safety: SafetyConfig, trading_model: TradingModel):
        self._config = AutoTraderConfig(safety=safety)
        self._trading_model = trading_model
        self._safety_blocked = False
        self._safety_reason = ''
        self._safety_current_value = 0.0
        self._safety_drawdown_pct = 0.0
        self._logger = logging.getLogger('test_safety_stub')

    def check_safety(self, current_value: float, initial_balance: float) -> None:
        """Delegate to the real _check_safety method via unbound call."""
        AutotraderTickLoop._check_safety(self, current_value, initial_balance)


def _make_stub(
    trading_model: TradingModel = TradingModel.SPOT,
    enabled: bool = True,
    min_balance: float = 0.0,
    min_equity: float = 0.0,
    max_drawdown_pct: float = 0.0,
) -> _SafetyStub:
    """Build a stub with the given safety config."""
    safety = SafetyConfig(
        enabled=enabled,
        min_balance=min_balance,
        min_equity=min_equity,
        max_drawdown_pct=max_drawdown_pct,
    )
    return _SafetyStub(safety, trading_model)


# =============================================================================
# SPOT MODE — min_equity
# =============================================================================

class TestSpotMinEquity:
    """Spot mode uses min_equity, ignores min_balance."""

    def test_equity_above_threshold_not_blocked(self):
        stub = _make_stub(TradingModel.SPOT, min_equity=5.0)
        stub.check_safety(current_value=12.48, initial_balance=12.49)
        assert not stub._safety_blocked

    def test_equity_below_threshold_blocked(self):
        stub = _make_stub(TradingModel.SPOT, min_equity=5.0)
        stub.check_safety(current_value=4.80, initial_balance=12.49)
        assert stub._safety_blocked
        assert 'min_equity' in stub._safety_reason

    def test_equity_recovers_clears_block(self):
        stub = _make_stub(TradingModel.SPOT, min_equity=5.0)
        stub.check_safety(current_value=4.80, initial_balance=12.49)
        assert stub._safety_blocked

        stub.check_safety(current_value=6.0, initial_balance=12.49)
        assert not stub._safety_blocked

    def test_min_balance_inert_in_spot_mode(self):
        """min_balance is set but spot mode should only check min_equity."""
        stub = _make_stub(TradingModel.SPOT, min_balance=100.0, min_equity=0.0)
        # current_value=5.0 is well below min_balance=100 but min_equity=0 (disabled)
        stub.check_safety(current_value=5.0, initial_balance=100.0)
        assert not stub._safety_blocked


# =============================================================================
# MARGIN MODE — min_balance
# =============================================================================

class TestMarginMinBalance:
    """Margin mode uses min_balance, ignores min_equity."""

    def test_balance_above_threshold_not_blocked(self):
        stub = _make_stub(TradingModel.MARGIN, min_balance=500.0)
        stub.check_safety(current_value=9800.0, initial_balance=10000.0)
        assert not stub._safety_blocked

    def test_balance_below_threshold_blocked(self):
        stub = _make_stub(TradingModel.MARGIN, min_balance=500.0)
        stub.check_safety(current_value=450.0, initial_balance=10000.0)
        assert stub._safety_blocked
        assert 'min_balance' in stub._safety_reason

    def test_balance_recovers_clears_block(self):
        stub = _make_stub(TradingModel.MARGIN, min_balance=500.0)
        stub.check_safety(current_value=450.0, initial_balance=10000.0)
        assert stub._safety_blocked

        stub.check_safety(current_value=600.0, initial_balance=10000.0)
        assert not stub._safety_blocked

    def test_min_equity_inert_in_margin_mode(self):
        """min_equity is set but margin mode should only check min_balance."""
        stub = _make_stub(TradingModel.MARGIN, min_balance=0.0, min_equity=9999.0)
        # current_value=500 is well below min_equity=9999 but min_balance=0 (disabled)
        stub.check_safety(current_value=500.0, initial_balance=10000.0)
        assert not stub._safety_blocked


# =============================================================================
# DRAWDOWN — mode-specific basis
# =============================================================================

class TestDrawdown:
    """max_drawdown_pct checks against the passed value (equity or balance)."""

    def test_spot_drawdown_based_on_equity(self):
        """Drawdown computed from equity — not raw balance."""
        stub = _make_stub(TradingModel.SPOT, max_drawdown_pct=30.0)
        # equity dropped 10% from initial
        stub.check_safety(current_value=9.0, initial_balance=10.0)
        assert not stub._safety_blocked
        assert abs(stub._safety_drawdown_pct - 10.0) < 0.01

    def test_spot_no_phantom_drawdown_after_buy(self):
        """
        Core #270 fix: buying an asset in spot mode transfers USD to asset.
        Equity stays the same — no phantom drawdown.
        """
        stub = _make_stub(TradingModel.SPOT, max_drawdown_pct=20.0)
        # Simulate: initial 12.49 USD. After BUY, equity is still ~12.48 (spread cost only)
        stub.check_safety(current_value=12.48, initial_balance=12.49)
        assert not stub._safety_blocked
        assert stub._safety_drawdown_pct < 1.0  # negligible spread cost

    def test_margin_drawdown_based_on_balance(self):
        stub = _make_stub(TradingModel.MARGIN, max_drawdown_pct=30.0)
        # balance dropped 25% from initial
        stub.check_safety(current_value=7500.0, initial_balance=10000.0)
        assert not stub._safety_blocked
        assert abs(stub._safety_drawdown_pct - 25.0) < 0.01

    def test_drawdown_exceeds_threshold_blocked(self):
        stub = _make_stub(TradingModel.SPOT, max_drawdown_pct=20.0)
        stub.check_safety(current_value=7.0, initial_balance=10.0)
        assert stub._safety_blocked
        assert 'max_drawdown' in stub._safety_reason
        assert abs(stub._safety_drawdown_pct - 30.0) < 0.01

    def test_drawdown_recovers_clears_block(self):
        stub = _make_stub(TradingModel.SPOT, max_drawdown_pct=20.0)
        stub.check_safety(current_value=7.0, initial_balance=10.0)
        assert stub._safety_blocked

        stub.check_safety(current_value=9.5, initial_balance=10.0)
        assert not stub._safety_blocked


# =============================================================================
# OR-COMBINED CONDITIONS
# =============================================================================

class TestCombinedConditions:
    """Both min threshold and drawdown fire — OR combined."""

    def test_both_conditions_trigger_combined_reason(self):
        stub = _make_stub(
            TradingModel.SPOT, min_equity=8.0, max_drawdown_pct=10.0,
        )
        # equity=5.0, initial=10.0 → dd=50%, below min_equity=8.0
        stub.check_safety(current_value=5.0, initial_balance=10.0)
        assert stub._safety_blocked
        assert 'min_equity' in stub._safety_reason
        assert 'max_drawdown' in stub._safety_reason

    def test_only_min_triggers(self):
        stub = _make_stub(
            TradingModel.SPOT, min_equity=8.0, max_drawdown_pct=80.0,
        )
        # equity=7.0, initial=10.0 → dd=30% (< 80%), but below min_equity=8.0
        stub.check_safety(current_value=7.0, initial_balance=10.0)
        assert stub._safety_blocked
        assert 'min_equity' in stub._safety_reason
        assert 'max_drawdown' not in stub._safety_reason

    def test_only_drawdown_triggers(self):
        stub = _make_stub(
            TradingModel.SPOT, min_equity=3.0, max_drawdown_pct=20.0,
        )
        # equity=7.0, initial=10.0 → dd=30% (> 20%), but above min_equity=3.0
        stub.check_safety(current_value=7.0, initial_balance=10.0)
        assert stub._safety_blocked
        assert 'min_equity' not in stub._safety_reason
        assert 'max_drawdown' in stub._safety_reason


# =============================================================================
# DISABLED THRESHOLDS / DISABLED SAFETY
# =============================================================================

class TestDisabled:
    """Zero thresholds and safety.enabled=False."""

    def test_zero_min_threshold_disabled(self):
        stub = _make_stub(TradingModel.SPOT, min_equity=0.0, max_drawdown_pct=0.0)
        stub.check_safety(current_value=0.01, initial_balance=10000.0)
        assert not stub._safety_blocked

    def test_zero_drawdown_threshold_disabled(self):
        stub = _make_stub(TradingModel.SPOT, min_equity=0.0, max_drawdown_pct=0.0)
        stub.check_safety(current_value=1.0, initial_balance=10000.0)
        assert not stub._safety_blocked
        assert stub._safety_drawdown_pct == 0.0

    def test_safety_disabled_no_check(self):
        stub = _make_stub(
            TradingModel.SPOT, enabled=False,
            min_equity=99999.0, max_drawdown_pct=0.1,
        )
        # Would trigger everything — but safety is disabled
        stub.check_safety(current_value=0.01, initial_balance=10000.0)
        assert not stub._safety_blocked


# =============================================================================
# DISPLAY STATE TRACKING
# =============================================================================

class TestDisplayState:
    """_safety_current_value and _safety_drawdown_pct are stored for display."""

    def test_current_value_stored(self):
        stub = _make_stub(TradingModel.SPOT, min_equity=5.0)
        stub.check_safety(current_value=12.48, initial_balance=12.49)
        assert abs(stub._safety_current_value - 12.48) < 0.001

    def test_drawdown_pct_stored(self):
        stub = _make_stub(TradingModel.SPOT, max_drawdown_pct=30.0)
        stub.check_safety(current_value=8.0, initial_balance=10.0)
        assert abs(stub._safety_drawdown_pct - 20.0) < 0.01

    def test_drawdown_pct_floored_at_zero(self):
        """If value > initial (profit), drawdown should be 0, not negative."""
        stub = _make_stub(TradingModel.SPOT, max_drawdown_pct=30.0)
        stub.check_safety(current_value=11.0, initial_balance=10.0)
        assert stub._safety_drawdown_pct == 0.0
