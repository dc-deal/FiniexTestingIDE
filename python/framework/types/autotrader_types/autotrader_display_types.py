"""
FiniexTestingIDE - AutoTrader Display Types
Data structures for live console dashboard (#228).

Lightweight snapshots pushed via queue from tick loop to display thread.
Designed for queue transport: all fields are primitives, lists, or dicts.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List

from python.framework.types.decision_logic_types import DecisionLogicAction
from python.framework.types.parameter_types import OutputValue
from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason
from python.framework.types.trading_env_types.order_types import OrderDirection
from python.framework.types.trading_env_types.pending_order_stats_types import ActiveOrderSnapshot


@dataclass
class PositionSnapshot:
    """
    Lightweight open position state for display queue transport.

    Built from Position objects in _build_display_stats().
    Contains only the fields needed for display rendering.

    Args:
        position_id: Position identifier
        symbol: Trading symbol
        direction: Order direction (LONG or SHORT)
        lots: Position size
        entry_price: Entry price
        unrealized_pnl: Current unrealized P&L
    """
    position_id: str
    symbol: str
    direction: OrderDirection
    lots: float
    entry_price: float
    unrealized_pnl: float


@dataclass
class TradeHistoryEntry:
    """
    Completed trade summary for display.

    Built from TradeRecord objects. Shows last N trades in dashboard.

    Args:
        trade_id: Position ID of the completed trade
        symbol: Trading symbol
        direction: Order direction (LONG or SHORT)
        lots: Trade size
        entry_price: Entry price
        exit_price: Exit price
        net_pnl: Net P&L after fees
        close_reason: How the trade was closed
    """
    trade_id: str
    symbol: str
    direction: OrderDirection
    lots: float
    entry_price: float
    exit_price: float
    net_pnl: float
    close_reason: CloseReason


@dataclass
class AutoTraderDisplayStats:
    """
    Stats snapshot pushed to display queue after each tick.

    Built by AutotraderTickLoop._build_display_stats().
    Consumed by AutoTraderLiveDisplay for rendering.

    All fields are primitives or lists of dataclasses for
    lightweight queue transport and future JSON serialization.

    Args:
        session_start: Session start time (UTC)
        dry_run: Whether this is a dry-run session
        symbol: Trading symbol
        broker_type: Broker identifier
        ticks_processed: Total ticks processed so far
        balance: Current account balance
        initial_balance: Starting account balance
        total_trades: Total completed trades
        winning_trades: Number of winning trades
        losing_trades: Number of losing trades
        open_positions: Current open position snapshots
        active_orders: Active limit/stop orders + pending orders in transit
        pipeline_count: Orders currently in transit to broker (LiveOrderTracker)
        recent_trades: Last N completed trades (newest first)
        clipping_ratio: Fraction of ticks that experienced clipping
        avg_processing_ms: Average tick processing time in ms
        max_processing_ms: Maximum tick processing time in ms
        queue_depth: Current tick queue depth
        total_ticks_clipped: Total clipped ticks
        processing_times_ms: Per-tick processing times for percentile calculation
        ticks_per_min: Session-average tick rate (ticks / uptime_minutes)
        last_price: Mid price of last tick ((bid+ask)/2), 0.0 if no ticks yet
        worker_times_ms: Worker name to average computation time in ms
        worker_outputs: Worker name to display=True output values
        last_decision_action: Last decision action (DecisionLogicAction enum)
        decision_outputs: Decision display=True output values
        decision_time_ms: Decision logic computation time in ms
    """
    # Session
    session_start: datetime
    dry_run: bool
    symbol: str
    broker_type: str
    ticks_processed: int

    # Portfolio
    balance: float
    initial_balance: float
    total_trades: int
    winning_trades: int
    losing_trades: int

    # Open Positions
    open_positions: List[PositionSnapshot] = field(default_factory=list)

    # Orders (pending + active limits + active stops combined)
    active_orders: List[ActiveOrderSnapshot] = field(default_factory=list)
    pipeline_count: int = 0

    # Trade History (last N completed trades, newest first)
    recent_trades: List[TradeHistoryEntry] = field(default_factory=list)

    # Clipping
    clipping_ratio: float = 0.0
    avg_processing_ms: float = 0.0
    max_processing_ms: float = 0.0
    queue_depth: int = 0
    total_ticks_clipped: int = 0
    processing_times_ms: List[float] = field(default_factory=list)

    # Tick rate
    ticks_per_min: float = 0.0

    # Last market price (mid = (bid+ask)/2 of last tick)
    last_price: float = 0.0

    # Account currency (which side of the pair the account holds)
    account_currency: str = 'USD'

    # Symbol currencies (from SymbolSpec — no string splitting heuristic)
    base_currency: str = ''
    quote_currency: str = ''

    # Trading model ('spot' or 'margin')
    trading_model: str = 'margin'

    # Safety / circuit breaker state
    safety_blocked: bool = False
    safety_reason: str = ''

    # Last order rejection (persists until session end or next successful trade)
    last_rejection: str = ''

    # Worker Performance
    worker_times_ms: Dict[str, float] = field(default_factory=dict)
    worker_max_times_ms: Dict[str, float] = field(default_factory=dict)

    # Worker Outputs (display=True only)
    worker_outputs: Dict[str, Dict[str, OutputValue]] = field(default_factory=dict)

    # Decision State
    last_decision_action: DecisionLogicAction = DecisionLogicAction.FLAT
    decision_outputs: Dict[str, OutputValue] = field(default_factory=dict)
    decision_time_ms: float = 0.0
    decision_max_time_ms: float = 0.0
