# ============================================
# python/framework/trading_env/simulation/order_latency_simulator.py
# ============================================
"""
FiniexTestingIDE - Simulation Latency Manager
Deterministic order delay simulation with seeded randomness.

Extends AbstractPendingOrderManager with ms-timestamp-based latency modeling:
- Seeded random delays (ms) for reproducibility
- PENDING → EXECUTED lifecycle based on tick millisecond timestamps
- Support for OPEN and CLOSE orders with same delay system

Architecture:
    AbstractPendingOrderManager  (storage, query, has_pending, is_pending_close)
        │
        └── OrderLatencySimulator  (this class)
            - SeededDelayGenerator for deterministic ms delays
            - submit_open_order() → store with calculated fill_at_msc
            - submit_close_order() → store with calculated fill_at_msc
            - process_tick() → return orders whose delay has elapsed

Design Philosophy:
Real brokers have two delay stages:
1. API Latency: Time for order to reach broker (network, API processing)
2. Execution Time: Time for broker to match order internally

We simulate both with seeded random generators to create realistic yet
reproducible order execution patterns. Delays are in milliseconds,
fill detection uses tick timestamps (collected_msc or time_msc).
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.trading_env.abstract_pending_order_manager import AbstractPendingOrderManager
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.trading_env_types.latency_simulator_types import PendingOrder, PendingOrderAction
from python.framework.types.trading_env_types.order_types import OpenOrderRequest, OrderDirection, OrderType
from python.framework.utils.seeded_generators.seeded_delay_generator import SeededDelayGenerator


# Module-level flag for one-time fallback warning
_fallback_warned = False


# ============================================
# Simulation Latency Manager
# ============================================

class OrderLatencySimulator(AbstractPendingOrderManager):
    """
    Manages order lifecycle with deterministic ms-based delays.

    Extends AbstractPendingOrderManager with simulation-specific logic:
    - Seeded delay generators for API latency + market execution (ms)
    - Timestamp-based fill detection (process_tick compares tick msc)

    Inherited from AbstractPendingOrderManager:
    - Pending order storage (_pending_orders dict)
    - Query methods (get_pending_orders, get_pending_count)
    - Convenience checks (has_pending_orders, is_pending_close)
    - Cleanup (clear_pending)
    """

    def __init__(
        self,
        seeds: Dict[str, int],
        logger: AbstractLogger,
        api_latency_min_ms: int = 20,
        api_latency_max_ms: int = 80,
        market_execution_min_ms: int = 30,
        market_execution_max_ms: int = 150,
    ):
        """
        Initialize latency simulator with seeds, logger, and ms delay ranges.

        Args:
            seeds: Dictionary with seed values:
                - api_latency_seed: Seed for API delay generator
                - market_execution_seed: Seed for execution delay generator
            logger: Logger instance for tracking order flow
            api_latency_min_ms: Minimum API latency in ms
            api_latency_max_ms: Maximum API latency in ms
            market_execution_min_ms: Minimum market execution time in ms
            market_execution_max_ms: Maximum market execution time in ms
        """
        super().__init__(logger)

        # Extract seeds with defaults
        DEFAULT_API_LATENCY_SEED = 42
        DEFAULT_MARKET_EXECUTION_SEED = 123

        api_seed = seeds.get('api_latency_seed')
        exec_seed = seeds.get('market_execution_seed')

        # Validate and apply defaults with specific warnings
        if api_seed is None:
            api_seed = DEFAULT_API_LATENCY_SEED
            self.logger.warning(
                f"⚠️ Missing 'api_latency_seed' in config - "
                f"using default: {DEFAULT_API_LATENCY_SEED}"
            )

        if exec_seed is None:
            exec_seed = DEFAULT_MARKET_EXECUTION_SEED
            self.logger.warning(
                f"⚠️ Missing 'market_execution_seed' in config - "
                f"using default: {DEFAULT_MARKET_EXECUTION_SEED}"
            )

        # Create delay generators (ms-based)
        # API latency: order reaches broker
        self.api_delay_gen = SeededDelayGenerator(
            seed=api_seed,
            min_delay=api_latency_min_ms,
            max_delay=api_latency_max_ms
        )

        # Market execution: broker matches order
        self.exec_delay_gen = SeededDelayGenerator(
            seed=exec_seed,
            min_delay=market_execution_min_ms,
            max_delay=market_execution_max_ms
        )

    # ============================================
    # Timestamp Extraction
    # ============================================

    @staticmethod
    def _get_tick_msc(tick: TickData) -> int:
        """
        Extract millisecond timestamp from tick.

        Prefers collected_msc (device-side, monotonic) over time_msc (broker-side).

        Args:
            tick: Current tick data

        Returns:
            Millisecond timestamp
        """
        global _fallback_warned

        if tick.collected_msc > 0:
            return tick.collected_msc

        if not _fallback_warned:
            logging.getLogger(__name__).warning(
                f"⚠️ collected_msc=0 — falling back to time_msc for latency timing"
            )
            _fallback_warned = True

        return tick.time_msc

    # ============================================
    # Simulation-Specific: Submit with Delay
    # ============================================

    def submit_open_order(
        self,
        order_id: str,
        request: OpenOrderRequest,
        tick: TickData,
    ) -> str:
        """
        Submit OPEN order for execution with delay.

        Order enters PENDING state and will be filled after
        combined API + execution delay (ms).

        Args:
            order_id: Unique order identifier
            request: OpenOrderRequest with all order parameters
            tick: Current tick data (for timestamp extraction)

        Returns:
            order_id: Same as input (for chaining)
        """
        current_msc = self._get_tick_msc(tick)

        # Generate delays (ms)
        api_delay = self.api_delay_gen.next()
        exec_delay = self.exec_delay_gen.next()
        total_delay = api_delay + exec_delay
        fill_at_msc = current_msc + total_delay

        # Build order_kwargs dict from request
        order_kwargs = {}
        if request.stop_loss is not None:
            order_kwargs["stop_loss"] = request.stop_loss
        if request.take_profit is not None:
            order_kwargs["take_profit"] = request.take_profit
        if request.comment:
            order_kwargs["comment"] = request.comment

        # entry_price: depends on order type
        # LIMIT: limit price (fill price)
        # STOP/STOP_LIMIT: stop_price (trigger price)
        # MARKET: 0 (fill at current tick)
        if request.order_type == OrderType.LIMIT and request.price is not None:
            entry_price = request.price
        elif request.order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and request.stop_price is not None:
            entry_price = request.stop_price
            if request.order_type == OrderType.STOP_LIMIT and request.price is not None:
                order_kwargs["limit_price"] = request.price
        else:
            entry_price = 0

        # Store pending order (inherited storage)
        self.store_order(PendingOrder(
            pending_order_id=order_id,
            placed_at_msc=current_msc,
            fill_at_msc=fill_at_msc,
            order_action=PendingOrderAction.OPEN,
            order_type=request.order_type,
            symbol=request.symbol,
            direction=request.direction,
            lots=request.lots,
            entry_price=entry_price,
            entry_time=datetime.now(timezone.utc),
            order_kwargs=order_kwargs
        ))

        # Log order reception
        self.logger.info(
            f"📨 Order received: {order_id} ({request.direction.value} {request.lots} lots) "
            f"- latency: {total_delay}ms (api:{api_delay}ms + exec:{exec_delay}ms) | tick_msc={current_msc}"
        )

        self.logger.debug(
            f"  placed_at_msc={current_msc}, fill_at_msc={fill_at_msc}, "
            f"collected_msc={tick.collected_msc}, time_msc={tick.time_msc}"
        )

        return order_id

    def submit_close_order(
        self,
        position_id: str,
        tick: TickData,
        close_lots: Optional[float] = None
    ) -> str:
        """
        Submit CLOSE order for execution with delay.

        Close orders use same delay system as open orders for realism.

        Args:
            position_id: Position to close
            tick: Current tick data (for timestamp extraction)
            close_lots: Lots to close (None = close all)

        Returns:
            position_id: Same as input (for chaining)
        """
        current_msc = self._get_tick_msc(tick)

        # Generate delays (same system as open orders)
        api_delay = self.api_delay_gen.next()
        exec_delay = self.exec_delay_gen.next()
        total_delay = api_delay + exec_delay
        fill_at_msc = current_msc + total_delay

        # Store pending close order (inherited storage)
        self.store_order(PendingOrder(
            pending_order_id=position_id,
            placed_at_msc=current_msc,
            fill_at_msc=fill_at_msc,
            order_action=PendingOrderAction.CLOSE,
            close_lots=close_lots
        ))

        # Log close order reception
        self.logger.info(
            f"📨 Close order received: {position_id} "
            f"- latency: {total_delay}ms (api:{api_delay}ms + exec:{exec_delay}ms) | tick_msc={current_msc}"
        )

        self.logger.debug(
            f"  placed_at_msc={current_msc}, fill_at_msc={fill_at_msc}, "
            f"collected_msc={tick.collected_msc}, time_msc={tick.time_msc}"
        )

        return position_id

    # ============================================
    # Simulation-Specific: Timestamp-Based Fill Detection
    # ============================================

    def process_tick(self, tick: TickData) -> List[PendingOrder]:
        """
        Process current tick and return orders ready to fill.

        Checks all pending orders and returns those whose
        fill_at_msc has been reached or passed by the current tick timestamp.

        Args:
            tick: Current tick data

        Returns:
            List of PendingOrder objects ready to be filled
        """
        current_msc = self._get_tick_msc(tick)

        to_fill = []
        to_remove = []

        # Find orders ready to fill
        for order_id, pending in self._pending_orders.items():
            if pending.fill_at_msc <= current_msc:
                to_fill.append(pending)
                to_remove.append(order_id)

                # Log order ready for fill
                actual_latency = current_msc - pending.placed_at_msc
                self.logger.debug(
                    f"✅ Order ready: {order_id} ({pending.order_action}) "
                    f"- latency: {actual_latency}ms | current_msc={current_msc}, "
                    f"placed_at_msc={pending.placed_at_msc}"
                )

        # Remove filled orders from pending
        for order_id in to_remove:
            self.remove_order(order_id)

        return to_fill
