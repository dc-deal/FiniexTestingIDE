"""
FiniexTestingIDE - AutoTrader Display Types
Data structures for live console dashboard (#228).

Lightweight snapshots pushed via queue from tick loop to display thread.
Designed for queue transport: all fields are primitives, lists, or dicts.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from python.framework.types.decision_logic_types import DecisionAwareness, DecisionLogicAction, StrategyEvent
from python.framework.types.parameter_types import OutputValue
from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason, CloseType
from python.framework.types.trading_env_types.broker_trade_types import BrokerTrade
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderSide
from python.framework.types.trading_env_types.pending_order_stats_types import ActiveOrderSnapshot
from python.framework.types.live_types.api_perf_types import ApiPerfSnapshot


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
        entry_trades: Per-execution BrokerTrade list from Position.entry_trades (#330).
            Single-fill → length 1; multi-fill (live, after #342) → length N.
            Renderer emits a multi-fill sub-line when len > 1.
    """
    position_id: str
    symbol: str
    direction: OrderDirection
    lots: float
    entry_price: float
    unrealized_pnl: float
    entry_trades: List[BrokerTrade] = field(default_factory=list)


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
        entry_trades: Per-execution BrokerTrade list from TradeRecord.entry_trades (#330).
            Shared across all derived TradeRecords on a partially-closed position.
        exit_trades: Per-execution BrokerTrade list from TradeRecord.exit_trades (#330).
            Distinct per close event.
    """
    trade_id: str
    symbol: str
    direction: OrderDirection
    lots: float
    entry_price: float
    exit_price: float
    net_pnl: float
    close_reason: CloseReason
    close_type: CloseType = CloseType.FULL
    entry_trades: List[BrokerTrade] = field(default_factory=list)
    exit_trades: List[BrokerTrade] = field(default_factory=list)
    # Trade-event side (BUY/SELL) for the close — used by Live TRADE HISTORY
    # column 'Side'. Default None for legacy / pre-#330 records.
    exit_side: Optional[OrderSide] = None


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
        pipeline_count: Orders currently in transit to broker (LiveRequestProcessor)
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

    # Broker config seed (8-char SHA256 of symbols block — empty if unavailable)
    config_hash: str = ''

    # Equity + spot balances (spot mode populated, margin mode equity only)
    equity: float = 0.0
    spot_balances: Optional[Dict[str, float]] = None

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
    # Safety detail values (for headroom display in SESSION panel)
    safety_current_value: float = 0.0
    safety_drawdown_pct: float = 0.0

    # Last order rejection (persists until session end or next successful trade)
    last_rejection: str = ''

    # Worker Performance
    worker_times_ms: Dict[str, float] = field(default_factory=dict)
    worker_max_times_ms: Dict[str, float] = field(default_factory=dict)
    worker_rolling_avg_times_ms: Dict[str, float] = field(default_factory=dict)

    # Worker Outputs (display=True only)
    worker_outputs: Dict[str, Dict[str, OutputValue]] = field(default_factory=dict)

    # Decision State
    last_decision_action: DecisionLogicAction = DecisionLogicAction.FLAT
    decision_outputs: Dict[str, OutputValue] = field(default_factory=dict)
    decision_time_ms: float = 0.0
    decision_max_time_ms: float = 0.0
    decision_rolling_avg_ms: float = 0.0

    # Decision logic config params (display=True inputs → Params: line in ALGO STATE)
    config_params: Dict[str, OutputValue] = field(default_factory=dict)

    # AwarenessChannel — ephemeral narration from decision logic
    last_awareness: Optional[DecisionAwareness] = None

    # Event tape — last N strategy moments (ring buffer snapshot)
    event_history: List[StrategyEvent] = field(default_factory=list)
    total_events_emitted: int = 0
    last_tick_time: Optional[datetime] = None

    # #320 — Heartbeat pulse frame
    # is_pulse: True if this frame was pushed by AbstractTradeExecutor.heartbeat
    #   during idle (no real tick processed). Renderer uses this to show a
    #   "💓 Ns since last tick" indicator instead of suggesting a fresh tick.
    # seconds_since_last_tick: wall-clock seconds since the most recent real
    #   tick was processed. 0.0 on regular frames.
    is_pulse: bool = False
    seconds_since_last_tick: float = 0.0

    # #327 — Drift Audit footer counters
    # Populated from DriftAuditor.get_display_counters() during _build_display_stats.
    # Renderer conditionally appends an "Audit:" line to the SESSION panel when
    # drift_enabled is True. PRICE counter measures Kraken-intra-reporting
    # consistency (dim). SLIPPAGE counter (#340) measures the real submission-tick
    # vs broker-fill price gap (cyan).
    drift_enabled: bool = False
    drift_audited: int = 0
    drift_fee_events: int = 0
    drift_volume_events: int = 0
    drift_price_events: int = 0
    drift_slippage_events: int = 0
    drift_max_fee_pct: float = 0.0
    drift_max_slippage_pct: float = 0.0

    # #151 — Reconciliation status (ALERT_ONLY)
    # Populated from Reconciler.get_display_counters(). Renderer appends a
    # one-line "Reconcile:" status to the SESSION panel when reconcile_enabled.
    # divergences = current cycle; count = cycles run (0 = no check yet);
    # state_age_s = seconds in the current clean/divergent state ("clean for Xs");
    # next_in_s = time-based bound to the next reconcile (may fire sooner on ticks).
    reconcile_enabled: bool = False
    reconcile_divergences: int = 0
    reconcile_clean: bool = True
    reconcile_count: int = 0
    reconcile_state_age_s: float = 0.0
    reconcile_next_in_s: float = 0.0

    # #351 — API performance monitor snapshot (None when not wired / disabled).
    # Renderer adds an "API PERFORMANCE" panel when present + non-empty.
    api_perf: Optional[ApiPerfSnapshot] = None
