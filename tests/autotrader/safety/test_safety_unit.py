"""
FiniexTestingIDE - Safety Circuit Breaker Unit Tests

Isolated tests for _check_safety logic — no executor, no tick loop, no scenario.
Uses a lightweight stub that mirrors the instance attributes _check_safety reads/writes.

Covers:
- Spot mode: min_equity triggers/clears, min_balance inert
- Margin mode: min_balance triggers/clears, min_equity inert
- Drawdown: the ACCOUNT VALUE in both models (#356), no phantom drawdown
- OR-combined conditions
- Disabled thresholds (0.0) and disabled safety (enabled=False)
"""

import logging
from types import SimpleNamespace

from python.framework.autotrader.autotrader_tick_loop import AutotraderTickLoop
from python.framework.types.autotrader_types.autotrader_config_types import (
    AutoTraderConfig,
    SafetyConfig,
)
from python.framework.types.config_types.market_config_types import TradingModel


class _SafetyStub:
    """
    Minimal stub exposing only the attributes that _check_safety reads/writes.

    Avoids constructing a full AutotraderTickLoop (requires executor, tick source, etc.).
    We bind the real _check_safety method to this stub so the logic under test is
    exactly the production code — no reimplementation.
    """

    def __init__(self, safety: SafetyConfig, trading_model: TradingModel,
                 day_start_value: float = 0.0):
        self._config = AutoTraderConfig(safety=safety)
        self._trading_model = trading_model
        self._safety_blocked = False
        self._safety_reason = ''
        self._safety_current_value = 0.0
        self._safety_drawdown_pct = 0.0
        # #356 Phase C — the breaker counts its ENGAGEMENTS for the session report. The
        # stub carries it because the stub IS the documented dependency surface: an
        # attribute the path consults has to appear here or the test stops describing it.
        self._safety_block_count = 0
        self._day_limit_hit = False
        self._logger = logging.getLogger('test_safety_stub')
        # #314 — the daily baseline is a tracker in the real loop; here it only has to
        # answer get_value(), which is all _check_daily_loss reads from it.
        self._day_baseline = (
            SimpleNamespace(get_value=lambda: day_start_value)
            if day_start_value > 0 else None)

    def check_safety(self, current_value: float, initial_balance: float) -> None:
        """Delegate to the real _check_safety method via unbound call."""
        AutotraderTickLoop._check_safety(self, current_value, initial_balance)

    def _block(self, reason: str) -> None:
        """The real accumulator, bound here for the same reason _check_safety is."""
        AutotraderTickLoop._block(self, reason)

    def _check_daily_loss(self, current_value: float) -> None:
        """The real daily check, bound here for the same reason."""
        AutotraderTickLoop._check_daily_loss(self, current_value)


def _make_stub(
    trading_model: TradingModel = TradingModel.SPOT,
    enabled: bool = True,
    min_balance: float = 0.0,
    min_equity: float = 0.0,
    max_drawdown_pct: float = 0.0,
    max_drawdown_abs: float = 0.0,
    max_daily_loss_abs: float = 0.0,
    max_daily_loss_pct: float = 0.0,
    day_start_value: float = 0.0,
) -> _SafetyStub:
    """Build a stub with the given safety config."""
    safety = SafetyConfig(
        enabled=enabled,
        min_balance=min_balance,
        min_equity=min_equity,
        max_drawdown_pct=max_drawdown_pct,
        max_drawdown_abs=max_drawdown_abs,
        max_daily_loss_abs=max_daily_loss_abs,
        max_daily_loss_pct=max_daily_loss_pct,
    )
    return _SafetyStub(safety, trading_model, day_start_value)


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


# =============================================================================
# ABSOLUTE DRAWDOWN CAP (#314)
# =============================================================================

class TestAbsoluteDrawdownCap:
    """
    The sibling of the percentage, and independent of it.

    A percentage auto-scales with the account, which is what protects a small one. On a
    large account the same percentage is a dangerously large number, and the absolute floor
    is what stops it. Either can fire first; both name themselves when they do.
    """

    def test_a_loss_beyond_the_cap_blocks(self):
        stub = _make_stub(max_drawdown_abs=1000.0)

        stub.check_safety(current_value=8_900.0, initial_balance=10_000.0)

        assert stub._safety_blocked
        assert 'max_drawdown_abs' in stub._safety_reason

    def test_a_loss_inside_the_cap_does_not(self):
        stub = _make_stub(max_drawdown_abs=1000.0)

        stub.check_safety(current_value=9_500.0, initial_balance=10_000.0)

        assert not stub._safety_blocked

    def test_zero_disables_it(self):
        stub = _make_stub(max_drawdown_abs=0.0)

        stub.check_safety(current_value=1.0, initial_balance=10_000.0)

        assert not stub._safety_blocked, 'an unset threshold must never gate anything'

    def test_it_is_independent_of_the_percentage(self):
        """
        A large account: 12 % is untouched while the absolute floor is long gone.

        That is the whole reason the pair exists rather than one of them.
        """
        stub = _make_stub(max_drawdown_pct=12.0, max_drawdown_abs=1000.0)

        stub.check_safety(current_value=97_000.0, initial_balance=100_000.0)

        assert stub._safety_blocked
        assert 'max_drawdown_abs' in stub._safety_reason
        assert 'max_drawdown (' not in stub._safety_reason, (
            'the percentage did not fire, and a reason that claims it did sends the '
            'operator to the wrong knob')


# =============================================================================
# DAILY LOSS LIMITS (#314)
# =============================================================================

class TestDailyLossLimits:
    """
    A different failure from the session drawdown, and the one a long run is exposed to.

    A session drawdown accumulates from process start; a day resets. A bot that loses a
    little every single day never trips a session limit at all.
    """

    def test_the_absolute_daily_limit_blocks(self):
        stub = _make_stub(max_daily_loss_abs=300.0, day_start_value=10_000.0)

        stub.check_safety(current_value=9_600.0, initial_balance=10_000.0)

        assert stub._safety_blocked
        assert 'max_daily_loss_abs' in stub._safety_reason

    def test_the_percentage_daily_limit_blocks(self):
        stub = _make_stub(max_daily_loss_pct=3.0, day_start_value=10_000.0)

        stub.check_safety(current_value=9_600.0, initial_balance=10_000.0)

        assert stub._safety_blocked
        assert 'max_daily_loss (' in stub._safety_reason

    def test_it_measures_against_the_DAY_not_the_session(self):
        """
        The distinction the whole feature rests on.

        Down 25 % on the session but only 1 % today: a daily limit of 3 % must stay silent,
        or it is a session limit wearing a different name.
        """
        stub = _make_stub(max_daily_loss_pct=3.0, day_start_value=7_600.0)

        stub.check_safety(current_value=7_525.0, initial_balance=10_000.0)

        assert not stub._safety_blocked, (
            f'blocked on {stub._safety_reason} — it read the session loss, not the daily one')

    def test_without_a_day_baseline_nothing_fires(self):
        """Before the first tick there is no day to measure against, and none is invented."""
        stub = _make_stub(max_daily_loss_abs=1.0, day_start_value=0.0)

        stub.check_safety(current_value=1.0, initial_balance=10_000.0)

        assert not stub._safety_blocked

    def test_every_breached_limit_is_named(self):
        """
        An operator reading a blocked session needs to know whether one limit was touched
        or three were blown through.
        """
        stub = _make_stub(
            max_drawdown_pct=5.0, max_drawdown_abs=500.0,
            max_daily_loss_abs=200.0, day_start_value=10_000.0)

        stub.check_safety(current_value=9_000.0, initial_balance=10_000.0)

        for expected in ('max_drawdown (', 'max_drawdown_abs', 'max_daily_loss_abs'):
            assert expected in stub._safety_reason, (
                f'{expected} fired but is not in "{stub._safety_reason}"')
