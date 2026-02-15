# ============================================
# python/framework/trading_env/order_latency_simulator.py
# ============================================
"""
FiniexTestingIDE - Simulation Latency Manager
Deterministic order delay simulation with seeded randomness.

Extends AbstractPendingOrderManager with tick-based latency modeling:
- Seeded random delays for reproducibility
- PENDING → EXECUTED lifecycle based on tick count
- Support for OPEN and CLOSE orders with same delay system

Architecture:
    AbstractPendingOrderManager  (storage, query, has_pending, is_pending_close)
        │
        └── OrderLatencySimulator  (this class)
            - SeededDelayGenerator for deterministic delays
            - submit_open_order() → store with calculated fill_at_tick
            - submit_close_order() → store with calculated fill_at_tick
            - process_tick() → return orders whose delay has elapsed

Design Philosophy:
Real brokers have two delay stages:
1. API Latency: Time for order to reach broker (network, API processing)
2. Execution Time: Time for broker to match order internally

We simulate both with seeded random generators to create realistic yet
reproducible order execution patterns.

Post-MVP Extensions:
- MS-based delays with tick timestamp mapping
- Seeded error injection (rejections, timeouts) for stress testing
- Partial fills with OrderBook integration
"""

from datetime import datetime, timezone
import random
from typing import Dict, List, Optional

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.trading_env.abstract_pending_order_manager import AbstractPendingOrderManager
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
# Simulation Latency Manager
# ============================================

class OrderLatencySimulator(AbstractPendingOrderManager):
    """
    Manages order lifecycle with deterministic tick-based delays.

    Extends AbstractPendingOrderManager with simulation-specific logic:
    - Seeded delay generators for API latency + market execution
    - Tick-based fill detection (process_tick)

    Inherited from AbstractPendingOrderManager:
    - Pending order storage (_pending_orders dict)
    - Query methods (get_pending_orders, get_pending_count)
    - Convenience checks (has_pending_orders, is_pending_close)
    - Cleanup (clear_pending)
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
        super().__init__(logger)

        # Fill counter for stress test hooks
        self._fill_counter = 0

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

    # ============================================
    # Simulation-Specific: Submit with Delay
    # ============================================

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
            order_id: Unique order identifier
            symbol: Trading symbol
            direction: LONG or SHORT
            lots: Position size
            current_tick: Current tick number
            **kwargs: Additional order parameters (SL, TP, comment)

        Returns:
            order_id: Same as input (for chaining)
        """
        # Generate delays
        api_delay = self.api_delay_gen.next()
        exec_delay = self.exec_delay_gen.next()
        total_delay = api_delay + exec_delay

        # Store pending order (inherited storage)
        self.store_order(PendingOrder(
            pending_order_id=order_id,
            placed_at_tick=current_tick,
            fill_at_tick=current_tick + total_delay,
            order_action=PendingOrderAction.OPEN,
            symbol=symbol,
            direction=direction,
            lots=lots,
            entry_price=0,
            entry_time=datetime.now(timezone.utc),
            order_kwargs=kwargs
        ))

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
            position_id: Same as input (for chaining)
        """
        # Generate delays (same system as open orders)
        api_delay = self.api_delay_gen.next()
        exec_delay = self.exec_delay_gen.next()
        total_delay = api_delay + exec_delay

        # Store pending close order (inherited storage)
        self.store_order(PendingOrder(
            pending_order_id=position_id,
            placed_at_tick=current_tick,
            fill_at_tick=current_tick + total_delay,
            order_action=PendingOrderAction.CLOSE,
            close_lots=close_lots
        ))

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

    # ============================================
    # Simulation-Specific: Tick-Based Fill Detection
    # ============================================

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
            self.remove_order(order_id)

        return to_fill
