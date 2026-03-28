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
from typing import Any, Optional

from python.framework.autotrader.autotrader_tick_loop import AutotraderTickLoop
from python.framework.autotrader.tick_sources.abstract_tick_source import AbstractTickSource
from python.framework.autotrader.autotrader_startup import (
    create_autotrader_logger,
    setup_pipeline,
    setup_tick_source,
)
from python.framework.autotrader.live_clipping_monitor import LiveClippingMonitor
from python.framework.autotrader.reporting.autotrader_csv_file_report import AutotraderCsvFileReport
from python.framework.autotrader.reporting.autotrader_post_session_report import AutotraderPostSessionReport
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult


class AutotraderMain:
    """
    Live trading runner for FiniexTestingIDE.

    Mirrors the backtesting process_tick_loop but for live execution:
    - Tick source runs in a separate thread (Threading model 8.a)
    - Main thread processes ticks synchronously: on_tick → bars → workers → decision
    - Workers and DecisionLogic are the same classes as in backtesting

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
        self._logger: Optional[ScenarioLogger] = None

        # Signal handling state
        self._first_interrupt_time: float = 0.0

    def run(self) -> AutoTraderResult:
        """
        Execute the complete AutoTrader session.

        Flow:
        1. Setup: load config, create pipeline objects
        2. Start tick source thread
        3. Tick loop: queue.get → on_tick → bars → workers → decision
        4. Shutdown: close positions, collect statistics

        Returns:
            AutoTraderResult with session statistics
        """
        self._session_start = time.monotonic()
        run_timestamp = datetime.now(timezone.utc)

        # === SETUP ===
        self._logger = create_autotrader_logger(self._config, run_timestamp)
        self._logger.info(
            f"🚀 AutotraderMain starting: {self._config.symbol} "
            f"({self._config.broker_type}, adapter={self._config.adapter_type})"
        )

        try:
            self._setup_signal_handlers()

            (self._executor,
             self._bar_controller,
             self._worker_orchestrator,
             self._decision_logic,
             self._clipping_monitor) = setup_pipeline(self._config, self._logger)

            self._tick_source, self._tick_thread = setup_tick_source(
                self._config, self._tick_queue, self._logger
            )

            # === TICK LOOP ===
            self._logger.info('🔄 Entering tick loop...')
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
                logger=self._logger,
            )
            ticks_processed, ticks_clipped = self._tick_loop.run()

        except Exception as e:
            self._logger.error(f"❌ AutoTrader error during setup/loop: {e}")
            self._shutdown_mode = 'emergency'
            ticks_processed = 0
            ticks_clipped = 0

        # === SHUTDOWN ===
        return self._shutdown(ticks_processed, ticks_clipped)

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
        self._logger.info(
            f"🛑 Shutdown initiated: mode={self._shutdown_mode}"
        )

        # Stop tick source
        if self._tick_source:
            self._tick_source.stop()
        if self._tick_thread and self._tick_thread.is_alive():
            self._tick_thread.join(timeout=5.0)

        # Close all open positions
        if self._executor:
            try:
                self._executor.close_all_remaining_orders()
                self._executor.check_clean_shutdown()
            except Exception as e:
                self._logger.error(f"Error during position cleanup: {e}")

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

        result = AutoTraderResult(
            session_duration_s=session_duration,
            ticks_processed=ticks_processed,
            ticks_clipped=ticks_clipped,
            shutdown_mode=self._shutdown_mode,
        )

        if self._executor:
            try:
                result.portfolio_stats = self._executor.portfolio.get_portfolio_statistics()
                result.execution_stats = self._executor.get_execution_stats()
                result.trade_history = self._executor.get_trade_history()
                result.order_history = self._executor.get_order_history()
            except Exception as e:
                self._logger.error(f"Error collecting executor stats: {e}")

        if self._decision_logic:
            try:
                result.decision_statistics = self._decision_logic.get_statistics()
            except Exception as e:
                self._logger.error(f"Error collecting decision stats: {e}")

        if self._worker_orchestrator:
            try:
                result.worker_statistics = self._worker_orchestrator.get_worker_statistics()
            except Exception as e:
                self._logger.error(f"Error collecting worker stats: {e}")

        if self._clipping_monitor:
            result.clipping_summary = self._clipping_monitor.get_session_summary()

        # === REPORTS ===
        session_name = self._config.name or f'{self._config.symbol}_{self._config.adapter_type}'

        csv_report = AutotraderCsvFileReport(self._logger, session_name)
        csv_report.write(result)

        post_session_report = AutotraderPostSessionReport(self._logger, session_name)
        post_session_report.print_report(result)

        # Close logger (flush handled by post_session_report)
        self._logger.close()

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
        # Stop tick loop if running
        if self._tick_loop:
            self._tick_loop.stop()
        if self._logger:
            self._logger.info('⚠️  Interrupt received — shutting down (Ctrl+C again within 3s to force)')

    def _handle_terminate(self, signum: int, frame: Any) -> None:
        """Handle SIGTERM — normal shutdown."""
        self._shutdown_mode = 'normal'
        self._running = False
        # Stop tick loop if running
        if self._tick_loop:
            self._tick_loop.stop()
        if self._logger:
            self._logger.info('🛑 SIGTERM received — normal shutdown')
