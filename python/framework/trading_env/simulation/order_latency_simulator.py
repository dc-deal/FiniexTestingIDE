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
            - submit_open_order() → store with calculated broker_fill_msc
            - submit_close_order() → store with calculated broker_fill_msc
            - process_tick() → return orders whose delay has elapsed

Design Philosophy:
Fill timing uses inbound latency only (order → broker). The fill price is
determined at broker_fill_msc = placed_at_msc + inbound_delay. This matches
the approach of established frameworks (QuantConnect, Backtrader, Zipline).

Delays are in milliseconds, fill detection uses tick timestamps
(collected_msc or time_msc).
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
        inbound_latency_min_ms: int = 20,
        inbound_latency_max_ms: int = 80,
    ):
        """
        Initialize latency simulator with seeds, logger, and ms delay ranges.

        Args:
            seeds: Dictionary with seed values:
                - inbound_latency_seed: Seed for inbound delay generator
            logger: Logger instance for tracking order flow
            inbound_latency_min_ms: Minimum inbound latency in ms (order → broker)
            inbound_latency_max_ms: Maximum inbound latency in ms (order → broker)
        """
        super().__init__(logger)

        # Extract seeds with defaults
        DEFAULT_INBOUND_LATENCY_SEED = 42

        inbound_seed = seeds.get('inbound_latency_seed')

        # Validate and apply defaults with specific warnings
        if inbound_seed is None:
            inbound_seed = DEFAULT_INBOUND_LATENCY_SEED
            self.logger.warning(
                f"⚠️ Missing 'inbound_latency_seed' in config - "
                f"using default: {DEFAULT_INBOUND_LATENCY_SEED}"
            )

        # Create delay generator (ms-based)
        # Inbound latency: order reaches broker
        self._inbound_delay_gen = SeededDelayGenerator(
            seed=inbound_seed,
            min_delay=inbound_latency_min_ms,
            max_delay=inbound_latency_max_ms
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

        # Generate delay (ms)
        inbound_delay = self._inbound_delay_gen.next()
        broker_fill_msc = current_msc + inbound_delay

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
            broker_fill_msc=broker_fill_msc,
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
            f"- inbound: {inbound_delay}ms | tick_msc={current_msc}"
        )

        self.logger.debug(
            f"  placed_at_msc={current_msc}, broker_fill_msc={broker_fill_msc}, "
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

        # Generate delay (same system as open orders)
        inbound_delay = self._inbound_delay_gen.next()
        broker_fill_msc = current_msc + inbound_delay

        # Store pending close order (inherited storage)
        self.store_order(PendingOrder(
            pending_order_id=position_id,
            placed_at_msc=current_msc,
            broker_fill_msc=broker_fill_msc,
            order_action=PendingOrderAction.CLOSE,
            close_lots=close_lots
        ))

        # Log close order reception
        self.logger.info(
            f"📨 Close order received: {position_id} "
            f"- inbound: {inbound_delay}ms | tick_msc={current_msc}"
        )

        self.logger.debug(
            f"  placed_at_msc={current_msc}, broker_fill_msc={broker_fill_msc}, "
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
        broker_fill_msc has been reached or passed by the current tick timestamp.

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
            if pending.broker_fill_msc <= current_msc:
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
