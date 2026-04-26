"""
FiniexTestingIDE - Parity Comparators

Assertion helpers for dual-pipeline parity tests.
Each function takes outputs from both pipelines and asserts structural
and numerical equivalence with clear diff messages on failure.
"""

from typing import List

import pytest

from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.types.portfolio_types.portfolio_aggregation_types import PortfolioStats
from python.framework.types.portfolio_types.portfolio_trade_record_types import TradeRecord

_DEFAULT_FLOAT_TOL = 1e-6


def assert_bars_equal(
    sim_controller: BarRenderingController,
    at_controller: BarRenderingController,
    symbol: str,
    timeframe: str,
    float_tolerance: float = _DEFAULT_FLOAT_TOL,
) -> None:
    """
    Assert that both controllers produced identical bar history for symbol/timeframe.

    Checks bar count, OHLC, volume, tick_count, and timestamp for every bar.

    Args:
        sim_controller: BarRenderingController from simulation pipeline
        at_controller: BarRenderingController from AutoTrader pipeline
        symbol: Trading symbol (e.g. 'BTCUSD')
        timeframe: Timeframe string (e.g. 'M5')
        float_tolerance: Absolute tolerance for float comparisons
    """
    sim_history = sim_controller.get_bar_history(symbol, timeframe)
    at_history = at_controller.get_bar_history(symbol, timeframe)

    assert len(sim_history) == len(at_history), (
        f'Bar count mismatch [{symbol}/{timeframe}]: '
        f'sim={len(sim_history)}, at={len(at_history)}'
    )

    for i, (s, a) in enumerate(zip(sim_history, at_history)):
        ctx = f'Bar[{i}] [{symbol}/{timeframe}]'
        assert s.open == pytest.approx(a.open, abs=float_tolerance), \
            f'{ctx} open: sim={s.open}, at={a.open}'
        assert s.high == pytest.approx(a.high, abs=float_tolerance), \
            f'{ctx} high: sim={s.high}, at={a.high}'
        assert s.low == pytest.approx(a.low, abs=float_tolerance), \
            f'{ctx} low: sim={s.low}, at={a.low}'
        assert s.close == pytest.approx(a.close, abs=float_tolerance), \
            f'{ctx} close: sim={s.close}, at={a.close}'
        assert s.volume == pytest.approx(a.volume, abs=float_tolerance), \
            f'{ctx} volume: sim={s.volume}, at={a.volume}'
        assert s.tick_count == a.tick_count, \
            f'{ctx} tick_count: sim={s.tick_count}, at={a.tick_count}'
        assert s.timestamp == a.timestamp, \
            f'{ctx} timestamp: sim={s.timestamp}, at={a.timestamp}'


def assert_trades_equal(
    sim_trades: List[TradeRecord],
    at_trades: List[TradeRecord],
    float_tolerance: float = _DEFAULT_FLOAT_TOL,
) -> None:
    """
    Assert that both pipelines produced an identical trade history.

    Checks count, direction, lots, entry/exit price, and close_reason
    for every trade in sequence.

    Args:
        sim_trades: TradeRecord list from simulation
        at_trades: TradeRecord list from AutoTrader
        float_tolerance: Absolute tolerance for price and P&L comparisons
    """
    assert len(sim_trades) == len(at_trades), (
        f'Trade count mismatch: sim={len(sim_trades)}, at={len(at_trades)}'
    )

    for i, (s, a) in enumerate(zip(sim_trades, at_trades)):
        ctx = f'Trade[{i}]'
        assert s.symbol == a.symbol, \
            f'{ctx} symbol: {s.symbol} vs {a.symbol}'
        assert s.direction == a.direction, \
            f'{ctx} direction: {s.direction} vs {a.direction}'
        assert s.lots == pytest.approx(a.lots, abs=float_tolerance), \
            f'{ctx} lots: {s.lots} vs {a.lots}'
        assert s.entry_price == pytest.approx(a.entry_price, abs=float_tolerance), \
            f'{ctx} entry_price: {s.entry_price} vs {a.entry_price}'
        assert s.exit_price == pytest.approx(a.exit_price, abs=float_tolerance), \
            f'{ctx} exit_price: {s.exit_price} vs {a.exit_price}'
        assert s.close_reason == a.close_reason, \
            f'{ctx} close_reason: {s.close_reason} vs {a.close_reason}'


def assert_portfolio_equal(
    sim_stats: PortfolioStats,
    at_stats: PortfolioStats,
    float_tolerance: float = _DEFAULT_FLOAT_TOL,
) -> None:
    """
    Assert that both pipelines produced identical final portfolio state.

    Checks balance, realized P&L totals, and trade counts.

    Args:
        sim_stats: PortfolioStats from simulation
        at_stats: PortfolioStats from AutoTrader
        float_tolerance: Absolute tolerance for float comparisons
    """
    assert sim_stats.balance == pytest.approx(at_stats.balance, abs=float_tolerance), \
        f'balance: sim={sim_stats.balance}, at={at_stats.balance}'
    assert sim_stats.total_profit == pytest.approx(at_stats.total_profit, abs=float_tolerance), \
        f'total_profit: sim={sim_stats.total_profit}, at={at_stats.total_profit}'
    assert sim_stats.total_loss == pytest.approx(at_stats.total_loss, abs=float_tolerance), \
        f'total_loss: sim={sim_stats.total_loss}, at={at_stats.total_loss}'
    assert sim_stats.total_trades == at_stats.total_trades, \
        f'total_trades: sim={sim_stats.total_trades}, at={at_stats.total_trades}'
