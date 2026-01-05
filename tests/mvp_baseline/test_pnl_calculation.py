"""
FiniexTestingIDE - P&L Calculation Tests
Validates profit/loss calculations against independently computed values

Tests:
- Individual trade P&L calculation
- Total P&L matches portfolio stats
- Spread cost calculation
- Fill tick determination via seeded delays
"""

import pytest
from typing import Dict, List, Any
from dataclasses import dataclass

import pandas as pd

from python.framework.types.portfolio_aggregation_types import PortfolioStats
from python.framework.trading_env.order_latency_simulator import SeededDelayGenerator


@dataclass
class ExpectedTrade:
    """Expected trade with calculated fill ticks and P&L."""
    signal_tick: int
    direction: str
    lot_size: float
    hold_ticks: int
    open_fill_tick: int
    close_fill_tick: int
    entry_price: float
    exit_price: float
    spread_at_entry: float
    gross_pnl: float
    spread_cost: float
    net_pnl: float


def calculate_tick_value(
    account_currency: str,
    base_currency: str,
    quote_currency: str,
    current_price: float
) -> float:
    """
    Calculate tick value based on account currency.

    Args:
        account_currency: Account denomination (e.g., 'USD', 'JPY')
        base_currency: Symbol base currency (e.g., 'GBP' for GBPUSD)
        quote_currency: Symbol quote currency (e.g., 'USD' for GBPUSD)
        current_price: Current mid price

    Returns:
        Tick value multiplier for P&L calculation
    """
    if account_currency == quote_currency:
        return 1.0
    elif account_currency == base_currency:
        return 1.0 / current_price if current_price > 0 else 1.0
    else:
        return 1.0


def build_expected_trades(
    trade_sequence: List[Dict[str, Any]],
    seeds_config: Dict[str, int],
    ticks_df: pd.DataFrame,
    broker_spec: Dict[str, Any],
    account_currency: str
) -> List[ExpectedTrade]:
    """
    Build expected trades with calculated fill ticks and P&L.

    CRITICAL: Delay generators called in sequence:
    Trade 1 OPEN → Trade 1 CLOSE → Trade 2 OPEN → Trade 2 CLOSE → ...

    Args:
        trade_sequence: Trade specifications from config
        seeds_config: Seeds for delay generators
        ticks_df: DataFrame with tick data
        broker_spec: Broker symbol specification
        account_currency: Account currency

    Returns:
        List of ExpectedTrade with all calculations
    """
    api_gen = SeededDelayGenerator(
        seed=seeds_config['api_latency_seed'],
        min_delay=1,
        max_delay=3
    )
    exec_gen = SeededDelayGenerator(
        seed=seeds_config['market_execution_seed'],
        min_delay=2,
        max_delay=5
    )

    digits = broker_spec['digits']
    contract_size = broker_spec['contract_size']
    base_currency = broker_spec['base_currency']
    quote_currency = broker_spec['quote_currency']

    expected_trades = []

    for trade in trade_sequence:
        signal_tick = trade['tick_number']
        direction = trade['direction']
        lot_size = trade['lot_size']
        hold_ticks = trade['hold_ticks']

        # OPEN delays
        open_api_delay = api_gen.next()
        open_exec_delay = exec_gen.next()
        open_fill_tick = signal_tick + open_api_delay + open_exec_delay

        # CLOSE delays
        close_signal_tick = signal_tick + hold_ticks
        close_api_delay = api_gen.next()
        close_exec_delay = exec_gen.next()
        close_fill_tick = close_signal_tick + close_api_delay + close_exec_delay

        # Bounds check
        if open_fill_tick >= len(ticks_df) or close_fill_tick >= len(ticks_df):
            continue

        # Get tick data
        entry_tick = ticks_df.iloc[open_fill_tick]
        exit_tick = ticks_df.iloc[close_fill_tick]

        # Entry/Exit prices based on direction
        if direction == 'LONG':
            entry_price = float(entry_tick['ask'])
            exit_price = float(exit_tick['bid'])
        else:  # SHORT
            entry_price = float(entry_tick['bid'])
            exit_price = float(exit_tick['ask'])

        # Spread at entry
        spread_at_entry = float(entry_tick['ask']) - float(entry_tick['bid'])

        # Calculate tick value
        mid_price = (entry_price + exit_price) / 2.0
        tick_value = calculate_tick_value(
            account_currency, base_currency, quote_currency, mid_price
        )

        # P&L calculation
        if direction == 'LONG':
            price_diff = exit_price - entry_price
        else:
            price_diff = entry_price - exit_price

        points = price_diff * (10 ** digits)
        gross_pnl = points * tick_value * lot_size

        # Spread cost (charged at entry)
        spread_points = spread_at_entry * (10 ** digits)
        spread_cost = spread_points * tick_value * lot_size

        net_pnl = gross_pnl - spread_cost

        expected_trades.append(ExpectedTrade(
            signal_tick=signal_tick,
            direction=direction,
            lot_size=lot_size,
            hold_ticks=hold_ticks,
            open_fill_tick=open_fill_tick,
            close_fill_tick=close_fill_tick,
            entry_price=entry_price,
            exit_price=exit_price,
            spread_at_entry=spread_at_entry,
            gross_pnl=gross_pnl,
            spread_cost=spread_cost,
            net_pnl=net_pnl
        ))

    return expected_trades


class TestPnLCalculation:
    """Tests for P&L calculation validation."""

    @pytest.fixture(scope="class")
    def expected_trades(
        self,
        trade_sequence: list,
        seeds_config: Dict[str, int],
        tick_dataframe: pd.DataFrame,
        broker_symbol_spec: Dict[str, Any],
        account_currency: str
    ) -> List[ExpectedTrade]:
        """
        Build expected trades once per test class.

        Args:
            trade_sequence: Trade specs from config
            seeds_config: Delay seeds
            tick_dataframe: Tick data
            broker_symbol_spec: Broker specs
            account_currency: Account currency

        Returns:
            List of ExpectedTrade
        """
        return build_expected_trades(
            trade_sequence,
            seeds_config,
            tick_dataframe,
            broker_symbol_spec,
            account_currency
        )

    def test_trade_count_matches(
        self,
        expected_trades: List[ExpectedTrade],
        portfolio_stats: PortfolioStats
    ):
        """Expected trade count should match portfolio total trades."""
        assert len(expected_trades) == portfolio_stats.total_trades, (
            f"Expected {len(expected_trades)} trades, "
            f"portfolio has {portfolio_stats.total_trades}"
        )

    def test_total_pnl_matches_portfolio(
        self,
        expected_trades: List[ExpectedTrade],
        portfolio_stats: PortfolioStats
    ):
        """Sum of individual trade P&L should match portfolio total."""
        expected_total = sum(t.net_pnl for t in expected_trades)
        actual_total = portfolio_stats.total_profit - portfolio_stats.total_loss

        tolerance = 0.01  # 1 cent tolerance for floating point
        assert abs(expected_total - actual_total) < tolerance, (
            f"Expected total P&L: {expected_total:.4f}, "
            f"Portfolio P&L: {actual_total:.4f}, "
            f"Diff: {abs(expected_total - actual_total):.6f}"
        )

    def test_total_spread_cost_matches(
        self,
        expected_trades: List[ExpectedTrade],
        portfolio_stats: PortfolioStats
    ):
        """Sum of spread costs should match portfolio spread cost."""
        expected_spread = sum(t.spread_cost for t in expected_trades)
        actual_spread = portfolio_stats.total_spread_cost

        tolerance = 0.01
        assert abs(expected_spread - actual_spread) < tolerance, (
            f"Expected spread: {expected_spread:.4f}, "
            f"Portfolio spread: {actual_spread:.4f}"
        )

    def test_fill_ticks_within_bounds(
        self,
        expected_trades: List[ExpectedTrade],
        tick_dataframe: pd.DataFrame
    ):
        """All fill ticks should be within tick data bounds."""
        max_tick = len(tick_dataframe) - 1

        for i, trade in enumerate(expected_trades):
            assert trade.open_fill_tick <= max_tick, (
                f"Trade {i}: open_fill_tick {trade.open_fill_tick} > max {max_tick}"
            )
            assert trade.close_fill_tick <= max_tick, (
                f"Trade {i}: close_fill_tick {trade.close_fill_tick} > max {max_tick}"
            )

    def test_fill_tick_after_signal(
        self,
        expected_trades: List[ExpectedTrade]
    ):
        """Fill tick should be after signal tick (due to delays)."""
        for i, trade in enumerate(expected_trades):
            assert trade.open_fill_tick > trade.signal_tick, (
                f"Trade {i}: fill {trade.open_fill_tick} not > signal {trade.signal_tick}"
            )

    def test_close_fill_after_open_fill(
        self,
        expected_trades: List[ExpectedTrade]
    ):
        """Close fill tick should be after open fill tick."""
        for i, trade in enumerate(expected_trades):
            assert trade.close_fill_tick > trade.open_fill_tick, (
                f"Trade {i}: close {trade.close_fill_tick} not > open {trade.open_fill_tick}"
            )

    def test_spread_positive(
        self,
        expected_trades: List[ExpectedTrade]
    ):
        """Spread at entry should always be positive."""
        for i, trade in enumerate(expected_trades):
            assert trade.spread_at_entry > 0, (
                f"Trade {i}: spread {trade.spread_at_entry} not positive"
            )

    def test_spread_cost_positive(
        self,
        expected_trades: List[ExpectedTrade]
    ):
        """Spread cost should always be positive."""
        for i, trade in enumerate(expected_trades):
            assert trade.spread_cost > 0, (
                f"Trade {i}: spread_cost {trade.spread_cost} not positive"
            )

    def test_winning_losing_count(
        self,
        expected_trades: List[ExpectedTrade],
        portfolio_stats: PortfolioStats
    ):
        """Winning/losing trade counts should match."""
        expected_winners = sum(1 for t in expected_trades if t.net_pnl > 0)
        expected_losers = sum(1 for t in expected_trades if t.net_pnl <= 0)

        assert expected_winners == portfolio_stats.winning_trades, (
            f"Expected {expected_winners} winners, got {portfolio_stats.winning_trades}"
        )
        assert expected_losers == portfolio_stats.losing_trades, (
            f"Expected {expected_losers} losers, got {portfolio_stats.losing_trades}"
        )

    def test_individual_trade_direction(
        self,
        expected_trades: List[ExpectedTrade],
        portfolio_stats: PortfolioStats
    ):
        """Long/short trade counts should match."""
        expected_long = sum(
            1 for t in expected_trades if t.direction == 'LONG')
        expected_short = sum(
            1 for t in expected_trades if t.direction == 'SHORT')

        assert expected_long == portfolio_stats.total_long_trades, (
            f"Expected {expected_long} long, got {portfolio_stats.total_long_trades}"
        )
        assert expected_short == portfolio_stats.total_short_trades, (
            f"Expected {expected_short} short, got {portfolio_stats.total_short_trades}"
        )


class TestDelaySequenceDeterminism:
    """Tests for delay sequence determinism in P&L context."""

    def test_delay_sequence_reproducible(
        self,
        seeds_config: Dict[str, int],
        trade_sequence: list
    ):
        """Same seeds should produce identical fill tick sequences."""
        def compute_fill_ticks(api_seed: int, exec_seed: int) -> List[tuple]:
            api_gen = SeededDelayGenerator(api_seed, 1, 3)
            exec_gen = SeededDelayGenerator(exec_seed, 2, 5)

            fills = []
            for trade in trade_sequence:
                signal = trade['tick_number']
                hold = trade['hold_ticks']

                # OPEN
                open_fill = signal + api_gen.next() + exec_gen.next()
                # CLOSE
                close_fill = signal + hold + api_gen.next() + exec_gen.next()

                fills.append((open_fill, close_fill))

            return fills

        fills1 = compute_fill_ticks(
            seeds_config['api_latency_seed'],
            seeds_config['market_execution_seed']
        )
        fills2 = compute_fill_ticks(
            seeds_config['api_latency_seed'],
            seeds_config['market_execution_seed']
        )

        assert fills1 == fills2, "Fill tick sequences not reproducible"

    def test_different_seeds_different_fills(
        self,
        seeds_config: Dict[str, int],
        trade_sequence: list
    ):
        """Different seeds should produce different fill sequences."""
        def compute_first_fill(api_seed: int, exec_seed: int) -> int:
            api_gen = SeededDelayGenerator(api_seed, 1, 3)
            exec_gen = SeededDelayGenerator(exec_seed, 2, 5)

            signal = trade_sequence[0]['tick_number']
            return signal + api_gen.next() + exec_gen.next()

        fill1 = compute_first_fill(
            seeds_config['api_latency_seed'],
            seeds_config['market_execution_seed']
        )
        fill2 = compute_first_fill(
            seeds_config['api_latency_seed'] + 1000,
            seeds_config['market_execution_seed'] + 1000
        )

        assert fill1 != fill2, "Different seeds produced same fill tick"
