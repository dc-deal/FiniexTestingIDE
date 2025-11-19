# ============================================
# python/framework/trading_env/reproducible_order_latency_simulator.py
# ============================================
"""
FiniexTestingIDE - Reproducible Order Latency Simulator
Deterministic order delay simulation with seeded randomness

MVP Design:
- Tick-based delays (Post-MVP: MS-based)
- Seeded random delays for reproducibility
- PENDING → EXECUTED lifecycle
- Always FILL (no rejections except margin)
- Support for OPEN and CLOSE orders with same delay system

Post-MVP Extensions:
- MS-based delays with tick timestamp mapping
- Partial fills with OrderBook integration
- FOK/IOC order types
- Market impact simulation

Architecture:
The OrderLatencySimulator sits between DecisionTradingAPI and 
Portfolio, simulating realistic broker execution delays while maintaining 
deterministic behavior through seeds for testing reproducibility.

Design Philosophy:
Real brokers have two delay stages:
1. API Latency: Time for order to reach broker (network, API processing)
2. Execution Time: Time for broker to match order internally

We simulate both with seeded random generators to create realistic yet
reproducible order execution patterns.
"""

from datetime import datetime
import random
from typing import Dict, List, Optional

from python.components.logger.abstract_logger import AbstractLogger
from python.framework.types.latency_simulator_types import PendingOrder, PendingOrderAction
from python.framework.types.order_types import OrderDirection


# ============================================
# Seeded Delay Generator
# ============================================

class SeededDelayGenerator:
    """
    Generate deterministic random delays using seeds.

    Uses Python's random.Random with explicit seed for reproducibility.
    Every run with same seed produces identical delay sequence.

    NOTE: Currently tick-based (ticks to wait).
    Post-MVP: Will be MS-based with tick→timestamp mapping.
    """

    def __init__(self, seed: int, min_delay: int, max_delay: int):
        """
        Initialize delay generator.

        Args:
            seed: Random seed for reproducibility
            min_delay: Minimum delay in ticks (MVP) / ms (Post-MVP)
            max_delay: Maximum delay in ticks (MVP) / ms (Post-MVP)
        """
        self.rng = random.Random(seed)
        self.min_delay = min_delay
        self.max_delay = max_delay

    def next(self) -> int:
        """
        Generate next delay value.

        Returns:
            Random delay between min_delay and max_delay (inclusive)
        """
        return self.rng.randint(self.min_delay, self.max_delay)


# ============================================
# Reproducible Order Latency Simulator
# ============================================

class OrderLatencySimulator:
    """
    Manages order lifecycle with deterministic delays.

    Simulates realistic broker behavior:
    1. Order submitted → PENDING status
    2. API latency delay (order reaches broker)
    3. Market execution delay (broker matches order)
    4. Order filled → EXECUTED status

    All delays are seeded for reproducibility across runs.
    This allows testing strategies with consistent execution
    conditions while maintaining realistic timing behavior.
    """

    def __init__(
        self,
        seeds: Dict[str, int],
        logger: AbstractLogger
    ):
        """
        Initialize latency simulator with seeds and logger.

        Args:
            seeds: Dictionary with seed values:
                - api_latency_seed: Seed for API delay generator
                - market_execution_seed: Seed for execution delay generator
            logger: Logger instance for tracking order flow

        Default seeds used if not provided:
            - api_latency_seed: 42
            - market_execution_seed: 123
        """
        self.logger = logger

        # Extract seeds with defaults
        api_seed = seeds.get('api_latency_seed', 42)
        exec_seed = seeds.get('market_execution_seed', 123)

        # Create delay generators
        # API latency: 1-3 ticks (order reaches broker)
        self.api_delay_gen = SeededDelayGenerator(
            seed=api_seed,
            min_delay=1,
            max_delay=3
        )

        # Market execution: 2-5 ticks (broker matches order)
        self.exec_delay_gen = SeededDelayGenerator(
            seed=exec_seed,
            min_delay=2,
            max_delay=5
        )

        # Pending orders waiting to be filled
        self._pending_orders: Dict[str, PendingOrder] = {}

        # Order counter for unique IDs
        self._order_counter = 0

    def submit_open_order(
        self,
        order_id: str,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        current_tick: int,
        **kwargs
    ) -> str:
        """
        Submit OPEN order for execution with delay.

        Order enters PENDING state and will be filled after
        combined API + execution delay.

        Args:
            symbol: Trading symbol
            direction: "long" or "short"
            lots: Position size
            current_tick: Current tick number
            **kwargs: Additional order parameters (SL, TP, comment)

        Returns:
            order_id: Unique identifier for this order
        """
        # Generate delays
        api_delay = self.api_delay_gen.next()
        exec_delay = self.exec_delay_gen.next()
        total_delay = api_delay + exec_delay

        # Store pending order
        self._pending_orders[order_id] = PendingOrder(
            pending_order_id=order_id,
            placed_at_tick=current_tick,
            fill_at_tick=current_tick + total_delay,
            order_action=PendingOrderAction.OPEN,
            symbol=symbol,
            direction=direction,
            lots=lots,
            entry_price=0,     # ← Store for converter
            entry_time=datetime.now(),       # ← Store for converter
            order_kwargs=kwargs
        )

        # Log order reception
        self.logger.info(
            f"📨 Order received: {order_id} ({direction.value} {lots} lots) "
            f"- latency: {total_delay} ticks"
        )

        self.logger.debug(
            f"  API delay: {api_delay} ticks, Exec delay: {exec_delay} ticks, "
            f"Will fill at tick: {current_tick + total_delay}"
        )

        return order_id

    def submit_close_order(
        self,
        position_id: str,
        current_tick: int,
        close_lots: Optional[float] = None
    ) -> str:
        """
        Submit CLOSE order for execution with delay.

        Close orders use same delay system as open orders for realism.

        Args:
            position_id: Position to close
            current_tick: Current tick number
            close_lots: Lots to close (None = close all)

        Returns:
            order_id: Unique identifier for this close order
        """
        # Generate delays (same system as open orders)
        api_delay = self.api_delay_gen.next()
        exec_delay = self.exec_delay_gen.next()
        total_delay = api_delay + exec_delay

        # Store pending close order
        self._pending_orders[position_id] = PendingOrder(
            pending_order_id=position_id,
            placed_at_tick=current_tick,
            fill_at_tick=current_tick + total_delay,
            order_action=PendingOrderAction.CLOSE,
            close_lots=close_lots
        )

        # Log close order reception
        self.logger.info(
            f"📨 Close order received: {position_id} "
            f"- latency: {total_delay} ticks"
        )

        self.logger.debug(
            f"  API delay: {api_delay} ticks, Exec delay: {exec_delay} ticks, "
            f"Fill at tick: {current_tick + total_delay}"
        )

        return position_id

    def process_tick(self, tick_number: int) -> List[PendingOrder]:
        """
        Process current tick and return orders ready to fill.

        Checks all pending orders and returns those whose
        fill_at_tick has been reached or passed.

        Args:
            tick_number: Current tick number

        Returns:
            List of PendingOrder objects ready to be filled
        """
        to_fill = []
        to_remove = []

        # Find orders ready to fill
        for order_id, pending in self._pending_orders.items():
            if pending.fill_at_tick <= tick_number:
                to_fill.append(pending)
                to_remove.append(order_id)

                # Log order ready for fill
                actual_latency = tick_number - pending.placed_at_tick
                self.logger.debug(
                    f"✅ Order ready: {order_id} ({pending.order_action}) "
                    f"- actual latency: {actual_latency} ticks"
                )

        # Remove filled orders from pending
        for order_id in to_remove:
            del self._pending_orders[order_id]

        return to_fill

    def get_pending_count(self) -> int:
        """
        Get number of pending orders.

        Useful for debugging and statistics.
        """
        return len(self._pending_orders)

    def get_pending_orders(
        self,
        filter_pending_action: Optional[PendingOrderAction] = None
    ) -> List[PendingOrder]:
        """
        Get pending orders, optionally filtered by action type.

        Args:
            filter_pending_action: Optional filter (OPEN or CLOSE)
                                  None returns all pending orders

        Returns:
            List of PendingOrder objects matching the filter

        Example:
            # Get only pending CLOSE orders
            closes = simulator.get_pending_orders(PendingOrderAction.CLOSE)

            # Get all pending orders
            all_pending = simulator.get_pending_orders()
        """
        if filter_pending_action is None:
            return list(self._pending_orders.values())

        return [
            pending for pending in self._pending_orders.values()
            if pending.order_action == filter_pending_action
        ]

    def clear_pending(self) -> None:
        """
        Clear all pending orders.

        Used when scenario ends to prevent orders from
        previous scenarios leaking into next scenario.
        """
        if self._pending_orders:
            count = len(self._pending_orders)
            self.logger.warning(
                f"⚠️ Clearing {count} pending order(s) at scenario end"
            )

        self._pending_orders.clear()
