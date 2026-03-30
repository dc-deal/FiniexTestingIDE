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
from typing import Optional

from python.framework.autotrader.autotrader_startup import create_session_file_logger
from python.framework.autotrader.live_clipping_monitor import LiveClippingMonitor
from python.framework.autotrader.tick_sources.abstract_tick_source import AbstractTickSource
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig


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
        executor,
        bar_controller,
        worker_orchestrator,
        decision_logic,
        clipping_monitor: LiveClippingMonitor,
        logger: ScenarioLogger,
        run_dir: Optional[Path] = None,
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
        self._run_dir = run_dir
        self._running = False

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

    def run(self) -> tuple:
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
                    self._logger.info('📭 Tick source exhausted — ending session')
                    break
                continue

            # Sentinel value: None = tick source finished
            if tick is None:
                self._logger.info('📭 Tick source signaled end — ending session')
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

            # === 5. Order Execution ===
            self._decision_logic.execute_decision(decision, tick)

            # === TIMING END ===
            elapsed_ns = time.perf_counter_ns() - tick_start_ns

            # === 6. Clipping Monitor ===
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

        self._running = False
        return ticks_processed, ticks_clipped

    def _check_daily_rotation(self, tick) -> None:
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
