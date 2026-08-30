"""
FiniexTestingIDE - Scenario Logger
Logger for scenario-specific logs (workers, decisions, execution)

Characteristics:
- Elapsed time timestamps (relative to scenario start)
- Console buffering (flush at end for clean output)
- Direct file output (per scenario file)
- One instance per scenario

Usage:
    # Created automatically by SingleScenario
    scenario.logger.info("Worker initialized")
    
    # Flush at end
    scenario.logger.flush_buffer()
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from python.framework.logging.abstract_logger import AbstractLogger, ColorCodes
from python.framework.logging.file_logger import FileLogger
from python.framework.types.log_level import LogLevel
from python.framework.types.log_record_types import LogRecord
from python.framework.utils.time_utils import format_log_elapsed


class ScenarioLogger(AbstractLogger):
    """
    Scenario-specific logger with buffered console output.

    Features:
    - Elapsed time timestamps (e.g., "[ 3s 417ms]")
    - Console buffering (prevents chaos during parallel execution)
    - Direct file output (one file per scenario)
    - Automatic flush on errors
    """

    def __init__(self,
                 scenario_set_name: str,
                 scenario_name: str,
                 run_timestamp: datetime,
                 log_root_override: Optional[Path] = None,
                 file_name_prefix_override: Optional[str] = None,
                 use_global_log_level_for_console: bool = False,
                 use_scenario_logs_subdir: bool = False,
                 event_time_column: bool = False,
                 flat_log_filename: Optional[str] = None
                 ):
        """
        Initialize scenario logger.

        Args:
            scenario_set_name: Scenario set name
            scenario_name: Scenario name (e.g., "GBPUSD_window_01")
            run_timestamp: Run timestamp string
            log_root_override: Custom log root path (bypasses config). Used by AutoTrader for separate log tree.
            file_name_prefix_override: Custom file name prefix (bypasses config). E.g., 'autotrader' → autotrader_<name>.log
            use_scenario_logs_subdir: Place log file in scenario_logs/ subdir (backtesting per-scenario logs only)
            event_time_column: This log lies on the run's own time axis, so every line carries
                the event-time column. True for the per-scenario logs and the live session log;
                false for the run-level logs (global, summary, system info), which describe the
                run from outside rather than from a moment inside it
            flat_log_filename: Write this ONE file directly into log_root_override instead of
                opening a <owner>/<run_timestamp>/ run directory. For output that describes
                something other than a run — today the shared data load a sweep performs once
                for all its combinations. Without it, work that is not a run leaves a directory
                shaped like one, and the API's run index has no way to tell the two apart
        """
        super().__init__(name=scenario_name, event_time_column=event_time_column)

        self.scenario_set_name = scenario_set_name
        self.run_timestamp = run_timestamp
        run_timestamp_str = self.run_timestamp.strftime('%Y%m%d_%H%M%S')
        self._use_global_log_level_for_console = use_global_log_level_for_console

        self.run_dir = None
        self.file_logger = None

        if self._file_logging_config.scenario_enabled:
            # Create scenario run directory
            # The caller hands over the category root it belongs to — a live session, a
            # standalone run, or one sweep's combinations. The three come from config
            # (file_logging.run_logs), which is the same source the API reads.
            log_root = (log_root_override if log_root_override
                        else self._file_logging_config.run_logs.single_runs)
            prefix = file_name_prefix_override if file_name_prefix_override else self._file_logging_config.scenario_file_name_prefix

            if flat_log_filename:
                # One file, straight into the given root. No run directory is opened, because
                # what is being logged here is not a run.
                log_dir = log_root
                log_dir.mkdir(parents=True, exist_ok=True)
                log_filename = flat_log_filename
            else:
                self.run_dir = log_root / scenario_set_name / run_timestamp_str
                self.run_dir.mkdir(parents=True, exist_ok=True)

                # Per-scenario files go into scenario_logs/ subdir (backtesting only)
                log_dir = self.run_dir / 'scenario_logs' if use_scenario_logs_subdir else self.run_dir
                if use_scenario_logs_subdir:
                    log_dir.mkdir(exist_ok=True)
                log_filename = prefix + '_' + scenario_name + '.log'

            self.file_logger = FileLogger(
                log_filename=log_filename,
                file_path=log_dir,
                log_level=self._file_logging_config.scenario_log_level
            )
        else:
            # File logging disabled for scenarios
            pass

    def _render_timestamp(self, record: LogRecord) -> str:
        """
        Render the record's observation time as elapsed time since scenario start.

        Args:
            record: The entry whose timestamp to render

        Returns:
            Elapsed time string (e.g., "[ 3s 417ms]")
        """
        return format_log_elapsed((record.timestamp - self.run_timestamp).total_seconds())

    def _should_log_console(self, level: LogLevel) -> bool:
        """
        check if console log is enabled for logger
        """
        effective_level = (
            self._console_logging_config.global_log_level
            if self._use_global_log_level_for_console
            else self._console_logging_config.scenario_log_level
        )
        return LogLevel.should_log(level, effective_level)

    def _should_log_file(self, level: LogLevel) -> bool:
        """
         check if file log is enabled for logger
        """
        return LogLevel.should_log(
            level, self._file_logging_config.scenario_log_level)

    def should_log_level(self, level: LogLevel):
        """
        check if any log is active - usecase: scenario silent mode (only file log)
        """
        should_log_console = self._console_logging_config.should_log_scenarios(
        ) and self._should_log_console(level)
        should_log_file = self._file_logging_config.is_file_logging_enabled(
        ) and self._should_log_file(level)
        return should_log_console or should_log_file

    def get_run_timestamp(self):
        return self.run_timestamp

    def get_log_dir(self):
        return self.run_dir

    def _log_console_implementation(self, record: LogRecord):
        """
        Buffer the record for the scenario log.

        Console: Buffered (flush at end) — NO explicit print. Scenario buffers are printed
        after the scenario run, so parallel scenarios do not interleave their output.
        File: Direct write (no buffer), handled separately.

        Args:
            record: The entry to buffer
        """
        self.console_buffer.append(record)

    def _capture_for_report(self, record: LogRecord) -> None:
        """
        Keep a WARNING/ERROR the console threshold suppressed — the run report still needs it.

        Args:
            record: The entry to keep
        """
        self.console_buffer.append(record)

    def _write_to_file_implementation(self, record: LogRecord):
        """
        Write directly to scenario log file.

        The elapsed timestamp is rendered HERE, not in FileLogger: it is measured against this
        logger's run_timestamp, which the file sink does not have.

        Args:
            record: The entry to write
        """
        if self.file_logger is not None:
            # Write to file (plain text, elapsed time, no colours)
            self.file_logger.write_log(
                record, self._render_timestamp(record), self._event_time_column)

    def flush_buffer(self):
        """
        Flush console buffer to stdout.

        Called at end of scenario execution or on errors.
        Outputs all buffered logs in order.
        """
        if not self.console_buffer:
            return

        # Print scenario header
        print(f"\n{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}")
        header_text = f'📊 SCENARIO: {self.name}'
        print(f'{ColorCodes.BOLD}{header_text.center(60)}{ColorCodes.RESET}')
        print(f"{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}")

        # Output all buffered logs
        effective_level = (
            self._console_logging_config.global_log_level
            if self._use_global_log_level_for_console
            else self._console_logging_config.scenario_log_level
        )
        # The filter is load-bearing: WARNING/ERROR are buffered even when the console
        # threshold suppresses them, because the run report needs them. They are removed
        # HERE, at display time, so the console output is unchanged by that wider capture.
        for record in self.console_buffer:
            if LogLevel.should_log(record.level, effective_level):
                print(AbstractLogger.render_record(
                    record, run_start=self.run_timestamp,
                    event_column=self._event_time_column))

        # Clear buffer
        self.console_buffer.clear()

    def close(self, flush_buffer: bool = False):
        """
        Close logger and flush any remaining buffers.

        Call at end of scenario execution.
        """
        # Flush console buffer
        if flush_buffer:
            self.flush_buffer()
        self.console_buffer.clear()

        # Close file logger
        if self.file_logger:
            self.file_logger.close()

    def swap_file_logger(self, new_file_logger: FileLogger) -> None:
        """
        Replace the current file logger with a new one.

        Used by AutoTrader for daily session log rotation:
        when the date changes, the tick loop creates a new FileLogger
        pointing to a new day's file and swaps it in.

        Args:
            new_file_logger: New FileLogger instance (already opened)
        """
        if self.file_logger:
            self.file_logger.close()
        self.file_logger = new_file_logger

