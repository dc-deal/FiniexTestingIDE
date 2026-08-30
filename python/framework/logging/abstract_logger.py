"""
FiniexTestingIDE - Abstract Logger Base Class
Base class for GlobalLogger and ScenarioLogger

Provides:
- Log level validation and filtering
- Color codes for console output
- Error handling methods (validation_error, config_error, hard_error)
- Abstract _log() method for different implementations

Subclasses must implement:
- _log_console_implementation(record) - Console strategy (buffer vs direct print)
- _write_to_file_implementation(record) - File sink
- _render_timestamp(record) - Timestamp column (elapsed vs absolute)
"""

import sys
import traceback
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from python.configuration.app_config_manager import AppConfigManager
from python.framework.types.config_types.console_logging_config_types import ConsoleLoggingConfig
from python.framework.types.config_types.file_logging_config_types import FileLoggingConfig
from python.framework.types.log_level import ColorCodes, LogLevel
from python.framework.types.log_record_types import LogRecord
from python.framework.utils.time_utils import format_log_elapsed, format_log_event_time

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
    - _log_console_implementation(record) - Different buffering/output strategies
    - _render_timestamp(record) - Different timestamp formats
    """

    def __init__(self, name: str, event_time_column: bool = False):
        """
        Initialize abstract logger.

        Args:
            name: Logger name/identifier
            event_time_column: Whether this log lies on the run's own time axis and therefore
                renders the event-time column. A ROLE decided at construction — separate from
                whether a clock is attached yet, because "this log has no time axis" and "the
                clock has not started" are different facts that would otherwise both read as
                an absent time
        """
        self.name = name
        self._event_time_column = event_time_column
        # The canonical clock, attached once the executor exists (attach_clock). Until then a
        # line on a time-axis log renders the filler — never a wall-clock substitute (§9).
        self._clock_fn: Optional[Callable[[], Optional[datetime]]] = None

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
    def _write_to_file_implementation(self, record: LogRecord):
        """
        Write the record to this logger's file sink.

        Args:
            record: The entry to write
        """
        pass

    @abstractmethod
    def _render_timestamp(self, record: LogRecord) -> str:
        """
        Render the record's OBSERVATION timestamp as this logger's column.

        Different implementations:
        - GlobalLogger: DateTime string (e.g., "2025-10-22 14:30:45")
        - ScenarioLogger: Elapsed time (e.g., "[ 3s 417ms]")

        Taken from the record rather than read fresh: one clock read per log call, so the file
        line and the console line can never disagree about when the entry was observed.

        Args:
            record: The entry whose timestamp to render

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
                     run_start: Optional[datetime] = None,
                     effective_level: Optional[LogLevel] = None,
                     event_column: bool = False):
        """
        Print buffered records that were obtained via get_records().

        Used in the parent process after collecting logs from workers, where no logger instance
        exists — which is why the elapsed form and the console threshold need passing in.

        The level filter is load-bearing, exactly as it is in ScenarioLogger.flush_buffer:
        WARNING and ERROR are buffered past the console threshold because the run report needs
        them, and they are removed again HERE, at display time. Without it, raising the console
        threshold stops hiding warnings on this surface.

        Args:
            buffer: The records to print
            scenario_name: Name for the header; None prints a generic header
            run_start: Run start for the elapsed timestamp form; None prints absolute UTC
            effective_level: Console threshold to apply; None prints every record
            event_column: Whether these records come from a log on the run's own time axis
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
            if effective_level is not None and not LogLevel.should_log(record.level, effective_level):
                continue
            print(AbstractLogger.render_record(record, run_start, event_column))
        print(f"{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}")

    @staticmethod
    def render_record(record: LogRecord, run_start: Optional[datetime] = None,
                      event_column: bool = False, colored: bool = True) -> str:
        """
        Render one record the way a surface shows it.

        Args:
            record: The record to render
            run_start: Run start for the elapsed form; None renders the absolute UTC form
            event_column: Whether to render the run's own time as its own column
            colored: ANSI colours — the console wants them, a log file does not

        Returns:
            The formatted line
        """
        timestamp = (format_log_elapsed((record.timestamp - run_start).total_seconds())
                     if run_start else record.timestamp.strftime('%Y-%m-%d %H:%M:%S'))
        return AbstractLogger.format_line(
            timestamp, record.level, record.message,
            event_time=record.event_time, event_column=event_column, colored=colored)

    @staticmethod
    def format_line(timestamp: str, level: LogLevel, message: str,
                    event_time: Optional[datetime] = None, event_column: bool = False,
                    colored: bool = True) -> str:
        """
        The ONE line formula — console and file both go through it.

        Two time columns, two questions: the timestamp is OBSERVATION time (how far into the
        run we were), the event column is EVENT time (what time it was in the market). §9's
        ts_init / ts_event pair, rendered.

        Args:
            timestamp: Already-rendered observation timestamp (elapsed or absolute)
            level: The entry's level
            message: The unrendered message
            event_time: The run's own time, or None while no clock is attached
            event_column: Whether to render the event-time column at all
            colored: ANSI colours — the console wants them, a log file does not

        Returns:
            The formatted line
        """
        color = _LEVEL_COLORS.get(level, ColorCodes.RESET) if colored else ''
        reset = ColorCodes.RESET if colored else ''
        line = f'{timestamp} {color}{level:8}{reset} | '
        if event_column:
            line += f'{format_log_event_time(event_time)} | '
        return line + message

    # ============================================
    # Helper Methods
    # ============================================

    def attach_clock(self, clock_fn: Callable[[], Optional[datetime]]) -> None:
        """
        Attach the canonical clock, once the executor that owns it exists.

        Not a constructor argument, and it cannot be one: the logger is passed INTO the
        executor's construction in both pipelines, so it necessarily exists first. Everything
        logged before this point renders the filler — correct, because before the executor
        there is no run time a line could speak about.

        Args:
            clock_fn: Returns the run's current time, or None while the clock is unset
        """
        self._clock_fn = clock_fn

    def _event_time(self) -> Optional[datetime]:
        """
        The run's own time right now, pulled from the canonical clock.

        Pulled rather than pushed: a pull covers every kind of pass that advances the clock —
        tick, heartbeat, and the timer/resolution events #375 adds — without a call site per
        kind. The pushed variant is what left the live session log without a time column for
        as long as it has existed.

        Returns:
            The current run time, or None while no clock is attached
        """
        return self._clock_fn() if self._clock_fn is not None else None

    def _process_log(self, level: LogLevel, message: str):
        should_log_console = self._should_log_console(level)
        should_log_file = self._should_log_file(level)
        # Early exit (§16): building the record costs a clock read and an allocation, and a
        # suppressed verbose() inside the tick loop would pay for both and throw it away.
        if not (should_log_console or should_log_file or level in _REPORT_LEVELS):
            return
        # ONE clock read per log call. The file path renders its timestamp from this record
        # too, so the two surfaces cannot disagree about when the entry was observed.
        record = LogRecord(
            level=level, timestamp=datetime.now(timezone.utc), scope=self.name,
            message=message, event_time=self._event_time())
        if should_log_console:
            self._log_console_implementation(record)
        elif level in _REPORT_LEVELS:
            # WARNING and ERROR are REPORT INPUT, not console output — a display threshold must
            # not decide what the run report gets to see. Everything else follows the console
            # setting, as before.
            self._capture_for_report(record)
        if should_log_file:
            self._write_to_file_implementation(record)

    def _capture_for_report(self, record: LogRecord) -> None:
        """
        Keep a record the console did not show, because the run report still needs it.

        Base: keep nothing — a logger that prints directly has no buffer to keep it in.

        Args:
            record: The record to keep
        """
        return
