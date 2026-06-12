"""
FiniexTestingIDE - AutoTrader Tick Loop
Main tick processing loop for live trading (Threading model 8.a).

Runs in the main thread, pulls ticks from queue, processes through:
executor.on_tick → bar_controller → workers → decision_logic.

Session log rotates daily: session_logs/autotrader_session_YYYYMMDD.log
"""

import queue
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Deque, Dict, List, Optional, Tuple

from python.framework.autotrader.autotrader_startup import create_session_file_logger
from python.framework.autotrader.live_clipping_monitor import LiveClippingMonitor
from python.framework.autotrader.tick_sources.abstract_tick_source import AbstractTickSource
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.process.tick_pipeline_core import execute_algo_path, render_bars_for_tick, run_ghost_pass
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.trading_env.decision_event_dispatcher import DecisionEventDispatcher
from python.framework.trading_env.live.drift_auditor import DriftAuditor
from python.framework.trading_env.live.reconciler import Reconciler
from python.framework.persistence.algo_state_store import AlgoStateStore
from python.framework.reporting.api_perf_monitor import ApiPerfMonitor
from python.framework.types.live_types.api_perf_types import ApiPerfSnapshot
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.display_label_cache import DisplayLabelCache
from python.framework.types.config_types.market_config_types import TradingModel
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.autotrader_types.autotrader_display_types import (
    AutoTraderDisplayStats,
    PositionSnapshot,
    RejectionEntry,
    TradeHistoryEntry,
)
from python.framework.types.decision_event_types import SessionEndEvent, SessionEndSeverity
from python.framework.types.decision_logic_types import Decision, DecisionLogicAction
from python.framework.types.parameter_types import OutputValue
from python.framework.types.trading_env_types.order_types import OrderResult
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


class AutotraderTickLoop:
    """
    Tick processing loop for live trading.

    Pulls ticks from a queue.Queue (fed by a TickSource thread),
    processes each tick through the full algo pipeline:
    on_tick → bars → workers → decision → clipping monitor.

    Session log rotates at midnight UTC — each day gets its own file
    in session_logs/ to prevent unbounded log growth on 24/7 sessions.

    Args:
        config: AutoTrader configuration
        tick_queue: Thread-safe queue receiving ticks from tick source
        tick_source: Tick source (for exhaustion check)
        executor: LiveTradeExecutor instance
        bar_controller: BarRenderingController instance
        worker_orchestrator: WorkerOrchestrator instance
        decision_logic: DecisionLogic instance
        clipping_monitor: LiveClippingMonitor instance
        logger: ScenarioLogger instance (session logger)
        run_dir: Session run directory (for log rotation)
    """

    def __init__(
        self,
        config: AutoTraderConfig,
        tick_queue: queue.Queue,
        tick_source: AbstractTickSource,
        executor: AbstractTradeExecutor,
        bar_controller: BarRenderingController,
        worker_orchestrator: WorkerOrchestrator,
        decision_logic: AbstractDecisionLogic,
        clipping_monitor: LiveClippingMonitor,
        logger: ScenarioLogger,
        trading_model: TradingModel,
        run_dir: Optional[Path] = None,
        display_queue: Optional[queue.Queue] = None,
        session_start: Optional[datetime] = None,
        dry_run: bool = True,
        display_label_cache: Optional[DisplayLabelCache] = None,
        drift_auditor: Optional[DriftAuditor] = None,
        decision_event_dispatcher: Optional[DecisionEventDispatcher] = None,
        reconciler: Optional[Reconciler] = None,
        api_monitor: Optional[ApiPerfMonitor] = None,
        state_store: Optional[AlgoStateStore] = None,
    ):
        self._config = config
        self._tick_queue = tick_queue
        self._tick_source = tick_source
        self._executor = executor
        self._bar_controller = bar_controller
        self._worker_orchestrator = worker_orchestrator
        self._decision_logic = decision_logic
        self._clipping_monitor = clipping_monitor
        self._logger = logger
        self._trading_model = trading_model
        self._run_dir = run_dir
        self._display_queue = display_queue
        self._session_start = session_start or datetime.now(timezone.utc)
        self._dry_run = dry_run
        self._display_label_cache = display_label_cache or DisplayLabelCache()
        self._drift_auditor = drift_auditor
        self._decision_event_dispatcher = decision_event_dispatcher
        self._reconciler = reconciler
        self._api_monitor = api_monitor
        self._state_store = state_store
        self._running = False

        # #360: idle-heartbeat cadence — max wait for a real tick before the loop
        # fires a timer event (drain + reconcile + re-poll + decision ghost-pass).
        # Config is in ms; queue.get() wants seconds.
        self._heartbeat_interval_s = config.execution.heartbeat_interval_ms / 1000.0

        # #360 ghost-pass observability — proves the heartbeat decision pass fires
        # (and how often it acts), reported once at session end.
        self._ghost_pass_count: int = 0
        self._ghost_action_count: int = 0

        # Resolve symbol currencies from broker config (avoids string splitting heuristic)
        symbol_spec = executor.broker.adapter.get_symbol_specification(config.symbol)
        self._base_currency = symbol_spec.base_currency
        self._quote_currency = symbol_spec.quote_currency

        # Safety / circuit breaker state
        self._safety_blocked = False
        self._safety_reason = ''
        self._safety_current_value: float = 0.0
        self._safety_drawdown_pct: float = 0.0

        # Last rejection (displayed until overwritten by next rejection)
        self._recent_rejections: Deque[RejectionEntry] = deque(maxlen=5)
        self._rejection_count: int = 0

        # New-max tracking for debug logging
        self._known_worker_maxes: Dict[str, float] = {}
        self._known_decision_max: float = 0.0

        # Initial spot equity baseline — set on first tick (first live price)
        self._initial_spot_equity: float = 0.0

        # #320 — Last real-tick state for heartbeat pulse frames. Updated only
        # when a tick is actually processed, NOT during heartbeat. Used to
        # build pulse snapshots that show last-known portfolio state plus a
        # "Ns since last tick" idle indicator.
        self._last_real_tick: Optional[TickData] = None
        self._last_real_decision: Optional[Decision] = None
        self._last_real_tick_wall_time: float = 0.0

        # Daily rotation state
        self._current_log_date: Optional[str] = None
        # Track placeholder file for cleanup on first tick
        self._initial_placeholder_path: Optional[Path] = None
        if self._logger.file_logger:
            self._initial_placeholder_path = self._logger.file_logger.log_file_path

    def stop(self) -> None:
        """Signal the tick loop to stop. Thread-safe."""
        self._running = False

    def is_running(self) -> bool:
        """Check if the tick loop is currently running."""
        return self._running

    def run(self) -> Tuple[int, int]:
        """
        Execute the tick processing loop.

        Blocks until tick source is exhausted, stop() is called,
        or a sentinel (None) is received from the queue.

        Returns:
            (ticks_processed, ticks_clipped) counts
        """
        self._running = True
        ticks_processed = 0
        ticks_clipped = 0
        prev_msc: int = 0

        # Track last valid tick/decision for the final shutdown snapshot
        last_tick: Optional[TickData] = None
        last_decision: Optional[Decision] = None

        # Daily rotation: date initialized from first tick (not wall clock)
        # This prevents spurious rotation in mock/replay mode where tick
        # timestamps differ from current date.
        self._current_log_date = None

        # #320 — Push an initial startup pulse frame so the display renders
        # the full dashboard from t=0 instead of falling back to a wait
        # placeholder. Subsequent pulses fire from queue.Empty heartbeats.
        self._push_pulse_frame(ticks_processed)

        while self._running:
            try:
                tick = self._tick_queue.get(timeout=self._heartbeat_interval_s)
            except queue.Empty:
                # No tick within timeout — check if source is exhausted
                if self._tick_source and self._tick_source.is_exhausted():
                    self._logger.info(
                        '📭 Tick source exhausted — ending session')
                    break
                # #360: timer event. Inject the wall-clock so the canonical clock
                # advances during idle (phase/op timeouts track real elapsed time),
                # then run the side-effect-free cadence — no tick state mutation.
                self._executor.set_current_time(datetime.now(timezone.utc))
                # #320 + #360: drain async responses + check timeouts + re-poll
                # active orders (the fill/cancel-confirm query now fires during idle).
                self._executor.heartbeat()
                # #151 + #360: reconcile on the timer too (was tick-only). Time-bounded
                # by min_interval_seconds → self-throttled, no API storm during idle.
                if self._reconciler is not None and self._reconciler.is_due(ticks_processed):
                    self._reconciler.reconcile(ticks_processed)
                # #348: deliver events surfaced by the heartbeat drain
                # (idle-time fills/cancels) to the algo hooks — before the ghost-pass
                # so the decision observes them this pass.
                self._drain_decision_events()
                # #360 ghost-pass: let an opt-in decision act between ticks (advance
                # phases, react to drained events, issue follow-up orders) with no
                # synthetic tick and no tick-state mutation.
                self._run_decision_heartbeat(ticks_processed)
                # #354: persist algo state on the idle timer too (hybrid cadence).
                self._persist_state_if_due(ticks_processed)
                if self._executor.is_session_end_requested():
                    self._logger.info(
                        f"🛑 Session end requested: {self._executor.get_session_end_reason()}")
                    break
                self._push_pulse_frame(ticks_processed)
                continue

            # Sentinel value: None = tick source finished
            if tick is None:
                self._logger.info(
                    '📭 Tick source signaled end — ending session')
                break

            # === DAILY LOG ROTATION ===
            self._check_daily_rotation(tick)

            # === TIMING START ===
            tick_start_ns = time.perf_counter_ns()

            # Inter-tick delta for clipping detection
            current_msc = tick.collected_msc if tick.collected_msc > 0 else tick.time_msc
            tick_delta_ms = 0.0
            if prev_msc > 0 and current_msc > prev_msc:
                tick_delta_ms = float(current_msc - prev_msc)
            if current_msc > 0:
                prev_msc = current_msc

            # Set logger tick context
            ticks_processed += 1
            self._logger.set_current_tick(ticks_processed, tick)

            # === 1. Trade Executor — BROKER PATH (all ticks) ===
            self._executor.on_tick(tick)

            # Capture spot equity baseline on first tick (first live price available)
            if ticks_processed == 1 and self._trading_model == TradingModel.SPOT:
                first_price = (tick.bid + tick.ask) / 2.0
                self._initial_spot_equity = self._executor.portfolio.get_spot_equity(first_price)

            # === 2. Bar Rendering (shared core, #303) ===
            current_bars = render_bars_for_tick(tick, self._bar_controller)

            # === 3+4. Bar History + Worker Processing + Decision (shared core, #303) ===
            decision = execute_algo_path(
                tick=tick,
                current_bars=current_bars,
                bar_controller=self._bar_controller,
                worker_orchestrator=self._worker_orchestrator,
                symbol=self._config.symbol,
            )

            # === 4b. New-max debug logging ===
            self._check_new_maxes()

            # === 5. Safety Check (circuit breaker) ===
            # Spot: equity (balance + held asset value). Margin: raw balance.
            if self._trading_model == TradingModel.SPOT:
                safety_value = self._executor.portfolio.get_spot_equity(
                    (tick.bid + tick.ask) / 2.0)
            else:
                safety_value = self._executor.get_balance()
            safety_baseline = (
                self._initial_spot_equity
                if self._trading_model == TradingModel.SPOT and self._initial_spot_equity > 0
                else self._executor.portfolio.initial_balance
            )
            self._check_safety(safety_value, safety_baseline)

            # === 6. Order Execution ===
            if self._safety_blocked:
                decision.action = DecisionLogicAction.FLAT
            order_result = self._decision_logic.execute_decision(decision, tick)
            self._record_rejection(order_result, decision)

            # === 6b. Decision event drain (#348) ===
            # Events buffered during on_tick (fills, partial closes, cancels)
            # are delivered to the algo hooks here — after compute/execute and
            # before the next tick. A hook may request session end.
            self._drain_decision_events()
            if self._executor.is_session_end_requested():
                self._logger.info(
                    f"🛑 Session end requested: {self._executor.get_session_end_reason()}")
                break

            # === 6c. Reconciliation (#151, hybrid cadence, ALERT_ONLY) ===
            # Broker truth-pull every N ticks OR M seconds. Sync (like the #320
            # polling path); infrequent, so the periodic block is bounded.
            if self._reconciler is not None and self._reconciler.is_due(ticks_processed):
                self._reconciler.reconcile(ticks_processed)

            # === 6d. Algo State Persistence (#354, hybrid cadence) ===
            # Restart-safe algo memory (Category B). Save every N ticks OR M seconds.
            self._persist_state_if_due(ticks_processed)

            # === TIMING END ===
            elapsed_ns = time.perf_counter_ns() - tick_start_ns

            # === 7. Clipping Monitor ===
            self._clipping_monitor.record_tick(elapsed_ns, tick_delta_ms)
            self._clipping_monitor.record_queue_depth(self._tick_queue.qsize())

            # Periodic clipping report
            report = self._clipping_monitor.get_periodic_report()
            if report is not None:
                self._logger.info(
                    f"📊 Clipping report: {report.interval_ticks} ticks, "
                    f"{report.interval_clipped} clipped, "
                    f"avg {report.interval_avg_processing_ms:.2f}ms, "
                    f"max {report.interval_max_processing_ms:.2f}ms, "
                    f"queue_depth_max={report.interval_max_queue_depth}"
                )
                ticks_clipped += report.interval_clipped

            # === 8. Display Stats ===
            if self._display_queue is not None:
                display_stats = self._build_display_stats(
                    decision, ticks_processed, tick)
                try:
                    self._display_queue.put_nowait(display_stats)
                except queue.Full:
                    pass  # Display will use last known state

            # Track last valid tick/decision for the final shutdown snapshot
            # and heartbeat pulse frames (#320).
            last_tick = tick
            last_decision = decision
            self._last_real_tick = tick
            self._last_real_decision = decision
            self._last_real_tick_wall_time = time.time()

        # === Session end event (#348) — emitted once the loop ends, whether by
        # request, tick-source exhaustion, or stop(). Delivered before teardown.
        if self._decision_event_dispatcher is not None:
            if self._executor.is_session_end_requested():
                end_reason = self._executor.get_session_end_reason()
                end_severity = self._executor.get_session_end_severity()
            else:
                end_reason = 'tick source exhausted'
                end_severity = SessionEndSeverity.NORMAL
            self._decision_event_dispatcher.submit(SessionEndEvent(
                reason=end_reason,
                severity=end_severity,
                tick_time=self._executor.get_current_time(),
            ))
            self._decision_event_dispatcher.drain()

        # Final display snapshot — ensures the last rendered frame reflects
        # the terminal pipeline state regardless of display refresh timing.
        # The queue (maxsize=10) is likely full from high-speed tick replay,
        # so drain stale snapshots first to guarantee the final one lands.
        if (self._display_queue is not None
                and last_tick is not None
                and last_decision is not None):
            while not self._display_queue.empty():
                try:
                    self._display_queue.get_nowait()
                except queue.Empty:
                    break
            final_stats = self._build_display_stats(
                last_decision, ticks_processed, last_tick)
            try:
                self._display_queue.put(final_stats, timeout=1.0)
            except queue.Full:
                pass

        # #360 ghost-pass observability — proves the idle decision pass fired
        # (and acted) over the session. Machine-parseable.
        self._logger.info(
            f"[GHOST] ghost_passes={self._ghost_pass_count} "
            f"ghost_actions={self._ghost_action_count} ticks={ticks_processed}")

        self._running = False
        return ticks_processed, ticks_clipped

    def _drain_decision_events(self) -> None:
        """Drain buffered decision events to the algo hooks, if a dispatcher is active (#348)."""
        if self._decision_event_dispatcher is not None:
            self._decision_event_dispatcher.drain()

    def _record_rejection(self, order_result: Optional[OrderResult], decision: Decision) -> None:
        """
        Record a rejected order into the rolling rejection buffer (display + count).

        Args:
            order_result: Result of the executed decision (may be None / non-rejected)
            decision: The decision that produced the order (for the side label)
        """
        if not (order_result and order_result.is_rejected):
            return
        reason = order_result.rejection_reason.value if order_result.rejection_reason else 'unknown'
        self._rejection_count += 1
        self._recent_rejections.append(RejectionEntry(
            seq=self._rejection_count,
            reason=reason,
            message=order_result.rejection_message or '',
            side=decision.action.value,
            tick_time=self._executor.get_current_time(),
        ))

    def _persist_state_if_due(self, ticks_processed: int) -> None:
        """
        Save the algo state snapshot if the persistence cadence is due (#354).

        No-op when no state store is wired (algo did not opt in). Mid-session save
        failures are logged (error pot, §35) and swallowed — a persistence problem
        must never abort a live trading session.

        Args:
            ticks_processed: Current tick counter (drives the hybrid cadence)
        """
        if self._state_store is None or not self._state_store.is_due(ticks_processed):
            return
        try:
            self._state_store.save(
                self._decision_logic.get_state_snapshot(), ticks_processed)
        except Exception as e:
            self._logger.error(f"Algo state save failed (continuing): {e}")

    def _run_decision_heartbeat(self, ticks_processed: int) -> None:
        """
        #360 ghost-pass: run an opt-in decision between ticks without a synthetic tick.

        Workers do not recompute — the orchestrator serves their cached results. The
        decision runs with tick=None so it can advance internal state (e.g. field-study
        phases), react to drained #348 events, and issue follow-up orders (re-arm /
        cancel / confirm). No tick state is mutated (no counter, no mark_dirty, no bar
        render). Safety reuses the last evaluated block state (no fresh price on idle).

        Args:
            ticks_processed: Current tick counter (unchanged on a ghost-pass)
        """
        decision = run_ghost_pass(self._worker_orchestrator)
        if decision is None:
            return
        self._ghost_pass_count += 1
        if self._safety_blocked:
            decision.action = DecisionLogicAction.FLAT
        order_result = self._decision_logic.execute_decision(decision, tick=None)
        if order_result is not None:
            self._ghost_action_count += 1
        self._record_rejection(order_result, decision)

    def _push_pulse_frame(self, ticks_processed: int) -> None:
        """
        Push a heartbeat pulse frame to the display queue (#320).

        Pulse frames carry the last-known portfolio snapshot plus a wall-clock
        delta so the dashboard shows "💓 Ns since last tick" instead of going
        stale during idle.

        Before the first real tick, a slim startup snapshot is pushed instead
        so the operator sees the session is alive (`💓 Ns since startup`)
        rather than the display's default "Waiting for first tick..." text.
        """
        if self._display_queue is None:
            return

        if self._last_real_tick is None or self._last_real_decision is None:
            seconds_since = (datetime.now(timezone.utc) - self._session_start).total_seconds()
            stats = self._build_startup_pulse_stats(seconds_since)
        else:
            seconds_since = time.time() - self._last_real_tick_wall_time
            stats = self._build_display_stats(
                self._last_real_decision, ticks_processed, self._last_real_tick)
            stats.is_pulse = True
            stats.seconds_since_last_tick = seconds_since

        try:
            self._display_queue.put_nowait(stats)
        except queue.Full:
            pass  # Display will use last known state

    def _build_startup_pulse_stats(self, seconds_since_start: float) -> AutoTraderDisplayStats:
        """
        Build a slim pre-first-tick pulse frame (#320).

        Carries only what's known before any market data has arrived: session
        identity, configured account, and the wall-clock delta since session
        start. The renderer flags the `ticks_processed == 0 + is_pulse` combo
        and shows `💓 Ns since startup` instead of the default wait label.

        Args:
            seconds_since_start: Wall-clock seconds since session_start

        Returns:
            Minimal AutoTraderDisplayStats with is_pulse=True
        """
        portfolio = self._executor.portfolio
        return AutoTraderDisplayStats(
            session_start=self._session_start,
            dry_run=self._dry_run,
            symbol=self._config.symbol,
            broker_type=self._config.broker_type,
            ticks_processed=0,
            config_hash=self._executor.broker.config_hash,
            balance=portfolio.balance,
            initial_balance=portfolio.initial_balance,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            equity=portfolio.balance,
            spot_balances=portfolio.get_balances() if self._trading_model == TradingModel.SPOT else None,
            account_currency=self._executor.account_currency,
            base_currency=self._base_currency,
            quote_currency=self._quote_currency,
            trading_model=self._trading_model.value,
            is_pulse=True,
            seconds_since_last_tick=seconds_since_start,
            **self._drift_display_counters(),
            **self._reconcile_display_counters(),
            api_perf=self._api_perf_snapshot(),
        )

    def _build_display_stats(self, decision: Decision, ticks_processed: int, tick: TickData) -> AutoTraderDisplayStats:
        """
        Build display stats snapshot from current pipeline state.

        Reads portfolio dirty-state (attribute reads, zero cost),
        open positions (0-3 in live), active orders, worker outputs.
        Called after the algo pipeline — not on the critical path.

        Args:
            decision: Current tick's decision
            ticks_processed: Tick counter
            tick: Current tick (for last_price)

        Returns:
            AutoTraderDisplayStats snapshot for display queue
        """
        portfolio = self._executor.portfolio

        # Ensure P&L is current before snapshotting (display only, not every tick)
        portfolio.ensure_positions_updated()

        # Mid price (needed early for spot equity)
        last_price = (tick.bid + tick.ask) / 2.0

        # Equity + spot balances — spot mode reads balances directly (O(1), no cache trigger)
        if self._trading_model == TradingModel.SPOT:
            equity = portfolio.get_spot_equity(last_price)
            spot_balances = portfolio.get_balances()
        else:
            # Margin mode: balance + unrealized P&L (positions already updated above)
            unrealized = sum(pos.unrealized_pnl for pos in portfolio.open_positions.values())
            equity = portfolio.balance + unrealized
            spot_balances = None

        # Open positions → PositionSnapshot
        open_positions = []
        for pos in self._executor.get_open_positions():
            open_positions.append(PositionSnapshot(
                position_id=pos.position_id,
                symbol=pos.symbol,
                direction=pos.direction,
                lots=pos.lots,
                entry_price=pos.entry_price,
                unrealized_pnl=pos.unrealized_pnl,
                entry_trades=list(pos.entry_trades),
            ))

        # Active orders (limit + stop) from pending stats
        pending_stats = self._executor.get_pending_stats()
        active_orders = list(pending_stats.active_limit_orders) + \
            list(pending_stats.active_stop_orders)
        pipeline_count = pending_stats.latency_queue_count

        # Trade history — last 10, newest first
        recent_trades: List[TradeHistoryEntry] = []
        trade_history = self._executor.get_trade_history()
        for trade in trade_history[-10:][::-1]:
            recent_trades.append(TradeHistoryEntry(
                trade_id=trade.position_id,
                symbol=trade.symbol,
                direction=trade.direction,
                lots=trade.lots,
                entry_price=trade.entry_price,
                exit_price=trade.exit_price,
                net_pnl=trade.net_pnl,
                close_reason=trade.close_reason,
                close_type=trade.close_type,
                entry_trades=list(trade.entry_trades),
                exit_trades=list(trade.exit_trades),
                exit_side=trade.exit_side,
                exit_time=trade.exit_time,
            ))

        # Clipping — lightweight snapshot (avoids full session summary construction)
        clipping_stats = self._clipping_monitor.get_display_stats()

        # Worker performance + outputs (display=True only — keys from cache)
        worker_times: Dict[str, float] = {}
        worker_max_times: Dict[str, float] = {}
        worker_rolling_avgs: Dict[str, float] = {}
        worker_outputs: Dict[str, Dict[str, OutputValue]] = {}
        worker_display_keys = self._display_label_cache.worker_display_output_keys
        for name, worker in self._worker_orchestrator.workers.items():
            # Performance
            if worker.performance_logger:
                stats = worker.performance_logger.get_stats()
                worker_times[name] = stats.worker_avg_time_ms
                worker_max_times[name] = stats.worker_max_time_ms
                worker_rolling_avgs[name] = worker.performance_logger.get_rolling_avg_ms()

            # Outputs (cached display=True key list, no per-tick schema read)
            display_keys = worker_display_keys.get(name)
            if not display_keys:
                continue
            result = self._worker_orchestrator.get_worker_result(name)
            if result:
                display_outputs = {
                    key: result.outputs[key]
                    for key in display_keys
                    if key in result.outputs
                }
                if display_outputs:
                    worker_outputs[name] = display_outputs

        # Decision outputs (display=True)
        decision_outputs: Dict[str, OutputValue] = {}
        decision_schema = self._decision_logic.__class__.get_output_schema()
        for key, param_def in decision_schema.items():
            if param_def.display and key in decision.outputs:
                decision_outputs[key] = decision.outputs[key]

        # Decision logic config params (static, but read fresh to support future live reload)
        config_params: Dict[str, OutputValue] = {}
        for raw_key, _display_key in self._display_label_cache.config_param_specs:
            if self._decision_logic.params.has(raw_key):
                config_params[raw_key] = self._decision_logic.params.get(raw_key)

        # Tick rate (session average)
        uptime_min = max(0.001, (datetime.now(timezone.utc) -
                         self._session_start).total_seconds() / 60.0)
        ticks_per_min = ticks_processed / uptime_min

        # Spot mode: P&L baseline = full equity at session start (USD + crypto × first_price)
        # Margin mode: P&L baseline = account balance at session start
        if self._trading_model == TradingModel.SPOT and self._initial_spot_equity > 0:
            initial_balance_for_display = self._initial_spot_equity
        else:
            initial_balance_for_display = portfolio.initial_balance

        return AutoTraderDisplayStats(
            session_start=self._session_start,
            dry_run=self._dry_run,
            symbol=self._config.symbol,
            broker_type=self._config.broker_type,
            ticks_processed=ticks_processed,
            config_hash=self._executor.broker.config_hash,
            balance=portfolio.balance,
            initial_balance=initial_balance_for_display,
            total_trades=portfolio.get_total_trades(),
            winning_trades=portfolio.get_winning_trades(),
            losing_trades=portfolio.get_losing_trades(),
            equity=equity,
            spot_balances=spot_balances,
            open_positions=open_positions,
            active_orders=active_orders,
            pipeline_count=pipeline_count,
            recent_trades=recent_trades,
            clipping_ratio=clipping_stats.clipping_ratio,
            avg_processing_ms=clipping_stats.avg_processing_ms,
            max_processing_ms=clipping_stats.max_processing_ms,
            queue_depth=self._tick_queue.qsize(),
            total_ticks_clipped=clipping_stats.ticks_clipped,
            processing_times_ms=clipping_stats.processing_times_ms,
            ticks_per_min=ticks_per_min,
            last_price=last_price,
            worker_times_ms=worker_times,
            worker_max_times_ms=worker_max_times,
            worker_rolling_avg_times_ms=worker_rolling_avgs,
            worker_outputs=worker_outputs,
            last_decision_action=decision.action,
            decision_outputs=decision_outputs,
            config_params=config_params,
            decision_time_ms=self._decision_logic.performance_logger.get_stats().decision_avg_time_ms if self._decision_logic.performance_logger else 0.0,
            decision_max_time_ms=self._decision_logic.performance_logger.get_stats().decision_max_time_ms if self._decision_logic.performance_logger else 0.0,
            decision_rolling_avg_ms=self._decision_logic.performance_logger.get_rolling_avg_ms() if self._decision_logic.performance_logger else 0.0,
            account_currency=self._executor.account_currency,
            base_currency=self._base_currency,
            quote_currency=self._quote_currency,
            trading_model=self._trading_model.value,
            safety_blocked=self._safety_blocked,
            safety_reason=self._safety_reason,
            safety_current_value=self._safety_current_value,
            safety_drawdown_pct=self._safety_drawdown_pct,
            recent_rejections=list(self._recent_rejections),
            total_rejections=self._rejection_count,
            last_awareness=self._decision_logic.get_last_awareness(),
            event_history=self._decision_logic.get_event_history(),
            total_events_emitted=self._decision_logic.get_total_events_emitted(),
            last_tick_time=self._executor.get_current_time(),
            **self._drift_display_counters(),
            **self._reconcile_display_counters(),
            api_perf=self._api_perf_snapshot(),
        )

    def _drift_display_counters(self) -> Dict[str, object]:
        """
        Build drift_* kwargs for AutoTraderDisplayStats. Returns empty dict
        (uses dataclass defaults) when no DriftAuditor is wired. #327.
        """
        if self._drift_auditor is None:
            return {}
        counters = self._drift_auditor.get_display_counters()
        return {
            'drift_enabled': bool(counters.get('drift_enabled', False)),
            'drift_audited': int(counters.get('drift_audited', 0)),
            'drift_fee_events': int(counters.get('drift_fee_events', 0)),
            'drift_volume_events': int(counters.get('drift_volume_events', 0)),
            'drift_price_events': int(counters.get('drift_price_events', 0)),
            'drift_slippage_events': int(counters.get('drift_slippage_events', 0)),
            'drift_max_fee_pct': float(counters.get('drift_max_fee_pct', 0.0)),
            'drift_max_slippage_pct': float(counters.get('drift_max_slippage_pct', 0.0)),
        }

    def _reconcile_display_counters(self) -> Dict[str, object]:
        """
        Build reconcile_* kwargs for AutoTraderDisplayStats. Returns empty dict
        (uses dataclass defaults) when no Reconciler is wired. #151.
        """
        if self._reconciler is None:
            return {}
        counters = self._reconciler.get_display_counters()
        return {
            'reconcile_enabled': bool(counters.get('reconcile_enabled', False)),
            'reconcile_divergences': int(counters.get('reconcile_divergences', 0)),
            'reconcile_clean': bool(counters.get('reconcile_clean', True)),
            'reconcile_count': int(counters.get('reconcile_count', 0)),
            'reconcile_state_age_s': float(counters.get('reconcile_state_age_s', 0.0)),
            'reconcile_next_in_s': float(counters.get('reconcile_next_in_s', 0.0)),
        }

    def _api_perf_snapshot(self) -> Optional[ApiPerfSnapshot]:
        """Return the API monitor snapshot for the display, or None if not wired (#351)."""
        if self._api_monitor is None:
            return None
        return self._api_monitor.get_snapshot()

    def _check_new_maxes(self) -> None:
        """Log new all-time max execution times for workers and decision logic."""
        for name, worker in self._worker_orchestrator.workers.items():
            if not worker.performance_logger:
                continue
            current_max = worker.performance_logger.get_stats().worker_max_time_ms
            prev_max = self._known_worker_maxes.get(name, 0.0)
            if current_max > prev_max:
                self._known_worker_maxes[name] = current_max
                self._logger.debug(
                    f'NEW MAX: {name:<16s} {current_max:.2f}ms  (prev: {prev_max:.2f}ms)'
                )

        if self._decision_logic.performance_logger:
            current_max = self._decision_logic.performance_logger.get_stats().decision_max_time_ms
            if current_max > self._known_decision_max:
                prev = self._known_decision_max
                self._known_decision_max = current_max
                dl_type = self._config.strategy_config.get('decision_logic_type', '')
                dl_label = dl_type.split('/')[-1] if dl_type else 'decision'
                self._logger.debug(
                    f'NEW MAX: {dl_label:<16s} {current_max:.2f}ms  (prev: {prev:.2f}ms)'
                )

    def _check_safety(self, current_value: float, initial_balance: float) -> None:
        """
        Evaluate circuit breaker conditions and update safety state.

        Soft stop: sets _safety_blocked flag. Existing positions continue,
        new entries are blocked by overriding decision to FLAT.

        Args:
            current_value: Equity (spot) or balance (margin) — the checked value
            initial_balance: Session start balance (== initial equity, no positions at start)
        """
        safety = self._config.safety
        if not safety.enabled:
            return

        # Already blocked — check if conditions cleared (value recovered)
        was_blocked = self._safety_blocked
        self._safety_blocked = False
        self._safety_reason = ''
        self._safety_current_value = current_value

        # Min threshold: spot checks min_equity, margin checks min_balance
        if self._trading_model == TradingModel.SPOT:
            min_threshold = safety.min_equity
            min_label = 'min_equity'
        else:
            min_threshold = safety.min_balance
            min_label = 'min_balance'

        if min_threshold > 0 and current_value < min_threshold:
            self._safety_blocked = True
            self._safety_reason = f'{min_label} ({current_value:.4f} < {min_threshold:.4f})'

        # Drawdown: same threshold, different input (equity for spot, balance for margin)
        if safety.max_drawdown_pct > 0 and initial_balance > 0:
            drawdown_pct = (initial_balance - current_value) / \
                initial_balance * 100.0
            self._safety_drawdown_pct = max(0.0, drawdown_pct)
            if drawdown_pct > safety.max_drawdown_pct:
                self._safety_blocked = True
                reason = f'max_drawdown ({drawdown_pct:.1f}% > {safety.max_drawdown_pct:.1f}%)'
                self._safety_reason = (
                    f'{self._safety_reason} + {reason}' if self._safety_reason else reason
                )
        else:
            self._safety_drawdown_pct = 0.0

        if self._safety_blocked and not was_blocked:
            self._logger.warning(
                f'⛔ Safety circuit breaker triggered: {self._safety_reason}')
        elif was_blocked and not self._safety_blocked:
            self._logger.info('✅ Safety circuit breaker cleared')

    def _check_daily_rotation(self, tick: TickData) -> None:
        """
        Check if the tick date differs from the current log file date.

        On first tick: set initial date and rotate to tick-date-based file
        (startup creates a file from wall clock, which may differ in replay mode).
        On subsequent ticks: rotate when midnight UTC is crossed.

        Args:
            tick: Current tick data
        """
        if not self._run_dir:
            return

        # Derive date from tick timestamp (milliseconds since epoch)
        tick_date = datetime.fromtimestamp(
            tick.time_msc / 1000.0, tz=timezone.utc
        ).strftime('%Y%m%d')

        if self._current_log_date is None:
            # First tick — set initial date and ensure file matches tick date
            log_level = self._logger.file_logger.log_level if self._logger.file_logger else 'INFO'
            new_file_logger = create_session_file_logger(
                self._run_dir, tick_date, log_level
            )
            self._logger.swap_file_logger(new_file_logger)
            self._current_log_date = tick_date
            # Keep placeholder file — it contains pre-tick logs (warmup bars, pipeline setup)
            self._initial_placeholder_path = None
            return

        if tick_date != self._current_log_date:
            self._logger.info(
                f"📅 Date change detected: {self._current_log_date} → {tick_date} — rotating session log"
            )
            log_level = self._logger.file_logger.log_level if self._logger.file_logger else 'INFO'
            new_file_logger = create_session_file_logger(
                self._run_dir, tick_date, log_level
            )
            self._logger.swap_file_logger(new_file_logger)
            self._current_log_date = tick_date
            self._logger.info(
                f"📅 Session log rotated to autotrader_session_{tick_date}.log"
            )
