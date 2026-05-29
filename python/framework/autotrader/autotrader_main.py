"""
FiniexTestingIDE - AutotraderMain
Live trading runner: Ticks → Workers → DecisionLogic → LiveTradeExecutor.

Threading model 8.a: sync algo loop in main thread,
tick source in separate thread, queue.Queue communication.
"""

import queue
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from python.framework.autotrader.autotrader_tick_loop import AutotraderTickLoop
from python.framework.autotrader.tick_sources.abstract_tick_source import AbstractTickSource
from python.framework.autotrader.autotrader_startup import (
    create_autotrader_loggers,
    setup_pipeline,
    setup_tick_source,
)
from python.framework.autotrader.live_clipping_monitor import LiveClippingMonitor
from python.framework.autotrader.reporting.autotrader_post_session_report import AutotraderPostSessionReport
from python.framework.reporting.event_stream_csv_writer import EventStreamWriter
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.trading_env.decision_event_dispatcher import DecisionEventDispatcher
from python.framework.trading_env.live.drift_auditor import DriftAuditor
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.decision_event_types import SessionEndSeverity
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.autotrader_types.display_label_cache import DisplayLabelCache
from python.configuration.market_config_manager import MarketConfigManager
from python.framework.utils.scenario_set_utils import ScenarioSetUtils
from python.framework.workers.worker_orchestrator import WorkerOrchestrator
from python.system.ui.autotrader_live_display import AutoTraderLiveDisplay


class AutotraderMain:
    """
    Live trading runner for FiniexTestingIDE.

    Mirrors the backtesting process_tick_loop but for live execution:
    - Tick source runs in a separate thread (Threading model 8.a)
    - Main thread processes ticks synchronously: on_tick → bars → workers → decision
    - Workers and DecisionLogic are the same classes as in backtesting

    Logging architecture:
    - global_logger: Startup phases, shutdown, errors → autotrader_global.log + console
    - session_logger: Per-tick processing → session_logs/autotrader_session_YYYYMMDD.log
    - summary_logger: Post-session summary → autotrader_summary.log + console

    Shutdown modes:
    - Normal: tick source exhausted or SIGTERM → close positions, cancel orders, collect stats
    - Emergency: SIGINT/Ctrl+C → immediate close, best-effort stats

    Args:
        config: AutoTraderConfig instance
    """

    def __init__(self, config: AutoTraderConfig):
        self._config = config
        self._running = False
        self._shutdown_mode = 'normal'
        self._session_start: Optional[float] = None

        # Tick communication (Threading model 8.a)
        self._tick_queue: queue.Queue = queue.Queue()
        self._tick_source: Optional[AbstractTickSource] = None
        self._tick_thread = None
        self._tick_loop: Optional[AutotraderTickLoop] = None

        # Pipeline components (created during run())
        self._executor: Optional[AbstractTradeExecutor] = None
        self._bar_controller: Optional[BarRenderingController] = None
        self._worker_orchestrator: Optional[WorkerOrchestrator] = None
        self._decision_logic: Optional[AbstractDecisionLogic] = None
        self._clipping_monitor: Optional[LiveClippingMonitor] = None
        self._display_label_cache: Optional[DisplayLabelCache] = None

        # #327 — Drift audit (live-only, gated by config.drift_audit.enabled)
        self._drift_auditor: Optional[DriftAuditor] = None

        # #348 — Decision event channel (None when the decision logic subscribes to no events)
        self._decision_event_dispatcher: Optional[DecisionEventDispatcher] = None

        # Loggers (created during run())
        self._global_logger: Optional[ScenarioLogger] = None
        self._session_logger: Optional[ScenarioLogger] = None
        self._summary_logger: Optional[ScenarioLogger] = None
        self._run_dir: Optional[Path] = None

        # Display (#228)
        self._display: Optional[AutoTraderLiveDisplay] = None
        self._display_queue: Optional[queue.Queue] = None

        # Signal handling state
        self._first_interrupt_time: float = 0.0

    def run(self) -> AutoTraderResult:
        """
        Execute the complete AutoTrader session.

        Flow:
        1. Setup: create loggers, print startup banner, create pipeline objects
        2. Start tick source thread
        3. Tick loop: queue.get → on_tick → bars → workers → decision
        4. Shutdown: close positions, collect statistics

        Returns:
            AutoTraderResult with session statistics
        """
        self._session_start = time.monotonic()
        run_timestamp = datetime.now(timezone.utc)

        # === LOGGERS ===
        self._global_logger, self._session_logger, self._summary_logger, self._run_dir = (
            create_autotrader_loggers(self._config, run_timestamp)
        )

        self._print_startup_banner()
        self._global_logger.info(
            f"🚀 AutotraderMain starting: {self._config.symbol} "
            f"({self._config.broker_type}, adapter={self._config.adapter_type})"
        )

        # Copy profile config snapshot to log directory (mirrors scenario_set.copy_config_snapshot)
        if self._config.config_path and self._run_dir:
            ScenarioSetUtils(
                config_snapshot_path=self._config.config_path,
                scenario_log_path=self._run_dir,
                file_name_prefix='autotrader',
            ).copy_config_snapshot()

        try:
            self._setup_signal_handlers()

            # === PIPELINE ===
            # Pipeline objects get session_logger — they produce per-tick output.
            # Startup phases are logged to console via _print_startup_phase().
            self._print_startup_phase('Creating pipeline objects...')
            (self._executor,
             self._bar_controller,
             self._worker_orchestrator,
             self._decision_logic,
             self._clipping_monitor,
             self._trading_model,
             self._display_label_cache) = setup_pipeline(self._config, self._session_logger)
            self._print_startup_phase('Pipeline created successfully')

            # === DRIFT AUDIT (#327) ===
            # Gated by config; live-only by design — DRYRUN orders auto-skipped
            # inside the auditor. MOCK orders are audited (useful for tests).
            if self._config.drift_audit.enabled and isinstance(self._executor, LiveTradeExecutor):
                self._drift_auditor = DriftAuditor(
                    executor=self._executor,
                    config=self._config.drift_audit,
                    logger=self._session_logger,
                )

            # === DECISION EVENT CHANNEL (#348) ===
            # Built only when the active decision logic subscribes to events.
            self._decision_event_dispatcher = DecisionEventDispatcher.create_if_subscribed(
                decision_logic=self._decision_logic,
                executor=self._executor,
                logger=self._session_logger,
            )

            # === TICK SOURCE ===
            self._print_startup_phase('Starting tick source...')
            _symbol_spec = self._executor.broker.adapter.get_symbol_specification(
                self._config.symbol
            )
            self._tick_source, self._tick_thread = setup_tick_source(
                self._config,
                self._tick_queue,
                _symbol_spec.base_currency,
                _symbol_spec.quote_currency,
                self._global_logger,
            )
            self._print_startup_phase('Tick source running')

            # === DISPLAY (#228) ===
            dry_run = self._config.adapter_type == 'mock' or self._is_dry_run()
            if self._config.display.enabled:
                self._display_queue = queue.Queue(maxsize=10)
                self._display = AutoTraderLiveDisplay(
                    display_queue=self._display_queue,
                    tick_source=self._tick_source,
                    config=self._config,
                    dry_run=dry_run,
                    display_label_cache=self._display_label_cache,
                )
                self._display.start()
                self._global_logger.info('📺 Live display started')

            # === TICK LOOP ===
            self._global_logger.info('🔄 Entering tick loop...')
            self._print_startup_phase('Entering tick loop')
            self._running = True

            self._tick_loop = AutotraderTickLoop(
                config=self._config,
                tick_queue=self._tick_queue,
                tick_source=self._tick_source,
                executor=self._executor,
                bar_controller=self._bar_controller,
                worker_orchestrator=self._worker_orchestrator,
                decision_logic=self._decision_logic,
                clipping_monitor=self._clipping_monitor,
                logger=self._session_logger,
                trading_model=self._trading_model,
                run_dir=self._run_dir,
                display_queue=self._display_queue,
                session_start=run_timestamp,
                dry_run=dry_run,
                display_label_cache=self._display_label_cache,
                drift_auditor=self._drift_auditor,
                decision_event_dispatcher=self._decision_event_dispatcher,
            )
            ticks_processed, ticks_clipped = self._tick_loop.run()

            # #348: an EMERGENCY session-end request escalates the shutdown mode.
            if (self._executor.is_session_end_requested()
                    and self._executor.get_session_end_severity() == SessionEndSeverity.EMERGENCY):
                self._shutdown_mode = 'emergency'

        except Exception as e:
            self._global_logger.error(f"❌ AutoTrader error during setup/loop: {e}")
            self._print_startup_error(str(e))
            self._shutdown_mode = 'emergency'
            ticks_processed = 0
            ticks_clipped = 0

        # === SHUTDOWN ===
        return self._shutdown(ticks_processed, ticks_clipped)

    # =========================================================================
    # STARTUP CONSOLE OUTPUT
    # =========================================================================

    def _print_startup_banner(self) -> None:
        """Print startup banner directly to console."""
        session_name = self._config.name or self._config.symbol
        print(f"\n{'=' * 60}")
        print(f"  🚀 FiniexAutoTrader — {session_name}")
        print(f"  Symbol: {self._config.symbol} | Broker: {self._config.broker_type}")
        print(f"  Adapter: {self._config.adapter_type}")
        if self._run_dir:
            print(f"  Log dir: {self._run_dir}")
        print(f"{'=' * 60}")

    def _print_startup_phase(self, message: str) -> None:
        """Print startup phase message directly to console and to global log."""
        print(f"  ▸ {message}")

    def _print_startup_error(self, message: str) -> None:
        """Print startup error to console. Startup errors abort the session."""
        print(f"\n{'=' * 60}")
        print(f"  ❌ STARTUP FAILED")
        print(f"  {message}")
        print(f"{'=' * 60}\n")

    # =========================================================================
    # SHUTDOWN
    # =========================================================================

    def _shutdown(self, ticks_processed: int, ticks_clipped: int) -> AutoTraderResult:
        """
        Execute shutdown sequence and collect results.

        Args:
            ticks_processed: Total ticks processed in tick loop
            ticks_clipped: Total ticks that experienced clipping

        Returns:
            AutoTraderResult with session statistics
        """
        self._running = False
        self._global_logger.info(
            f"🛑 Shutdown initiated: mode={self._shutdown_mode}"
        )

        # Stop tick source
        if self._tick_source:
            self._tick_source.stop()
        if self._tick_thread and self._tick_thread.is_alive():
            self._tick_thread.join(timeout=5.0)

        # Stop display (before position cleanup prints to console)
        if self._display:
            self._display.stop()
            self._global_logger.info('📺 Live display stopped')

        # Close all open positions
        if self._executor:
            try:
                self._executor.close_all_remaining_orders()
                self._executor.check_clean_shutdown()
            except Exception as e:
                self._global_logger.error(f"Error during position cleanup: {e}")

        # #327 — Drift auditor cleanup (surfaces unfinished audits + final summary)
        if self._drift_auditor:
            try:
                self._drift_auditor.shutdown()
            except Exception as e:
                self._global_logger.error(f"Error during drift auditor shutdown: {e}")

        # Collect statistics and produce reports
        return self._collect_results(ticks_processed, ticks_clipped)

    def _collect_results(self, ticks_processed: int, ticks_clipped: int) -> AutoTraderResult:
        """
        Collect all statistics into AutoTraderResult and produce reports.

        Args:
            ticks_processed: Total ticks processed
            ticks_clipped: Total clipped ticks

        Returns:
            AutoTraderResult
        """
        session_duration = time.monotonic() - self._session_start

        # Collect warning/error counts + messages from session logger before closing
        warnings = self._session_logger.get_buffer_warnings()
        errors = self._session_logger.get_buffer_errors()

        result = AutoTraderResult(
            session_duration_s=session_duration,
            ticks_processed=ticks_processed,
            ticks_clipped=ticks_clipped,
            shutdown_mode=self._shutdown_mode,
            warning_messages=[line for _, line in warnings],
            error_messages=[line for _, line in errors],
        )

        if self._executor:
            try:
                result.portfolio_stats = self._executor.portfolio.get_portfolio_statistics()
                result.execution_stats = self._executor.get_execution_stats()
                result.trade_history = self._executor.get_trade_history()
                result.order_history = self._executor.get_order_history()
            except Exception as e:
                self._global_logger.error(f"Error collecting executor stats: {e}")

        if self._decision_logic:
            try:
                result.decision_statistics = self._decision_logic.get_statistics()
            except Exception as e:
                self._global_logger.error(f"Error collecting decision stats: {e}")

        if self._worker_orchestrator:
            try:
                result.worker_statistics = self._worker_orchestrator.get_worker_statistics()
            except Exception as e:
                self._global_logger.error(f"Error collecting worker stats: {e}")

        if self._clipping_monitor:
            result.clipping_summary = self._clipping_monitor.get_session_summary()

        # === REPORTS ===
        # Long-format event-stream CSV (#330) — replaces the previous
        # autotrader_orders.csv + autotrader_trades.csv pair with a single
        # chronological events.csv (FIX ExecutionReport style).
        EventStreamWriter.from_autotrader_result(
            trade_history=result.trade_history or [],
            order_history=result.order_history or [],
            run_dir=self._run_dir,
        ).flush('events.csv')

        post_session_report = AutotraderPostSessionReport(
            summary_logger=self._summary_logger,
            global_logger=self._global_logger,
        )
        post_session_report.print_report(result)

        # Close all loggers
        self._global_logger.info('🏁 Session complete — loggers closing')
        self._session_logger.close()
        self._summary_logger.close()
        self._global_logger.close()

        return result

    # =========================================================================
    # SIGNAL HANDLING
    # =========================================================================

    def _setup_signal_handlers(self) -> None:
        """
        Register signal handlers for graceful shutdown.

        First Ctrl+C → normal shutdown (close positions).
        Second Ctrl+C within 3s → force exit.
        SIGTERM → normal shutdown.
        """
        signal.signal(signal.SIGINT, self._handle_interrupt)
        signal.signal(signal.SIGTERM, self._handle_terminate)

    def _handle_interrupt(self, signum: int, frame: Any) -> None:
        """
        Handle SIGINT (Ctrl+C).

        First interrupt: normal shutdown.
        Second interrupt within 3s: force exit.
        """
        now = time.monotonic()
        if self._first_interrupt_time > 0 and (now - self._first_interrupt_time) < 3.0:
            # Second Ctrl+C within 3s → force exit
            print('\n⚡ Force exit — second interrupt received')
            sys.exit(1)

        self._first_interrupt_time = now
        self._shutdown_mode = 'emergency'
        self._running = False
        print('\n⚠️  Shutdown initiated — closing positions (Ctrl+C again within 3s to force)')
        # Stop tick loop if running
        if self._tick_loop:
            self._tick_loop.stop()
        if self._global_logger:
            self._global_logger.info('⚠️  Interrupt received — shutting down (Ctrl+C again within 3s to force)')

    def _handle_terminate(self, signum: int, frame: Any) -> None:
        """Handle SIGTERM — normal shutdown."""
        self._shutdown_mode = 'normal'
        self._running = False
        # Stop tick loop if running
        if self._tick_loop:
            self._tick_loop.stop()
        if self._global_logger:
            self._global_logger.info('🛑 SIGTERM received — normal shutdown')

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _is_dry_run(self) -> bool:
        """
        Determine if the session is a dry-run based on market config.

        Mock adapter is always dry-run. Live adapter reads the dry_run flag
        from market_config.json for the broker type.

        Returns:
            True if dry-run mode
        """
        if self._config.adapter_type == 'mock':
            return True
        return MarketConfigManager().get_dry_run(self._config.broker_type)
