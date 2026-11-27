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
            - process_up_to_msc() → return orders whose delay has elapsed by an instant

Design Philosophy:
Fill timing uses inbound latency only (order → broker). The fill price is
determined at broker_fill_msc = placed_at_msc + inbound_delay. This matches
the approach of established frameworks (QuantConnect, Backtrader, Zipline).

Delays are in milliseconds, counted from the instant an order is sent in and compared with the
instant being resolved, both on the arrival axis (#460).
"""

from datetime import datetime
from typing import Dict, List, Optional

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.trading_env.abstract_pending_order_manager import AbstractPendingOrderManager
from python.framework.types.config_types.backtesting_config_types import (
    inbound_latency_range_problem,
)
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason
from python.framework.types.trading_env_types.latency_simulator_types import (
    PendingOrder,
    PendingOrderAction,
    PendingOrderTiming,
)
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderType,
)
from python.framework.types.trading_env_types.submission_metadata_types import SubmissionMetadata
from python.framework.utils.seeded_generators.seeded_delay_generator import SeededDelayGenerator

# ============================================
# Simulation Latency Manager
# ============================================

class OrderLatencySimulator(AbstractPendingOrderManager):
    """
    Manages order lifecycle with deterministic ms-based delays.

    Extends AbstractPendingOrderManager with simulation-specific logic:
    - Seeded delay generators for API latency + market execution (ms)
    - Timestamp-based fill detection (process_up_to_msc compares an instant's msc)

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

        # The configuration refuses such a range before a run starts; built here directly, the
        # simulator refuses it too (#460)
        problem = inbound_latency_range_problem(inbound_latency_min_ms, inbound_latency_max_ms)
        if problem is not None:
            raise ValueError(problem)

        # Create delay generator (ms-based)
        # Inbound latency: order reaches broker
        self._inbound_delay_gen = SeededDelayGenerator(
            seed=inbound_seed,
            min_delay=inbound_latency_min_ms,
            max_delay=inbound_latency_max_ms
        )

    # ============================================
    # Simulation-Specific: Submit with Delay
    # ============================================

    def submit_open_order(
        self,
        order_id: str,
        request: OpenOrderRequest,
        tick: TickData,
        placed_msc: int,
        placed_at: datetime,
    ) -> str:
        """
        Submit OPEN order for execution with delay.

        Order enters PENDING state and will be filled after
        combined API + execution delay (ms).

        Args:
            order_id: Unique order identifier
            request: OpenOrderRequest with all order parameters
            tick: The last tick the sender saw, for the submission's price and venue time
            placed_msc: The instant the order is sent at, on the axis due times are timed on —
                the backtest's arrival axis (#460); the due time is this plus the drawn delay
            placed_at: The same instant on the canonical clock, the order's entry time

        Returns:
            order_id: Same as input (for chaining)
        """
        current_msc = placed_msc

        # Generate delay (ms)
        inbound_delay = self._inbound_delay_gen.next()
        broker_fill_msc = current_msc + inbound_delay

        # Build order_kwargs dict from request
        order_kwargs = {}
        if request.stop_loss is not None:
            order_kwargs['stop_loss'] = request.stop_loss
        if request.take_profit is not None:
            order_kwargs['take_profit'] = request.take_profit
        if request.comment:
            order_kwargs['comment'] = request.comment

        # entry_price: depends on order type
        # LIMIT: limit price (fill price)
        # STOP/STOP_LIMIT: stop_price (trigger price)
        # MARKET: 0 (fill at current tick)
        if request.order_type == OrderType.LIMIT and request.price is not None:
            entry_price = request.price
        elif request.order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and request.stop_price is not None:
            entry_price = request.stop_price
            if request.order_type == OrderType.STOP_LIMIT and request.price is not None:
                order_kwargs['limit_price'] = request.price
        else:
            entry_price = 0

        # Store pending order (inherited storage)
        self.store_order(PendingOrder(
            pending_order_id=order_id,
            timing=PendingOrderTiming(
                placed_at_msc=current_msc,
                broker_fill_msc=broker_fill_msc,
            ),
            order_action=PendingOrderAction.OPEN,
            order_type=request.order_type,
            symbol=request.symbol,
            direction=request.direction,
            lots=request.lots,
            entry_price=entry_price,
            entry_time=placed_at,
            order_kwargs=order_kwargs,
            submission=SubmissionMetadata(
                tick_mid_price=tick.mid,
                tick_time_msc=tick.time_msc,
            ),
        ))

        # Log order reception
        self.logger.info(
            f'📨 Order received: {order_id} ({request.direction.value} {request.lots} lots) '
            f'- inbound: {inbound_delay}ms | tick_msc={current_msc}'
        )

        self.logger.debug(
            f'  placed_at_msc={current_msc}, broker_fill_msc={broker_fill_msc}, '
            f'collected_msc={tick.collected_msc}, time_msc={tick.time_msc}'
        )

        return order_id

    def submit_close_order(
        self,
        position_id: str,
        tick: TickData,
        placed_msc: int,
        symbol: Optional[str] = None,
        close_lots: Optional[float] = None,
        close_reason: Optional[CloseReason] = None,
        position_direction: Optional[OrderDirection] = None,
        exit_level: Optional[float] = None,
    ) -> str:
        """
        Submit CLOSE order for execution with delay.

        Close orders use same delay system as open orders for realism.

        Args:
            position_id: Position to close
            tick: The last tick the sender saw, for the submission's price and venue time
            placed_msc: The instant the close is sent at, on the axis due times are timed on (#460)
            symbol: The position's symbol — carried on the order, so what it ends with still
                names its instrument once the position is gone (#362)
            close_lots: Lots to close (None = close all)
            close_reason: Why the close was requested, or None for a plain strategy
                close — it rides on the PendingOrder to the fill (#500)
            position_direction: The position's direction, carried for the same reason as
                its symbol
            exit_level: The level an own exit fired at (#625), carried like the reason

        Returns:
            position_id: Same as input (for chaining)
        """
        current_msc = placed_msc

        # Generate delay (same system as open orders)
        inbound_delay = self._inbound_delay_gen.next()
        broker_fill_msc = current_msc + inbound_delay

        # Store pending close order (inherited storage)
        self.store_order(PendingOrder(
            pending_order_id=position_id,
            timing=PendingOrderTiming(
                placed_at_msc=current_msc,
                broker_fill_msc=broker_fill_msc,
            ),
            order_action=PendingOrderAction.CLOSE,
            order_type=OrderType.MARKET,
            symbol=symbol,
            closed_position_direction=position_direction,
            close_reason=close_reason,
            exit_level=exit_level,
            close_lots=close_lots,
            submission=SubmissionMetadata(
                tick_mid_price=tick.mid,
                tick_time_msc=tick.time_msc,
            ),
        ))

        # Log close order reception
        self.logger.info(
            f'📨 Close order received: {position_id} '
            f'- inbound: {inbound_delay}ms | tick_msc={current_msc}'
        )

        self.logger.debug(
            f'  placed_at_msc={current_msc}, broker_fill_msc={broker_fill_msc}, '
            f'collected_msc={tick.collected_msc}, time_msc={tick.time_msc}'
        )

        return position_id

    # ============================================
    # Simulation-Specific: Timestamp-Based Fill Detection
    # ============================================

    def process_up_to_msc(self, current_msc: int) -> List[PendingOrder]:
        """
        Resolve and return orders whose broker_fill_msc is at or before current_msc.

        Keyed on an explicit instant, so the backtest's timeline driver (#460) resolves a fill at
        the instant it comes due — between two replayed data ticks too, not only on a tick.

        Args:
            current_msc: The instant to resolve against, on the arrival axis

        Returns:
            List of PendingOrder objects ready to be filled
        """
        if not self._pending_orders:
            return []
        to_fill = []
        to_remove = []

        # Find orders ready to fill
        for order_id, pending in self._pending_orders.items():
            if pending.timing.broker_fill_msc <= current_msc:
                to_fill.append(pending)
                to_remove.append(order_id)

                # Log order ready for fill
                actual_latency = current_msc - pending.timing.placed_at_msc
                self.logger.debug(
                    f'✅ Order ready: {order_id} ({pending.order_action}) '
                    f'- latency: {actual_latency}ms | current_msc={current_msc}, '
                    f'placed_at_msc={pending.timing.placed_at_msc}'
                )

        # Remove filled orders from pending
        for order_id in to_remove:
            self.remove_order(order_id)

        return to_fill

    def peek_next_due_msc(self) -> Optional[int]:
        """
        When the next pending order comes due, resolving nothing (#457).

        So a timeline can ask when the next fill lands without reading this simulator's state.
        The instant lies on the arrival axis the backtest orders by (#460): the instant an order was
        sent at, plus its delay.

        Returns:
            The earliest `broker_fill_msc` among the pending orders, or None when none is pending
        """
        if not self._pending_orders:
            return None
        return min(pending.timing.broker_fill_msc for pending in self._pending_orders.values())
