"""
FiniexTestingIDE - Spot SELL Scenario Tests

End-to-end tests validating that SELL signals on spot markets correctly
execute through the full pipeline (DecisionTradingApi → OrderGuard → executor).

Validates three cases:
1. BUY on spot acquires base currency (baseline)
2. SELL on spot sells held base currency (was blocked before OrderSide refactor)
3. SELL without base balance produces INSUFFICIENT_FUNDS rejection

Uses backtesting_margin_stress decision logic with trade_sequence on kraken_spot.
"""

from typing import List

import pytest

from python.framework.types.trading_env_types.order_types import (
    OrderResult,
    OrderStatus,
    RejectionReason,
)
from python.framework.types.trading_env_types.trading_env_stats_types import ExecutionStats
from tests.shared.fixture_helpers import (
    extract_order_history,
    extract_process_result,
    extract_tick_loop_results,
    run_scenario,
)


SPOT_SELL_CONFIG = 'backtesting/spot_sell_test.json'


@pytest.fixture(scope='module')
def spot_sell_tick_loop():
    """Run the spot sell scenario once for the module."""
    summary = run_scenario(SPOT_SELL_CONFIG)
    process_result = extract_process_result(summary)
    return extract_tick_loop_results(process_result)


@pytest.fixture(scope='module')
def spot_sell_order_history(spot_sell_tick_loop) -> List[OrderResult]:
    return extract_order_history(spot_sell_tick_loop)


@pytest.fixture(scope='module')
def spot_sell_execution_stats(spot_sell_tick_loop) -> ExecutionStats:
    return spot_sell_tick_loop.execution_stats


class TestSpotBuyExecutes:
    """BUY on spot should execute normally (baseline)."""

    def test_buy_order_executed(
        self,
        spot_sell_order_history: List[OrderResult],
    ):
        """The LONG/BUY order must pass guard and executor."""
        executed = [
            r for r in spot_sell_order_history
            if r.status == OrderStatus.EXECUTED
        ]
        assert len(executed) >= 1, (
            'Expected at least 1 executed order in spot mode'
        )


class TestSpotSellWithBalance:
    """SELL on spot with held base currency should execute."""

    def test_sell_order_not_guard_rejected(
        self,
        spot_sell_order_history: List[OrderResult],
    ):
        """SELL (SHORT direction) must NOT be rejected by the guard."""
        guard_rejections = [
            r for r in spot_sell_order_history
            if r.order_id.startswith('guard_')
        ]
        assert len(guard_rejections) == 0, (
            f"Expected no guard rejections on spot, "
            f"got {len(guard_rejections)}: {[r.rejection_reason for r in guard_rejections]}"
        )

    def test_at_least_two_orders_executed(
        self,
        spot_sell_order_history: List[OrderResult],
    ):
        """Both BUY and SELL orders should execute (not just BUY)."""
        executed = [
            r for r in spot_sell_order_history
            if r.status == OrderStatus.EXECUTED
        ]
        assert len(executed) >= 2, (
            f"Expected at least 2 executed orders (BUY + SELL), "
            f"got {len(executed)}"
        )


class TestSpotSellInsufficientBalance:
    """SELL on spot without base balance should produce INSUFFICIENT_FUNDS."""

    def test_insufficient_funds_rejection_present(
        self,
        spot_sell_order_history: List[OrderResult],
    ):
        """SELL with no base balance must be rejected as INSUFFICIENT_FUNDS."""
        insufficient = [
            r for r in spot_sell_order_history
            if r.rejection_reason == RejectionReason.INSUFFICIENT_FUNDS
        ]
        assert len(insufficient) >= 1, (
            f"Expected at least 1 INSUFFICIENT_FUNDS rejection for "
            f"SELL without base balance, got {len(insufficient)}"
        )

    def test_execution_stats_counts_rejection(
        self,
        spot_sell_execution_stats: ExecutionStats,
    ):
        """The INSUFFICIENT_FUNDS rejection must appear in stats."""
        assert spot_sell_execution_stats.orders_rejected >= 1, (
            f"Expected at least 1 rejection in execution_stats, "
            f"got {spot_sell_execution_stats.orders_rejected}"
        )
