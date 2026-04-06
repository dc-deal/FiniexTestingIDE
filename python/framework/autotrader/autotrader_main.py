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
from python.framework.autotrader.reporting.autotrader_csv_file_report import AutotraderCsvFileReport
from python.framework.autotrader.reporting.autotrader_post_session_report import AutotraderPostSessionReport
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.utils.scenario_set_utils import ScenarioSetUtils
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
        self._executor = None
        self._bar_controller = None
        self._worker_orchestrator = None
        self._decision_logic = None
        self._clipping_monitor: Optional[LiveClippingMonitor] = None

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
             self._clipping_monitor) = setup_pipeline(self._config, self._session_logger)
            self._print_startup_phase('Pipeline created successfully')

            # === TICK SOURCE ===
            self._print_startup_phase('Starting tick source...')
            self._tick_source, self._tick_thread = setup_tick_source(
                self._config, self._tick_queue, self._global_logger
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
                run_dir=self._run_dir,
                display_queue=self._display_queue,
                session_start=run_timestamp,
                dry_run=dry_run,
            )
            ticks_processed, ticks_clipped = self._tick_loop.run()

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
        csv_report = AutotraderCsvFileReport(self._run_dir)
        csv_report.write(result)

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
        Determine if the session is a dry-run based on broker settings.

        Mock adapter is always dry-run. Live adapter checks
        broker_settings JSON for the dry_run flag (default: True).

        Returns:
            True if dry-run mode
        """
        if self._config.adapter_type == 'mock':
            return True
        if not self._config.broker_settings:
            return True
        try:
            from python.framework.autotrader.autotrader_startup import _load_broker_settings
            settings = _load_broker_settings(self._config.broker_settings)
            return settings.get('dry_run', True)
        except Exception:
            return True
