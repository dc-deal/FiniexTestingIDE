"""
FiniexTestingIDE - AutoTrader Tick Loop
Main tick processing loop for live trading (Threading model 8.a).

Runs in the main thread, pulls ticks from queue, processes through:
executor.on_tick → bar_controller → workers → decision_logic.

Session log rotates daily: session_logs/autotrader_session_YYYYMMDD.log
"""

import queue
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from python.framework.autotrader.autotrader_startup import create_session_file_logger
from python.framework.autotrader.live_clipping_monitor import LiveClippingMonitor
from python.framework.autotrader.tick_sources.abstract_tick_source import AbstractTickSource
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.market_types.market_config_types import TradingModel
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.autotrader_types.autotrader_display_types import (
    AutoTraderDisplayStats,
    PositionSnapshot,
    TradeHistoryEntry,
)
from python.framework.types.decision_logic_types import Decision, DecisionLogicAction
from python.framework.types.parameter_types import OutputValue
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
        self._running = False

        # Resolve symbol currencies from broker config (avoids string splitting heuristic)
        symbol_spec = executor.broker.adapter.get_symbol_specification(config.symbol)
        self._base_currency = symbol_spec.base_currency
        self._quote_currency = symbol_spec.quote_currency

        # Safety / circuit breaker state
        self._safety_blocked = False
        self._safety_reason = ''

        # Last rejection (displayed until overwritten by next rejection)
        self._last_rejection = ''

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

        while self._running:
            try:
                tick = self._tick_queue.get(timeout=1.0)
            except queue.Empty:
                # No tick within timeout — check if source is exhausted
                if self._tick_source and self._tick_source.is_exhausted():
                    self._logger.info(
                        '📭 Tick source exhausted — ending session')
                    break
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

            # === 2. Bar Rendering ===
            current_bars = self._bar_controller.process_tick(tick)

            # === 3. Bar History ===
            bar_history = self._bar_controller.get_all_bar_history(
                symbol=self._config.symbol
            )

            # === 4. Worker Processing + Decision ===
            decision = self._worker_orchestrator.process_tick(
                tick=tick,
                current_bars=current_bars,
                bar_history=bar_history
            )

            # === 5. Safety Check (circuit breaker) ===
            self._check_safety(self._executor.get_balance(),
                               self._executor.portfolio.initial_balance)

            # === 6. Order Execution ===
            if self._safety_blocked:
                decision.action = DecisionLogicAction.FLAT
            order_result = self._decision_logic.execute_decision(decision, tick)
            if order_result and order_result.is_rejected:
                reason = order_result.rejection_reason.value if order_result.rejection_reason else 'unknown'
                self._last_rejection = f'{reason} — {order_result.rejection_message} ({decision.action.value})'

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
            last_tick = tick
            last_decision = decision

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

        self._running = False
        return ticks_processed, ticks_clipped

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
            ))

        # Clipping — lightweight snapshot (avoids full session summary construction)
        clipping_stats = self._clipping_monitor.get_display_stats()

        # Worker performance + outputs (display=True only)
        worker_times: Dict[str, float] = {}
        worker_max_times: Dict[str, float] = {}
        worker_outputs: Dict[str, Dict[str, OutputValue]] = {}
        for name, worker in self._worker_orchestrator.workers.items():
            # Performance
            if worker.performance_logger:
                stats = worker.performance_logger.get_stats()
                worker_times[name] = stats.worker_avg_time_ms
                worker_max_times[name] = stats.worker_max_time_ms

            # Outputs (display=True from schema)
            schema = worker.__class__.get_output_schema()
            result = self._worker_orchestrator.get_worker_result(name)
            if result and schema:
                display_outputs = {}
                for key, param_def in schema.items():
                    if param_def.display and key in result.outputs:
                        display_outputs[key] = result.outputs[key]
                if display_outputs:
                    worker_outputs[name] = display_outputs

        # Decision outputs (display=True)
        decision_outputs: Dict[str, OutputValue] = {}
        decision_schema = self._decision_logic.__class__.get_output_schema()
        for key, param_def in decision_schema.items():
            if param_def.display and key in decision.outputs:
                decision_outputs[key] = decision.outputs[key]

        # Tick rate (session average)
        uptime_min = max(0.001, (datetime.now(timezone.utc) -
                         self._session_start).total_seconds() / 60.0)
        ticks_per_min = ticks_processed / uptime_min

        return AutoTraderDisplayStats(
            session_start=self._session_start,
            dry_run=self._dry_run,
            symbol=self._config.symbol,
            broker_type=self._config.broker_type,
            ticks_processed=ticks_processed,
            balance=portfolio.balance,
            initial_balance=portfolio.initial_balance,
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
            worker_outputs=worker_outputs,
            last_decision_action=decision.action,
            decision_outputs=decision_outputs,
            decision_time_ms=self._decision_logic.performance_logger.get_stats().decision_avg_time_ms if self._decision_logic.performance_logger else 0.0,
            decision_max_time_ms=self._decision_logic.performance_logger.get_stats().decision_max_time_ms if self._decision_logic.performance_logger else 0.0,
            account_currency=self._executor.account_currency,
            base_currency=self._base_currency,
            quote_currency=self._quote_currency,
            trading_model=self._trading_model.value,
            safety_blocked=self._safety_blocked,
            safety_reason=self._safety_reason,
            last_rejection=self._last_rejection,
            last_awareness=self._decision_logic.get_last_awareness(),
            event_history=self._decision_logic.get_event_history(),
            total_events_emitted=self._decision_logic.get_total_events_emitted(),
            last_tick_time=self._executor.get_current_time(),
        )

    def _check_safety(self, balance: float, initial_balance: float) -> None:
        """
        Evaluate circuit breaker conditions and update safety state.

        Soft stop: sets _safety_blocked flag. Existing positions continue,
        new entries are blocked by overriding decision to FLAT.

        Args:
            balance: Current account balance
            initial_balance: Session start balance
        """
        safety = self._config.safety
        if not safety.enabled:
            return

        # Already blocked — check if conditions cleared (balance recovered)
        was_blocked = self._safety_blocked
        self._safety_blocked = False
        self._safety_reason = ''

        if safety.min_balance > 0 and balance < safety.min_balance:
            self._safety_blocked = True
            self._safety_reason = f'min_balance ({balance:.4f} < {safety.min_balance:.4f})'

        if safety.max_drawdown_pct > 0 and initial_balance > 0:
            drawdown_pct = (initial_balance - balance) / \
                initial_balance * 100.0
            if drawdown_pct > safety.max_drawdown_pct:
                self._safety_blocked = True
                reason = f'max_drawdown ({drawdown_pct:.1f}% > {safety.max_drawdown_pct:.1f}%)'
                self._safety_reason = (
                    f'{self._safety_reason} + {reason}' if self._safety_reason else reason
                )

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
