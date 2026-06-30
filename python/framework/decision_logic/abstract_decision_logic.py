"""
FiniexTestingIDE - Abstract Decision Logic (Refactored)
Base class for all decision logic implementations

Decision Logic orchestrates worker results into trading decisions AND executes them.
This layer is separate from worker coordination - it focuses purely
on decision-making strategy AND trade execution, not on worker management.
"""

from abc import ABC, abstractmethod
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from python.configuration.app_config_manager import AppConfigManager
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.reporting.diagnostics_csv_sink import DiagnosticsCsvSink
from python.framework.types.component_metadata_types import ComponentMetadata
from python.framework.decision_logic.decision_logic_performance_tracker import DecisionLogicPerformanceTracker
from python.framework.trading_env.decision_trading_api import DecisionTradingApi
from python.framework.types.decision_logic_types import AwarenessLevel, Decision, DecisionAwareness, StrategyEvent
from python.framework.types.decision_event_types import (
    DecisionEventType,
    OrderCancelledEvent,
    OrderFilledEvent,
    OrderRejectedEvent,
    PartialCloseEvent,
    SessionEndEvent,
)
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.market_types.market_types import TradingContext
from python.framework.types.persistence_types import RestoreContext
from python.framework.types.trading_env_types.order_types import OrderResult, OrderType
from python.framework.types.parameter_types import InputParamDef, OutputParamDef, ValidatedParameters
from python.framework.types.performance_types.performance_stats_types import DecisionLogicStats
from python.framework.types.worker_types import WorkerRequirement, WorkerResult
from python.framework.validators.parameter_validator import validate_parameters


class AbstractDecisionLogic(ABC):
    """
    Abstract base class for decision logic implementations.

    Decision Logic takes worker results, generates trading decisions,
    and executes them via DecisionTradingApi.

    Philosophy (from Worker Manifest):
    - Workers are atomic units (first level)
    - DecisionLogic orchestrates results (second level)
    - No sub-workers, no hidden dependencies

    Architecture:
    1. get_required_order_types() declares needed order types
    2. BatchOrchestrator validates against broker capabilities
    3. DecisionTradingApi is injected after validation
    4. compute_tick() / compute_heartbeat() generate decisions
    5. execute_decision() executes trades (Template Method)
       - Calls _execute_decision_impl() (subclass implements this)
       - Automatically updates statistics
    """

    def __init__(
        self,
        name: str,
        logger: ScenarioLogger,
        config=None,
        trading_context: TradingContext = None
    ):
        """
        Initialize decision logic.

        Args:
            name: Decision logic name
            logger: ScenarioLogger instance (REQUIRED)
            config: ValidatedParameters or dict (auto-wrapped)
            trading_context: TradingContext (optional)

        Raises:
            ValueError: If logger is None
        """
        if logger is None:
            raise ValueError(
                f"DecisionLogic '{name}' requires a logger instance")

        self.name = name
        self._trading_context = trading_context

        # Set by DecisionLogicFactory when loaded from a file path
        # Used by WorkerOrchestrator to resolve relative worker references
        self._source_path: Optional[Path] = None

        # API and loggers
        self.trading_api: Optional[DecisionTradingApi] = None
        self.logger = logger
        self.performance_logger: DecisionLogicPerformanceTracker = None

        # --- Parameter access  ---
        if isinstance(config, ValidatedParameters):
            self.params = config
        else:
            self.params = ValidatedParameters(config or {})

        # Raw dict access preserved for WorkerOrchestrator._extract_decision_logic_type()
        # which reads: decision_logic.config['decision_logic_type']
        self.config = self.params.as_dict()

        # AwarenessChannel — ephemeral narration slot (single slot, last write wins)
        self._last_awareness: Optional[DecisionAwareness] = None

        # Event tape — bounded ring buffer for strategy moments (UI cache)
        tape_size = AppConfigManager().get_event_tape_size()
        self._event_history: deque[StrategyEvent] = deque(maxlen=tape_size)
        self._total_events_emitted: int = 0

        # Diagnostics CSV sinks (#376) — algo-declared, framework-flushed at run end
        self._diagnostics_sinks: Dict[str, DiagnosticsCsvSink] = {}

    # ============================================
    # abstractmethods
    # ============================================

    @classmethod
    @abstractmethod
    def get_required_order_types(cls, decision_logic_config: Dict[str, Any]) -> List[OrderType]:
        """
            Declare required order types WITHOUT creating instance.

            This allows validation BEFORE Trade Simulator creation.
            Mirrors Worker.calculate_requirements() pattern.

            Args:
                config: Decision logic configuration dict

            Returns:
                List of OrderType this logic will use

            Example:
                @classmethod
                def get_required_order_types(cls, decision_logic_config):
                    return [OrderType.MARKET]
            """
        pass

    # ============================================
    # Parameter Schema
    # ============================================

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, InputParamDef]:
        """
        Declare parameter schema for validation and UX.

        Override in subclass to define decision logic parameters
        with types, ranges, and defaults.

        Returns:
            Dict[param_name, InputParamDef]
        """
        return {}

    @classmethod
    def get_output_schema(cls) -> Dict[str, OutputParamDef]:
        """
        Declare output schema for typed decision outputs.

        Override in subclass to define what compute() returns
        in Decision.outputs. Used by #228 Live Console UI
        to render decision state generically.

        Returns:
            Dict[output_name, OutputParamDef]
        """
        return {}

    @classmethod
    def get_metadata(cls) -> ComponentMetadata:
        """
        Author-declared metadata (version, doc link, recommended market fit).

        Override to declare. Default is an empty ComponentMetadata (opt-in, no-op).
        Complements the automatic config_fingerprint with semantic intent; the
        recommended_markets / recommended_instruments drive a soft (non-blocking)
        market-fit warning at pre-flight.

        Returns:
            ComponentMetadata for this decision logic
        """
        return ComponentMetadata()

    @classmethod
    def validate_parameter_schema(
        cls,
        config: Dict[str, Any],
        strict: bool = True
    ) -> List[str]:
        """
        Validate config against parameter schema (no instance needed).

        Rejects unknown config keys — decision_logic_config is pure schema parameters
        (no structural keys), so any non-schema key is a typo that would otherwise be
        silently ignored at runtime.

        Called in Phase 0 (static) and Phase 6 (factory).

        Args:
            config: Decision logic configuration dict
            strict: True = raise on boundary violations, False = warn only

        Returns:
            List of warning messages
        """
        return validate_parameters(
            config, cls.get_parameter_schema(), strict,
            context_name=cls.__name__, reserved_keys=set(),
        )

    def execute_decision(
        self,
        decision: Decision,
        tick: Optional[TickData]
    ) -> Optional[OrderResult]:
        """
        Execute trading decision via DecisionTradingApi (Template Method).

        On a ghost-pass (#360) tick is None — only logics that opt in via
        wants_heartbeat() are dispatched this way.

        This is a template method that:
        1. Calls _execute_decision_impl() (implemented by subclass)
        2. Automatically updates statistics
        3. Returns order result

        Subclasses should implement _execute_decision_impl() instead.

        Args:
            decision: Decision object from compute()
            tick: Current tick data

        Returns:
            OrderResult if order was sent, None if no trade
        """
        # Call implementation (subclass)
        order_result = self._execute_decision_impl(decision, tick)

        if order_result:
            # Track trade request in performance logger
            if self.performance_logger:
                self.performance_logger.record_trade_requested()

        return order_result

    @abstractmethod
    def _execute_decision_impl(
        self,
        decision: Decision,
        tick: Optional[TickData]
    ) -> Optional[OrderResult]:
        """
        Implementation of decision execution (to be overridden by subclass).

        This is called by execute_decision() template method.
        Subclass implements the actual trading logic here.

        Args:
            decision: Decision object from compute()
            tick: Current tick data

        Returns:
            OrderResult if order was sent, None if no trade
        """
        pass

    # ============================================
    # Existing abstractmethods (unchanged)
    # ============================================

    @abstractmethod
    def get_required_workers(self) -> Dict[str, WorkerRequirement]:
        """
        Declare required worker instances with their type and consumed signals (#425).

        The single, mandatory wiring between a decision logic and its workers: each
        entry maps an instance name to a WorkerRequirement carrying the worker type
        AND the output signals this logic reads from that instance. The config's
        worker_instances must provide matching keys and types — type override is not
        allowed (declaring 'CORE/rsi' forbids a config 'CORE/macd' for the same name).

        Signals are the worker's output-schema keys (strings, e.g. 'position'). A worker
        computes only its declared optional outputs and skips the rest, so a hot decision
        pays nothing for signals it never reads. Declare every output accessed via
        get_signal() — reading an undeclared (hence uncomputed) optional output raises.
        Use WorkerRequirement.of(type, *signals) for an explicit subset, or
        WorkerRequirement.all(type) to read every output (explicit compute-all, the
        successor of the old empty-map default).

        Returns:
            Dict[instance_name, WorkerRequirement] - the exact worker instances + signals
        """
        pass

    @abstractmethod
    def compute_tick(
        self,
        tick: TickData,
        worker_results: Dict[str, WorkerResult],
    ) -> Decision:
        """
        Generate trading decision for a real market tick (TICK pass-trigger).

        This is the core decision-making method. It receives all worker
        outputs and must return a structured Decision object. The tick is
        guaranteed non-None — the heartbeat pass-trigger has its own handler
        (compute_heartbeat), so no None-guards are needed here.

        Args:
            tick: Current tick data (never None)
            worker_results: Dict[worker_name, WorkerResult] - All worker outputs

        Returns:
            Decision object with action/confidence/reason
        """
        pass

    def compute_heartbeat(
        self,
        worker_results: Dict[str, WorkerResult],
    ) -> Optional[Decision]:
        """
        Generate trading decision for an idle heartbeat (HEARTBEAT pass-trigger, #360).

        Ghost-pass between ticks: workers do not recompute — worker_results
        are their cached last outputs. No market tick exists; read time via
        get_current_time() and prices from your own last-tick state. Only
        called when wants_heartbeat() returns True — override both together.

        Args:
            worker_results: Cached worker outputs from the last tick pass

        Returns:
            Decision to execute with tick=None, or None for no ghost action
        """
        return None

    # ============================================
    # AwarenessChannel — ephemeral narration
    # ============================================

    def notify_awareness(
        self,
        message: str,
        level: AwarenessLevel = AwarenessLevel.INFO,
        reason_key: Optional[str] = None
    ) -> None:
        """
        Set the current awareness narration (single slot, last write wins).

        Called in compute_tick() to tell the operator what the algo is "thinking".
        NOT for structural rejections (those go through OrderGuard).

        Args:
            message: Human-readable narration
            level: Visual severity (INFO/NOTICE/ALERT)
            reason_key: Optional machine-readable key for grouping
        """
        self._last_awareness = DecisionAwareness(
            message=message,
            level=level,
            reason_key=reason_key,
        )

    def get_last_awareness(self) -> Optional[DecisionAwareness]:
        """
        Read the last awareness narration (non-destructive).

        Returns:
            DecisionAwareness or None if never set
        """
        return self._last_awareness

    # ============================================
    # Event Tape — elevated log entries for live UI
    # ============================================

    def emit_event(
        self,
        message: str,
        level: AwarenessLevel = AwarenessLevel.INFO,
        reason_key: Optional[str] = None,
    ) -> None:
        """
        Surface a strategy moment as an elevated log entry.

        Writes an INFO log line tagged '[EVENT][<level>]' and appends a
        StrategyEvent to the in-memory ring buffer for live UI consumption.
        Tick time is resolved via the trading API clock source, matching
        the OrderGuard clock semantics (sim time in backtests, wall-clock
        in live trading).

        Args:
            message: Human-readable event description
            level: Algo-semantic severity (INFO/NOTICE/ALERT) — does NOT
                   change the underlying log level, which is always INFO
            reason_key: Optional machine-readable key for grouping/filtering
        """
        tick_time = self.trading_api.get_current_time()
        event = StrategyEvent(
            message=message,
            level=level,
            tick_time=tick_time,
            reason_key=reason_key,
        )
        self._event_history.append(event)
        self._total_events_emitted += 1
        self.logger.info(
            f"[EVENT][{level.name}] t={tick_time.isoformat()} {message}"
        )

    def get_event_history(self) -> List[StrategyEvent]:
        """
        Read the event tape (non-destructive).

        Returns:
            List of StrategyEvent from the ring buffer (oldest first)
        """
        return list(self._event_history)

    def get_total_events_emitted(self) -> int:
        """
        Total events emitted since session start (includes evicted ones).

        Returns:
            Cumulative event count
        """
        return self._total_events_emitted

    # ============================================
    # Diagnostics CSV Sinks (#376)
    # ============================================

    def diagnostics_csv(self, name: str, columns: List[str]) -> DiagnosticsCsvSink:
        """
        Get or create a named diagnostics CSV sink (strategy-owned schema).

        The strategy declares the columns and appends rows during the run; the
        framework owns the file logistics and flushes it to the run directory at
        run end (both pipelines), next to events.csv. Calling again with the same
        name returns the same sink (columns set on first create). Low-frequency
        use only (decision moments) — rows buffer in memory, no hot-path cost.

        Args:
            name: Filename stem (e.g. 'setup_funnel')
            columns: Ordered column names — the CSV header

        Returns:
            The DiagnosticsCsvSink for this name (created on first call)
        """
        if name not in self._diagnostics_sinks:
            self._diagnostics_sinks[name] = DiagnosticsCsvSink(name, columns)
        return self._diagnostics_sinks[name]

    def get_diagnostics_sinks(self) -> List[DiagnosticsCsvSink]:
        """
        All diagnostics sinks declared by this logic (for the framework flush).

        Returns:
            List of DiagnosticsCsvSink (empty if none declared)
        """
        return list(self._diagnostics_sinks.values())

    # ============================================
    # Decision Event Channel (#348)
    # ============================================

    @classmethod
    def get_subscribed_events(cls) -> Set[DecisionEventType]:
        """
        Declare which decision events this logic wants to receive.

        Override in subclass to subscribe to order/lifecycle events delivered
        between ticks via the on_* hooks. Default: no subscriptions (most
        logics react only in compute_tick()). The DecisionEventDispatcher only
        buffers and delivers the subscribed types — unsubscribed events cost
        nothing.

        Returns:
            Set of DecisionEventType to subscribe to
        """
        return set()

    def wants_heartbeat(self) -> bool:
        """
        Whether this logic should run a ghost-pass on the idle heartbeat (#360).

        Default False: the orchestrator skips the ghost-pass and the logic only
        runs on real ticks (existing behavior, never called with tick=None).
        Override to True for logics that must act between ticks — advance internal
        state, react to drained events, issue follow-up orders. A ghost-pass calls
        compute()/_execute_decision_impl() with tick=None and cached worker results;
        such a logic MUST handle tick=None (no fresh market data).

        Returns:
            True to receive idle-heartbeat ghost-passes
        """
        return False

    def on_order_filled(self, event: OrderFilledEvent) -> None:
        """
        React to an order fill. No-op unless overridden.

        Called at the tick-loop boundary after the fill resolved (sim latency
        path or live poll/push). Subscribe via get_subscribed_events().

        Args:
            event: Fill detail (order/position id, price, lots, full result)
        """
        pass

    def on_order_rejected(self, event: OrderRejectedEvent) -> None:
        """
        React to an order rejection. No-op unless overridden.

        Args:
            event: Rejection detail (order id, reason, message, full result)
        """
        pass

    def on_order_cancelled(self, event: OrderCancelledEvent) -> None:
        """
        React to an order cancellation. No-op unless overridden.

        Args:
            event: Cancellation detail (order id, direction)
        """
        pass

    def on_partial_close(self, event: PartialCloseEvent) -> None:
        """
        React to a partial position close. No-op unless overridden.

        Args:
            event: Partial-close detail (position id, closed/remaining lots, price)
        """
        pass

    def on_session_end(self, event: SessionEndEvent) -> None:
        """
        React to the session ending. No-op unless overridden.

        Args:
            event: Session-end detail (reason, severity)
        """
        pass

    # ============================================
    # State Persistence (#354) — restart-safe algo memory (Category B)
    # ============================================

    def uses_state_persistence(self) -> bool:
        """
        Opt into restart-safe state persistence (#354).

        Default False: the whole persistence subsystem (store, restore, staleness
        check, boot pre-flight) is skipped — most algos (CORE demos, backtest
        showcases) hold no restart-relevant memory and need nothing. Override to
        True for a live bot that must survive restarts (e.g. a swing-state counter,
        "already entered today" flag, risk high-water-mark). A True here means the
        algo also implements get_state_snapshot/restore_state.

        Returns:
            True to enable persistence for this algo
        """
        return False

    def get_state_snapshot(self) -> Dict[str, Any]:
        """
        Return the algo's internal state for persistence (#354).

        Must be JSON-serializable — use only primitives (str/int/float/bool/
        list/dict/None); store timestamps as ISO strings. Category B only: persist
        position-independent memory (counters, regime, risk HWM, daily flags), NOT
        live-position references (those return on boot via Cold-Start Recovery, #355).

        Returns:
            JSON-serializable state dict (empty default → nothing persisted)
        """
        return {}

    def restore_state(self, snapshot: Dict[str, Any]) -> None:
        """
        Restore algo internal state from a persisted snapshot (#354).

        Called once after warmup and before the first decision, only if the
        framework staleness guard and accepts_restored_state() both pass. No-op default.

        Args:
            snapshot: The previously persisted state dict
        """
        return None

    def accepts_restored_state(self, snapshot: Dict[str, Any], ctx: RestoreContext) -> bool:
        """
        Algo-level freshness gate for a persisted snapshot (#354).

        Runs AFTER the coarse framework max-age guard passes and BEFORE
        restore_state(). Default True → only the framework's max_age_trading_days
        policy decides. Override to apply algo-specific freshness rules — e.g. a
        daily "already entered today" flag is stale across a date boundary even when
        the coarse guard would keep it. Timing is provided via ctx (the algo must not
        read wall-clock itself — §9).

        Args:
            snapshot: The persisted snapshot (not yet applied)
            ctx: Restore context (saved-at / now / age / trading-day age / weekend-aware)

        Returns:
            True to proceed with restore_state(snapshot); False to discard + start fresh
        """
        return True

    # ============================================
    # API Injection
    # ============================================

    def set_trading_api(self, trading_api: DecisionTradingApi) -> None:
        """
        Inject DecisionTradingApi after validation.

        This is called by BatchOrchestrator after verifying that
        the broker supports all required order types.

        Args:
            trading_api: Validated DecisionTradingApi instance
        """
        self.trading_api = trading_api

    def get_statistics(self) -> DecisionLogicStats:
        """
        Get decision logic statistics.

        Delegates to performance tracker which returns complete
        DecisionLogicStats (signals + performance).

        Returns:
            DecisionLogicStats with all metrics
        """
        if not self.performance_logger:
            # Fallback for tests without performance logger
            return DecisionLogicStats()

        return self.performance_logger.get_stats()

    def set_performance_logger(self, performance_logger: DecisionLogicPerformanceTracker) -> None:
        """
        Set performance logger for this decision logic.

        Called by WorkerOrchestrator to enable performance tracking.

        Args:
            logger: PerformanceLogDecisionLogic instance
        """
        self.performance_logger = performance_logger
