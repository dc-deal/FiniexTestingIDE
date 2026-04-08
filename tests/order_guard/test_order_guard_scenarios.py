"""
FiniexTestingIDE - OrderGuard Scenario Tests

End-to-end integration tests that run full backtesting scenarios through
the DecisionTradingApi → OrderGuard → TradeSimulator pipeline.

Two scenarios:
- order_guard_spot_short_test: SHORT orders in SPOT mode → SPOT_SHORT_BLOCKED
- order_guard_cooldown_test:    Consecutive INSUFFICIENT_MARGIN → REJECTION_COOLDOWN

Unlike the unit tests in test_order_guard_unit.py, these verify that guard
rejections correctly flow into _order_history and execution_stats.orders_rejected
via the record_guard_rejection() hook on AbstractTradeExecutor.
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


# =============================================================================
# SPOT + SHORT SCENARIO
# =============================================================================

SPOT_SHORT_CONFIG = 'backtesting/order_guard_spot_short_test.json'


@pytest.fixture(scope='module')
def spot_short_tick_loop():
    """Run the SHORT+SPOT scenario once for the module."""
    summary = run_scenario(SPOT_SHORT_CONFIG)
    process_result = extract_process_result(summary)
    return extract_tick_loop_results(process_result)


@pytest.fixture(scope='module')
def spot_short_order_history(spot_short_tick_loop) -> List[OrderResult]:
    return extract_order_history(spot_short_tick_loop)


@pytest.fixture(scope='module')
def spot_short_execution_stats(spot_short_tick_loop) -> ExecutionStats:
    return spot_short_tick_loop.execution_stats


class TestSpotShortBlocked:
    """SHORT orders must be blocked by OrderGuard when trading_model == SPOT."""

    def test_short_rejections_in_order_history(
        self,
        spot_short_order_history: List[OrderResult],
    ):
        """All SHORT attempts should appear as SPOT_SHORT_BLOCKED rejections."""
        guard_rejections = [
            r for r in spot_short_order_history
            if r.rejection_reason == RejectionReason.SPOT_SHORT_BLOCKED
        ]
        assert len(guard_rejections) >= 2, (
            f"Expected at least 2 SPOT_SHORT_BLOCKED rejections, "
            f"got {len(guard_rejections)}"
        )

    def test_guard_rejections_have_guard_prefix(
        self,
        spot_short_order_history: List[OrderResult],
    ):
        """Guard rejections must use the 'guard_' order_id prefix."""
        for result in spot_short_order_history:
            if result.rejection_reason == RejectionReason.SPOT_SHORT_BLOCKED:
                assert result.order_id.startswith('guard_'), (
                    f"Guard rejection has wrong id prefix: {result.order_id}"
                )

    def test_long_order_executed_in_spot(
        self,
        spot_short_order_history: List[OrderResult],
    ):
        """The LONG order must pass the guard and execute normally."""
        executed = [
            r for r in spot_short_order_history
            if r.status == OrderStatus.EXECUTED
        ]
        assert len(executed) >= 1, (
            'Expected at least 1 executed LONG order in spot mode'
        )

    def test_execution_stats_counts_guard_rejections(
        self,
        spot_short_execution_stats: ExecutionStats,
    ):
        """Guard rejections must be reflected in orders_rejected counter."""
        assert spot_short_execution_stats.orders_rejected >= 2, (
            f"Expected >= 2 rejections in execution_stats, "
            f"got {spot_short_execution_stats.orders_rejected}"
        )


# =============================================================================
# COOLDOWN SCENARIO
# =============================================================================

COOLDOWN_CONFIG = 'backtesting/order_guard_cooldown_test.json'


@pytest.fixture(scope='module')
def cooldown_tick_loop():
    """Run the cooldown scenario once for the module."""
    summary = run_scenario(COOLDOWN_CONFIG)
    process_result = extract_process_result(summary)
    return extract_tick_loop_results(process_result)


@pytest.fixture(scope='module')
def cooldown_order_history(cooldown_tick_loop) -> List[OrderResult]:
    return extract_order_history(cooldown_tick_loop)


@pytest.fixture(scope='module')
def cooldown_execution_stats(cooldown_tick_loop) -> ExecutionStats:
    return cooldown_tick_loop.execution_stats


class TestRejectionCooldown:
    """Consecutive broker rejections must arm the guard cooldown."""

    def test_margin_rejection_arms_cooldown(
        self,
        cooldown_order_history: List[OrderResult],
    ):
        """At least one INSUFFICIENT_MARGIN rejection must arm the cooldown."""
        margin_rejections = [
            r for r in cooldown_order_history
            if r.rejection_reason == RejectionReason.INSUFFICIENT_MARGIN
        ]
        assert len(margin_rejections) >= 1, (
            f"Expected at least 1 INSUFFICIENT_MARGIN rejection to arm "
            f"cooldown (threshold=1), got {len(margin_rejections)}"
        )

    def test_cooldown_rejection_present(
        self,
        cooldown_order_history: List[OrderResult],
    ):
        """Third attempt must be blocked by the guard as REJECTION_COOLDOWN."""
        cooldown_rejections = [
            r for r in cooldown_order_history
            if r.rejection_reason == RejectionReason.REJECTION_COOLDOWN
        ]
        assert len(cooldown_rejections) >= 1, (
            f"Expected at least 1 REJECTION_COOLDOWN rejection, "
            f"got {len(cooldown_rejections)}"
        )

    def test_cooldown_rejection_is_guard_sourced(
        self,
        cooldown_order_history: List[OrderResult],
    ):
        """REJECTION_COOLDOWN results must carry the guard_ order_id prefix."""
        for result in cooldown_order_history:
            if result.rejection_reason == RejectionReason.REJECTION_COOLDOWN:
                assert result.order_id.startswith('guard_'), (
                    f"Cooldown rejection has wrong id prefix: {result.order_id}"
                )

    def test_execution_stats_counts_all_rejections(
        self,
        cooldown_execution_stats: ExecutionStats,
    ):
        """orders_rejected must include both broker and guard rejections."""
        assert cooldown_execution_stats.orders_rejected >= 2, (
            f"Expected >= 2 rejections (1 broker + 1 guard), "
            f"got {cooldown_execution_stats.orders_rejected}"
        )
