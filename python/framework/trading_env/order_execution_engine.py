# ============================================
# python/framework/trading_env/order_execution_engine.py
# ============================================
"""
FiniexTestingIDE - Order Execution Engine
Deterministic order delay simulation with seeded randomness

MVP Design:
- Tick-based delays (Post-MVP: MS-based)
- Seeded random delays for reproducibility
- PENDING → EXECUTED lifecycle
- Always FILL (no rejections except margin)

Post-MVP Extensions:
- MS-based delays with tick timestamp mapping
- Partial fills with OrderBook integration
- FOK/IOC order types
- Market impact simulation

Architecture:
The OrderExecutionEngine sits between DecisionTradingAPI and Portfolio,
simulating realistic broker execution delays while maintaining deterministic
behavior through seeds for testing reproducibility.

Design Philosophy:
Real brokers have two delay stages:
1. API Latency: Time for order to reach broker (network, API processing)
2. Execution Time: Time for broker to match order internally

We simulate both with seeded random generators to create realistic yet
reproducible order execution patterns.
"""

import random
from dataclasses import dataclass
from typing import Dict, List, Optional


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
# Pending Order Data
# ============================================

@dataclass
class PendingOrder:
    """
    Order waiting to be filled.

    Contains all information needed to execute the order once
    the delay period has elapsed.
    """
    order_id: str
    placed_at_tick: int
    fill_at_tick: int

    # Original order data for execution
    symbol: str
    direction: str  # "BUY" or "SELL"
    lots: float
    order_kwargs: Dict  # stop_loss, take_profit, comment, etc.


# ============================================
# Order Execution Engine
# ============================================

class OrderExecutionEngine:
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

    Usage:
        # In TradeSimulator.__init__
        seeds = config.get('trade_simulator_seeds', {})
        self.execution_engine = OrderExecutionEngine(seeds)

        # When order placed
        order_id = self.execution_engine.submit_order(
            order_data, current_tick
        )

        # Every tick
        filled_orders = self.execution_engine.process_tick(tick_number)
        for order in filled_orders:
            self._fill_order(order)
    """

    def __init__(self, seeds: Dict[str, int]):
        """
        Initialize execution engine with seeds.

        Args:
            seeds: Dictionary with seed values:
                - api_latency_seed: Seed for API delay generator
                - market_execution_seed: Seed for execution delay generator

        Default seeds used if not provided:
            - api_latency_seed: 42
            - market_execution_seed: 123
        """
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
        self.pending_orders: Dict[str, PendingOrder] = {}

        # Order counter for unique IDs
        self._order_counter = 0

    def submit_order(
        self,
        symbol: str,
        direction: str,
        lots: float,
        current_tick: int,
        **kwargs
    ) -> str:
        """
        Submit order for execution with delay.

        Order enters PENDING state and will be filled after
        combined API + execution delay.

        Args:
            symbol: Trading symbol
            direction: "BUY" or "SELL"
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

        # Create unique order ID
        self._order_counter += 1
        order_id = f"ord_{self._order_counter}"

        # Store pending order
        self.pending_orders[order_id] = PendingOrder(
            order_id=order_id,
            placed_at_tick=current_tick,
            fill_at_tick=current_tick + total_delay,
            symbol=symbol,
            direction=direction,
            lots=lots,
            order_kwargs=kwargs
        )

        return order_id

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
        for order_id, pending in self.pending_orders.items():
            if pending.fill_at_tick <= tick_number:
                to_fill.append(pending)
                to_remove.append(order_id)

        # Remove filled orders from pending
        for order_id in to_remove:
            del self.pending_orders[order_id]

        return to_fill

    def get_pending_count(self) -> int:
        """
        Get number of pending orders.

        Useful for debugging and statistics.
        """
        return len(self.pending_orders)

    def clear_pending(self) -> None:
        """
        Clear all pending orders.

        Used when scenario ends to prevent orders from
        previous scenarios leaking into next scenario.
        """
        self.pending_orders.clear()
