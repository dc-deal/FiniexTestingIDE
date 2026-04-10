"""
FiniexTestingIDE - OrderGuard Scenario Tests

End-to-end integration tests that run full backtesting scenarios through
the DecisionTradingApi → OrderGuard → TradeSimulator pipeline.

Scenario:
- order_guard_cooldown_test: Consecutive INSUFFICIENT_MARGIN → REJECTION_COOLDOWN

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
