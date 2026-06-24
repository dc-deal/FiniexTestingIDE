"""
Swap / overnight-funding accrual tests (#365).

Drives the PortfolioManager accrual directly with a real MT5 broker config and a
controllable clock: opens a position, advances the canonical clock across 17:00-NY
rollovers, and asserts the signed swap on the position — the worked debit / credit /
triple examples, the spot gate, and determinism (idempotent re-accrual).
"""

from datetime import datetime, timezone

import pytest

from python.framework.exceptions.swap_errors import SwapModeNotImplementedError
from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.trading_env.portfolio_manager import PortfolioManager
from python.framework.types.config_types.market_config_types import SwapRolloverConfig
from python.framework.types.portfolio_types.portfolio_types import Position
from python.framework.types.trading_env_types.broker_types import SwapMode
from python.framework.types.trading_env_types.order_types import OrderDirection

_MT5_CONFIG = 'configs/brokers/mt5/mt5_broker_config.json'


class _NullLogger:
    """Minimal duck-typed logger — the accrual path does not log."""

    def verbose(self, *args, **kwargs): pass
    def debug(self, *args, **kwargs): pass
    def info(self, *args, **kwargs): pass
    def warning(self, *args, **kwargs): pass
    def error(self, *args, **kwargs): pass


class _Clock:
    """Controllable canonical clock injected as the portfolio's clock_fn."""

    def __init__(self, now: datetime):
        self._now = now

    def set(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


def _utc(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _portfolio(clock: _Clock, spot_mode: bool = False) -> PortfolioManager:
    broker_config = BrokerConfigFactory.build_broker_config(_MT5_CONFIG)
    return PortfolioManager(
        logger=_NullLogger(), initial_balance=10_000.0, account_currency='USD',
        broker_config=broker_config, leverage=500,
        margin_call_level=50.0, stop_out_level=20.0,
        spot_mode=spot_mode, clock_fn=clock.now,
        swap_rollover=SwapRolloverConfig(local_time='17:00', timezone='America/New_York'))


def _open(portfolio: PortfolioManager, direction: OrderDirection,
          entry_time: datetime, lots: float = 1.0, tick_value: float = 1.0) -> Position:
    position = Position(
        position_id='p1', symbol='EURUSD', direction=direction, lots=lots,
        original_lots=lots, entry_price=1.10, entry_time=entry_time,
        entry_tick_value=tick_value, digits=5, contract_size=100000)
    portfolio.open_positions['p1'] = position
    return position


def test_long_debit_two_nights():
    # EURUSD swap_long = -7.85 (debit). Mon 10:00 → Wed 09:00 = Mon + Tue (2 normal nights).
    clock = _Clock(_utc(2026, 1, 12, 10))
    portfolio = _portfolio(clock)
    position = _open(portfolio, OrderDirection.LONG, _utc(2026, 1, 12, 10))

    clock.set(_utc(2026, 1, 14, 9))
    portfolio._accrue_all_swaps()

    assert position.get_swap_cost() == pytest.approx(15.70)  # 2 × 7.85 debit


def test_short_credit_triple_wednesday():
    # EURUSD swap_short = +3.8 (credit). Tue 12:00 → Thu 08:00 = Tue ×1 + Wed ×3 = 4 swap-days.
    clock = _Clock(_utc(2026, 1, 13, 12))
    portfolio = _portfolio(clock)
    position = _open(portfolio, OrderDirection.SHORT, _utc(2026, 1, 13, 12))

    clock.set(_utc(2026, 1, 15, 8))
    portfolio._accrue_all_swaps()

    assert position.get_swap_cost() == pytest.approx(-15.20)  # 4 × 3.8 credit (negative cost)


def test_spot_mode_accrues_no_swap():
    clock = _Clock(_utc(2026, 1, 12, 10))
    portfolio = _portfolio(clock, spot_mode=True)
    position = _open(portfolio, OrderDirection.LONG, _utc(2026, 1, 12, 10))

    clock.set(_utc(2026, 1, 14, 9))
    portfolio._accrue_all_swaps()

    assert position.get_swap_cost() == 0.0


def test_accrual_is_deterministic_and_idempotent():
    # Same inputs twice → identical swap; a second accrual at the same clock adds nothing
    # (the window is consumed once — swap_accrued_until advances).
    results = []
    for _ in range(2):
        clock = _Clock(_utc(2026, 1, 13, 12))
        portfolio = _portfolio(clock)
        position = _open(portfolio, OrderDirection.SHORT, _utc(2026, 1, 13, 12))
        clock.set(_utc(2026, 1, 15, 8))
        portfolio._accrue_all_swaps()
        portfolio._accrue_all_swaps()  # idempotent — no double-count
        results.append(position.get_swap_cost())
    assert results[0] == results[1] == pytest.approx(-15.20)


class TestSwapModeImplemented:
    """SwapMode.is_implemented + SwapModeNotImplementedError — the #407 contract both
    pipelines rely on (sim validator marks invalid, AutoTrader startup raises)."""

    def test_points_and_none_are_implemented(self):
        assert SwapMode.POINTS.is_implemented
        assert SwapMode.NONE.is_implemented

    def test_other_modes_not_implemented(self):
        assert not SwapMode.INTEREST_CURRENT.is_implemented
        assert not SwapMode.INTEREST_OPEN.is_implemented
        assert not SwapMode.PERCENTAGE.is_implemented
        assert not SwapMode.UNKNOWN.is_implemented

    def test_exception_names_symbol_and_mode(self):
        err = SwapModeNotImplementedError('XAUUSD', SwapMode.PERCENTAGE)
        assert 'XAUUSD' in str(err)
        assert 'percentage' in str(err)
        assert err.symbol == 'XAUUSD'
        assert err.swap_mode == SwapMode.PERCENTAGE

    def test_exception_is_finiex_and_value_error(self):
        # multiple inheritance (§10) — catchable as both
        err = SwapModeNotImplementedError('EURUSD', SwapMode.UNKNOWN)
        assert isinstance(err, ValueError)
