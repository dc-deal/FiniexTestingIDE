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

from datetime import datetime, timezone

from python.framework.logging.abstract_logger import AbstractLogger, ColorCodes
from python.framework.logging.file_logger import FileLogger
from python.framework.types.log_level import LogLevel
from python.framework.types.market_data_types import TickData
from python.framework.utils.time_utils import format_timestamp


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
                 run_timestamp: datetime
                 ):
        """
        Initialize scenario logger.

        Args:
            scenario_set_name: Scenario set name
            scenario_name: Scenario name (e.g., "GBPUSD_window_01")
            run_timestamp: Run timestamp string
        """
        super().__init__(name=scenario_name)

        self.scenario_set_name = scenario_set_name
        self.run_timestamp = run_timestamp
        run_timestamp_str = self.run_timestamp.strftime("%Y%m%d_%H%M%S")
        self._tick_loop_started = False
        self._current_tick = None
        self._tick_loop_count = 1

        self.run_dir = None
        self.file_logger = None

        if self._file_logging_config.scenario_enabled:
            # Create scenario run directory
            log_root = self._file_logging_config.scenario_log_root_path
            prefix = self._file_logging_config.scenario_file_name_prefix
            self.run_dir = log_root / scenario_set_name / run_timestamp_str
            self.run_dir.mkdir(parents=True, exist_ok=True)

            self.file_logger = FileLogger(
                log_filename=prefix+'_'+scenario_name+".log",
                file_path=self.run_dir,
                log_level=self._file_logging_config.scenario_log_level
            )
        else:
            # File logging disabled for scenarios
            pass

    def _get_timestamp(self) -> str:
        """
        Get elapsed time since scenario start.

        Returns:
            Elapsed time string (e.g., "[ 3s 417ms]")
        """
        elapsed = datetime.now(timezone.utc) - self.run_timestamp
        total_seconds = elapsed.total_seconds()
        seconds = int(total_seconds)
        milliseconds = int((total_seconds - seconds) * 1000)
        return f"[{seconds:3d}s {milliseconds:3d}ms]"

    def _should_log_console(self, level: LogLevel) -> str:
        """
        check if console log is enabled for logger
        """
        return LogLevel.should_log(
            level, self._console_logging_config.scenario_log_level)

    def _should_log_file(self, level: LogLevel) -> str:
        """
         check if file log is enabled for logger
        """
        return LogLevel.should_log(
            level, self._file_logging_config.scenario_log_level)

    def should_logLevel(self, level: LogLevel):
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

    def reset_start_time(self):
        self.start_time = datetime.now(timezone.utc)

    def set_tick_loop_started(self, started: bool):
        self._tick_loop_started = started
        self.file_logger.set_tick_loop_started(started)
        if (started):
            self._tick_loop_count = 1

    def set_current_tick(self, tick_count: int, tick: TickData):
        self.file_logger.set_current_tick(tick_count, tick)
        self._current_tick = tick
        self._tick_loop_count = tick_count

    def _log_console_implementation(self, level: str, message: str, timestamp: str):
        """
            Format Message for Scenario Log.

        Console: Buffered (flush at end)
        File: Direct write (no buffer)

        Args:
            level: Log level (INFO, DEBUG, WARNING, ERROR)
            message: Log message
        """
        if self._tick_loop_started:
            tick_time = format_timestamp(self._current_tick.timestamp)
            message = f"{self._tick_loop_count:5}| {tick_time} | {message}"
        formatted_line = self._format_log_line(level, message, timestamp)

        # Console output (BUFFERED) - for scenario loggers.
        # NO EXPLICIT CONSOLE PRINT. Scenario Buffers must be printet after scenario run.
        self._add_to_console_buffer(level, formatted_line)

    def _add_to_console_buffer(self, level: str, formatted_line: str):
        """
        Add log line to console buffer.

        Args:
            level: Log level
            formatted_line: Pre-formatted log line with colors
        """
        self.console_buffer.append((level, formatted_line))

    def _write_to_file_implementation(self, level: str, message: str, timestamp: str):
        """
        Write directly to scenario log file.

        Args:
            level: Log level
            message: Log message (plain text, no colors)
            timestamp: Elapsed time timestamp
        """
        if self.file_logger is not None:
            # Write to file (plain text format with elapsed time)
            self.file_logger.write_log(level, message, timestamp)

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
        header_text = f"📊 SCENARIO: {self.name}"
        print(f"{ColorCodes.BOLD}{header_text.center(60)}{ColorCodes.RESET}")
        print(f"{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}")

        # Output all buffered logs
        for level, formatted_line in self.console_buffer:
            should_log = LogLevel.should_log(
                level, self._console_logging_config.scenario_log_level)
            if should_log:
                print(formatted_line)

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

    def get_elapsed_time_seconds(self) -> float:
        """
        Get elapsed time since scenario start in seconds.

        Returns:
            Elapsed time in seconds
        """
        return (datetime.now(timezone.utc) - self.start_time).total_seconds()
