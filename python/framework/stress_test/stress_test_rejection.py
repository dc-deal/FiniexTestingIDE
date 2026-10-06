# ============================================
# python/framework/stress_test/stress_test_rejection.py
# ============================================
"""
FiniexTestingIDE - Stress Test: Order Rejection
Seeded probability-based order rejection for stress testing.

Replaces the former hardcoded module-constant approach with
config-driven, per-scenario rejection injection.
"""

from datetime import datetime
from typing import Optional

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.types.trading_env_types.latency_simulator_types import PendingOrder
from python.framework.types.trading_env_types.order_types import (
    OrderAction,
    OrderResult,
    OrderStatus,
    OrderType,
    RejectionReason,
    create_refusal_result,
)
from python.framework.types.trading_env_types.stress_test_types import StressTestRejectOrderConfig
from python.framework.utils.seeded_generators.seeded_probability_filter import (
    SeededProbabilityFilter,
)


class StressTestRejection:
    """
    Stress test module for probabilistic order rejection.

    Uses SeededProbabilityFilter for deterministic, reproducible rejection
    patterns. Same seed + same order sequence = identical rejections.

    Args:
        config: Rejection stress test configuration
        logger: Logger instance
    """

    def __init__(self, config: StressTestRejectOrderConfig, logger: AbstractLogger):
        self._config = config
        self._logger = logger
        self._rejection_count: int = 0

        if config.enabled:
            self._filter = SeededProbabilityFilter(
                seed=config.seed,
                probability=config.probability
            )
            logger.info(
                f'[STRESS TEST] Order rejection enabled — '
                f'probability: {config.probability:.0%}, seed: {config.seed}'
            )
        else:
            self._filter = None

    def should_reject(self, pending_order: PendingOrder, now: datetime) -> Optional[OrderResult]:
        """
        Check if this order should be rejected by the stress test.

        Applied to OPEN orders only — the simulation calls it from the open branch of its
        fill path — so the rejection states the open side.

        Args:
            pending_order: The pending order to evaluate
            now: The canonical clock at the refusal

        Returns:
            OrderResult rejection if triggered, None otherwise
        """
        if self._filter is None:
            return None

        if not self._filter.should_trigger():
            return None

        self._rejection_count += 1

        rejection = create_refusal_result(
            order_id=pending_order.pending_order_id,
            reason=RejectionReason.BROKER_ERROR,
            message=f'[STRESS TEST] Seeded rejection #{self._rejection_count} '
            f'(probability: {self._config.probability:.0%})',
            status=OrderStatus.REJECTED,
            execution_time=now,
            action=OrderAction.OPEN,
            symbol=pending_order.symbol,
            direction=pending_order.direction,
            requested_lots=pending_order.lots,
            order_type=pending_order.order_type or OrderType.MARKET,
        )

        self._logger.warning(
            f'[STRESS TEST] Order {pending_order.pending_order_id} rejected '
            f'(#{self._rejection_count}, probability: {self._config.probability:.0%})'
        )

        return rejection

