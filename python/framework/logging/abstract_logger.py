"""
FiniexTestingIDE - Abstract Logger Base Class
Base class for GlobalLogger and ScenarioLogger

Provides:
- Log level validation and filtering
- Color codes for console output
- Error handling methods (validation_error, config_error, hard_error)
- Abstract _log() method for different implementations

Subclasses must implement:
- _log(level, message) - Core logging logic
- _get_timestamp() - Timestamp format (datetime vs elapsed time)
"""

import sys
import traceback
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from python.configuration.app_config_manager import AppConfigManager
from python.framework.types.config_types.console_logging_config_types import ConsoleLoggingConfig
from python.framework.types.config_types.file_logging_config_types import FileLoggingConfig
from python.framework.types.log_level import ColorCodes, LogLevel
from python.framework.types.log_record_types import LogRecord
from python.framework.utils.time_utils import format_log_elapsed, format_timestamp

# The levels the run report reads. They are captured regardless of the CONSOLE threshold —
# a display setting must not decide what the report gets to see.
_REPORT_LEVELS = (LogLevel.WARNING, LogLevel.ERROR)

# One palette for every rendering path.
_LEVEL_COLORS = {
    LogLevel.VERBOSE: ColorCodes.PURPLE,
    LogLevel.DEBUG: ColorCodes.GRAY,
    LogLevel.INFO: ColorCodes.BLUE,
    LogLevel.WARNING: ColorCodes.YELLOW,
    LogLevel.ERROR: ColorCodes.RED,
}


class AbstractLogger(ABC):
    """
    Abstract base class for all loggers.

    Provides common functionality:
    - Log level validation
    - Filtering based on log levels
    - Error methods with auto-flush and exit
    - Color-coded console output

    Subclasses implement:
    - _log(level, message) - Different buffering/output strategies
    - _get_timestamp() - Different timestamp formats
    """

    def __init__(self, name: str):
        """
        Initialize abstract logger.

        Args:
            name: Logger name/identifier
        """
        self.name = name

        # Load config objects
        app_config = AppConfigManager()

        # For GlobalLogger: use console log level
        # For ScenarioLogger: use scenario log level (with inheritance)
        self._console_logging_config: ConsoleLoggingConfig = app_config.get_console_logging_config_object()
        self._file_logging_config: FileLoggingConfig = app_config.get_file_logging_config_object()

        # Will be set by subclasses (GlobalLogger vs ScenarioLogger)
        # Setup config from parent (uses global file logging config)
        self.file_logging_enabled = self._file_logging_config.global_enabled

        # Buffered log entries as RECORDS, not rendered lines — rendering happens at the surface
        # that prints them (print_buffer), so a later consumer never has to take a line apart.
        self.console_buffer: List[LogRecord] = []

    @abstractmethod
    def _log_console_implementation(self, record: LogRecord):
        """
        Core logging method - must be implemented by subclasses.

        Different implementations:
        - GlobalLogger: Direct console + file output
        - ScenarioLogger: Buffered console + direct file output

        Args:
            level: Log level (INFO, DEBUG, WARNING, ERROR)
            message: Log message
        """
        pass

    @abstractmethod
    def _write_to_file_implementation(self, level: str, message: str, timestamp: str):
        """
        Write to global log file.

        Args:
            level: Log level
            message: Log message (plain text, no colors)
            timestamp: DateTime timestamp
        """
        pass

    @abstractmethod
    def _get_timestamp(self) -> str:
        """
        Get timestamp string for log entry.

        Different implementations:
        - GlobalLogger: DateTime string (e.g., "2025-10-22 14:30:45")
        - ScenarioLogger: Elapsed time (e.g., "[ 3s 417ms]")

        Returns:
            Formatted timestamp string
        """
        pass

    @abstractmethod
    def _should_log_console(self, level: LogLevel) -> bool:
        """
        check if console log is enabled for logger
        """
        pass

    @abstractmethod
    def _should_log_file(self, level: LogLevel) -> bool:
        """
         check if file log is enabled for logger
        """
        pass

    # ============================================
    # Public Logging API
    # ============================================

    def verbose(self, message: str):
        """Log VERBOSE message - All Logs also Tick / Order Data"""
        self._process_log(LogLevel.VERBOSE, message)

    def debug(self, message: str):
        """Log DEBUG message Many Logs - also minor log events"""
        self._process_log(LogLevel.DEBUG, message)

    def info(self, message: str):
        """Log INFO message (respects log level filtering)"""
        self._process_log(LogLevel.INFO, message)

    def warning(self, message: str):
        """Log WARNING message (respects log level filtering)"""
        self._process_log(LogLevel.WARNING, message)

    def error(self, message: str):
        """Log ERROR message (respects log level filtering)"""
        self._process_log(LogLevel.ERROR, message)

    # ============================================
    # Critical Error Methods (Auto-Flush + Exit)
    # ============================================

    def validation_error(self, message: str, context: Optional[Dict[str, Any]] = None):
        """
        Parameter/Input validation error.

        Use for: Invalid user input, wrong parameter values, constraint violations.
        NO stack trace (error message should be self-explanatory).

        Args:
            message: Human-readable error description
            context: Optional dict with error context

        Behavior:
            - Flushes all buffered logs
            - Prints formatted error message
            - Exits with code 1
        """

        print(f"\n{ColorCodes.RED}{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}")
        print(f'{ColorCodes.RED}{ColorCodes.BOLD}❌ VALIDATION ERROR{ColorCodes.RESET}')
        print(f"{ColorCodes.RED}{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}")
        print(f'\n{ColorCodes.RED}{message}{ColorCodes.RESET}')

        if context:
            print(f'\n{ColorCodes.YELLOW}Context:{ColorCodes.RESET}')
            for key, value in context.items():
                print(f'  {key}: {value}')

        print(f"\n{ColorCodes.RED}{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}\n")
        sys.exit(1)

    def config_error(self, message: str, file_path: Optional[str] = None):
        """
        Configuration file error.

        Use for: Missing config files, invalid JSON, schema violations.
        NO stack trace.

        Args:
            message: Human-readable error description
            file_path: Optional path to problematic config file

        Behavior:
            - Flushes all buffered logs
            - Prints formatted error message
            - Exits with code 1
        """

        print(f"\n{ColorCodes.RED}{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}")
        print(
            f'{ColorCodes.RED}{ColorCodes.BOLD}⚙️ CONFIGURATION ERROR{ColorCodes.RESET}')
        print(f"{ColorCodes.RED}{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}")
        print(f'\n{ColorCodes.RED}{message}{ColorCodes.RESET}')

        if file_path:
            print(f'\n{ColorCodes.YELLOW}File: {file_path}{ColorCodes.RESET}')

        print(f"\n{ColorCodes.RED}{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}\n")
        sys.exit(1)

    def hard_error(self, message: str, exception: Optional[Exception] = None):
        """
        Critical code error (WITH stack trace).

        Use for: Unexpected exceptions, runtime errors, bugs.
        Shows full stack trace for debugging.

        Args:
            message: Human-readable error description
            exception: Optional exception object

        Behavior:
            - Flushes all buffered logs
            - Prints formatted error message with stack trace
            - Exits with code 1
        """

        print(f"\n{ColorCodes.RED}{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}")
        print(f'{ColorCodes.RED}{ColorCodes.BOLD}💥 CRITICAL ERROR{ColorCodes.RESET}')
        print(f"{ColorCodes.RED}{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}")
        print(f'\n{ColorCodes.RED}{message}{ColorCodes.RESET}')

        if exception:
            print(f'\n{ColorCodes.YELLOW}Exception:{ColorCodes.RESET}')
            print(f'{ColorCodes.RED}{str(exception)}{ColorCodes.RESET}')
            print(f'\n{ColorCodes.YELLOW}Stack Trace:{ColorCodes.RESET}')
            traceback.print_exc()

        print(f"\n{ColorCodes.RED}{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}\n")
        sys.exit(1)

    # ============================================
    # Buffer Serialization / Cross-Process Support
    # ============================================

    def get_records(self, level: Optional[LogLevel] = None) -> list[LogRecord]:
        """
        Return the buffered records, optionally of one level only.

        Records are picklable dataclasses, so the result is safe to pass across processes.

        Args:
            level: Restrict to this level; None returns every buffered record

        Returns:
            The buffered records, in the order they were logged
        """
        if level is None:
            return list(self.console_buffer)
        return [record for record in self.console_buffer if record.level == level]

    @staticmethod
    def print_buffer(buffer: list[LogRecord], scenario_name: str = None,
                     run_start: Optional[datetime] = None):
        """
        Print buffered records that were obtained via get_records().

        Used in the parent process after collecting logs from workers, where no logger instance
        exists — which is why the elapsed form needs run_start passed in.

        Args:
            buffer: The records to print
            scenario_name: Name for the header; None prints a generic header
            run_start: Run start for the elapsed timestamp form; None prints absolute UTC
        """
        if not buffer:
            print('(empty log buffer)')
            return
        print(f"\n{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}")
        if scenario_name:
            print(
                f"{ColorCodes.BOLD}{(' SCENARIO '+scenario_name).center(60)}{ColorCodes.RESET}")
        else:
            print(
                f"{ColorCodes.BOLD}{' SCENARIO LOG BUFFER '.center(60)}{ColorCodes.RESET}")
        print(f"{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}")
        for record in buffer:
            print(AbstractLogger.render_record(record, run_start))
        print(f"{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}")

    @staticmethod
    def render_record(record: LogRecord, run_start: Optional[datetime] = None) -> str:
        """
        Render one record the way the console shows it.

        Args:
            record: The record to render
            run_start: Run start for the elapsed form; None renders the absolute UTC form

        Returns:
            The formatted line, colours included
        """
        timestamp = (format_log_elapsed((record.timestamp - run_start).total_seconds())
                     if run_start else record.timestamp.strftime('%Y-%m-%d %H:%M:%S'))
        message = record.message
        if record.tick_index is not None:
            # The tick prefix is presentation too — it is rebuilt here from the record's own
            # fields, so the buffered message stays the bare fact the report reads.
            message = (f'{record.tick_index:5}| '
                       f'{format_timestamp(record.tick_time)} | {message}')
        return AbstractLogger.format_line(timestamp, record.level, message)

    @staticmethod
    def format_line(timestamp: str, level: LogLevel, message: str) -> str:
        """
        The ONE console line formula — every rendering path goes through it.

        Args:
            timestamp: Already-rendered timestamp (elapsed or absolute)
            level: The entry's level
            message: The unrendered message

        Returns:
            The formatted line, colours included
        """
        color = _LEVEL_COLORS.get(level, ColorCodes.RESET)
        return f'{timestamp} {color}{level:8}{ColorCodes.RESET} | {message}'

    # ============================================
    # Helper Methods
    # ============================================

    def _get_color_for_level(self, level: str) -> str:
        """Get ANSI color code for log level"""
        return _LEVEL_COLORS.get(level, ColorCodes.RESET)

    def _process_log(self, level: LogLevel, message: str):
        should_log_console = self._should_log_console(level)
        should_log_file = self._should_log_file(level)
        timestamp_implemenration = self._get_timestamp()
        tick_index, tick_time = self._tick_context()
        record = LogRecord(
            level=level, timestamp=datetime.now(timezone.utc), scope=self.name,
            message=message, tick_index=tick_index, tick_time=tick_time)
        if should_log_console:
            self._log_console_implementation(record)
        elif level in _REPORT_LEVELS:
            # WARNING and ERROR are REPORT INPUT, not console output — a display threshold must
            # not decide what the run report gets to see. Everything else follows the console
            # setting, as before.
            self._capture_for_report(record)
        if should_log_file:
            self._write_to_file_implementation(
                level, message, timestamp_implemenration)

    def _tick_context(self) -> tuple[Optional[int], Optional[datetime]]:
        """
        The tick being processed, when the logger is inside a tick loop.

        Returns:
            (tick_index, tick_time); (None, None) for a logger with no tick loop
        """
        return None, None

    def _capture_for_report(self, record: LogRecord) -> None:
        """
        Keep a record the console did not show, because the run report still needs it.

        Base: keep nothing — a logger that prints directly has no buffer to keep it in.

        Args:
            record: The record to keep
        """
        return

    def _format_log_line(self, level: str, message: str, timestamp: str) -> str:
        """
        Format a log line with color and timestamp.

        Args:
            level: Log level
            message: Log message
            timestamp: Timestamp string (format depends on subclass)

        Returns:
            Formatted log line
        """
        color = self._get_color_for_level(level)
        reset = ColorCodes.RESET

        return f'{timestamp} {color}{level:8}{reset} | {message}'
