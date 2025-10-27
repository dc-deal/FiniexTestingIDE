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

from python.components.logger.abstract_logger import AbstractLogger, ColorCodes
from python.components.logger.file_logger import FileLogger
from python.framework.types.log_level import LogLevel
from python.components.logger.file_logger import FileLogger


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
            scenario_name: Scenario name (e.g., "GBPUSD_window_01")
            run_dir: Directory for log files (created if None)
        """
        super().__init__(name=scenario_name)

        self.scenario_set_name = scenario_set_name
        self.run_timestamp = run_timestamp

        self.run_dir = None
        self.file_logger = None
        if self.file_logging_enabled:
            # Create default run directory
            self.run_dir = Path(self.file_log_root) / \
                self.scenario_set_name / self.run_timestamp

            self.run_dir.mkdir(parents=True, exist_ok=True)

            self.file_logger = FileLogger(
                log_type="scenario",
                run_dir=self.run_dir,
                scenario_name=self.name,
                log_level=self.file_log_level
            )

    def get_log_dir(self):
        return self.run_dir

    def reset_start_time(self, prepare_hint: str):
        self.start_time = datetime.now()
        self._log(LogLevel.DEBUG, "🚀 Starting Scenario " +
                  self.name+" Log Timer ("+prepare_hint+").")

    def _get_timestamp(self) -> str:
        """
        Get elapsed time since scenario start.

        Returns:
            Elapsed time string (e.g., "[ 3s 417ms]")
        """
        elapsed = datetime.now() - self.start_time
        total_seconds = elapsed.total_seconds()
        seconds = int(total_seconds)
        milliseconds = int((total_seconds - seconds) * 1000)
        return f"[{seconds:3d}s {milliseconds:3d}ms]"

    def _log(self, level: str, message: str):
        """
        Log message with buffered console and direct file output.

        Console: Buffered (flush at end)
        File: Direct write (no buffer)

        Args:
            level: Log level (INFO, DEBUG, WARNING, ERROR)
            message: Log message
        """
        timestamp = self._get_timestamp()
        formatted_line = self._format_log_line(level, message, timestamp)

        # Console output (BUFFERED)
        self._add_to_console_buffer(level, formatted_line)

        # File output (DIRECT - if enabled)
        if self.file_logging_enabled and LogLevel.should_log(level, self.file_log_level):
            self._write_to_file(level, message, timestamp)

    def _add_to_console_buffer(self, level: str, formatted_line: str):
        """
        Add log line to console buffer.

        Args:
            level: Log level
            formatted_line: Pre-formatted log line with colors
        """
        self.console_buffer.append((level, formatted_line))

    def _write_to_file(self, level: str, message: str, timestamp: str):
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
            print(formatted_line)

        # Clear buffer
        self.console_buffer.clear()

    def close(self):
        """
        Close logger and flush any remaining buffers.

        Call at end of scenario execution.
        """
        # Flush console buffer
        self.flush_buffer()

        # Close file logger
        if self.file_logger:
            self.file_logger.close()

    def get_elapsed_time_seconds(self) -> float:
        """
        Get elapsed time since scenario start in seconds.

        Returns:
            Elapsed time in seconds
        """
        return (datetime.now() - self.start_time).total_seconds()
