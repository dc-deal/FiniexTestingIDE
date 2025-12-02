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
from typing import Any, Dict, List, Optional, Tuple

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.console_logging_config import ConsoleLoggingConfig
from python.configuration.file_logging_config import FileLoggingConfig
from python.framework.types.log_level import ColorCodes, LogLevel


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
        self.file_log_level = self._file_logging_config.global_log_level

        self.console_buffer: List[Tuple[str, str]] = []

    @abstractmethod
    def _log_console_implementation(self, level: str, message: str):
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
    def _should_log_console(self, level: LogLevel) -> str:
        """
        check if console log is enabled for logger
        """
        pass

    @abstractmethod
    def _should_log_file(self, level: LogLevel) -> str:
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
        print(f"{ColorCodes.RED}{ColorCodes.BOLD}❌ VALIDATION ERROR{ColorCodes.RESET}")
        print(f"{ColorCodes.RED}{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}")
        print(f"\n{ColorCodes.RED}{message}{ColorCodes.RESET}")

        if context:
            print(f"\n{ColorCodes.YELLOW}Context:{ColorCodes.RESET}")
            for key, value in context.items():
                print(f"  {key}: {value}")

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
            f"{ColorCodes.RED}{ColorCodes.BOLD}⚙️ CONFIGURATION ERROR{ColorCodes.RESET}")
        print(f"{ColorCodes.RED}{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}")
        print(f"\n{ColorCodes.RED}{message}{ColorCodes.RESET}")

        if file_path:
            print(f"\n{ColorCodes.YELLOW}File: {file_path}{ColorCodes.RESET}")

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
        print(f"{ColorCodes.RED}{ColorCodes.BOLD}💥 CRITICAL ERROR{ColorCodes.RESET}")
        print(f"{ColorCodes.RED}{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}")
        print(f"\n{ColorCodes.RED}{message}{ColorCodes.RESET}")

        if exception:
            print(f"\n{ColorCodes.YELLOW}Exception:{ColorCodes.RESET}")
            print(f"{ColorCodes.RED}{str(exception)}{ColorCodes.RESET}")
            print(f"\n{ColorCodes.YELLOW}Stack Trace:{ColorCodes.RESET}")
            traceback.print_exc()

        print(f"\n{ColorCodes.RED}{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}\n")
        sys.exit(1)

    # ============================================
    # Buffer Serialization / Cross-Process Support
    # ============================================

    def get_buffer(self) -> list[tuple[str, str]]:
        """
        Return a serializable copy of the console buffer.

        This makes it safe to pass across processes (e.g. via ProcessPoolExecutor).
        """
        # Ensure all entries are plain strings
        return [(str(level), str(line)) for level, line in self.console_buffer]

    def get_buffer_errors(self) -> list[tuple[str, str]]:
        """
        Return only ERROR entries from the console buffer.

        Returns:
            List of (level, line) tuples containing only ERROR entries
        """
        return [
            (level, line)
            for level, line in self.console_buffer
            if level == LogLevel.ERROR
        ]

    def get_buffer_warnings(self) -> list[tuple[str, str]]:
        """
        Return only WARNING entries from the console buffer.
        Returns:
            List of (level, line) tuples containing only ERROR entries
        """
        return [
            (level, line)
            for level, line in self.console_buffer
            if level == LogLevel.WARNING
        ]

    def set_buffer(self, buffer: list[tuple[str, str]]):
        """
        Replace the current console buffer with a provided list.
        """
        if not isinstance(buffer, list):
            raise ValueError("Expected a list of (level, line) tuples.")
        self.console_buffer = [(str(level), str(line))
                               for level, line in buffer]

    @staticmethod
    def print_buffer(buffer: list[tuple[str, str]], scenario_name: str = None):
        """
        Print a buffer that was obtained via get_buffer().

        Can be used in the parent process after collecting logs from workers.
        """
        if not buffer:
            print("(empty log buffer)")
            return
        print(f"\n{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}")
        if scenario_name:
            print(
                f"{ColorCodes.BOLD}{(' SCENARIO '+scenario_name).center(60)}{ColorCodes.RESET}")
        else:
            print(
                f"{ColorCodes.BOLD}{' SCENARIO LOG BUFFER '.center(60)}{ColorCodes.RESET}")
        print(f"{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}")
        for level, line in buffer:
            print(line)
        print(f"{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}")

    # ============================================
    # Helper Methods
    # ============================================

    def _get_color_for_level(self, level: str) -> str:
        """Get ANSI color code for log level"""
        color_map = {
            LogLevel.VERBOSE: ColorCodes.PURPLE,
            LogLevel.DEBUG: ColorCodes.GRAY,
            LogLevel.INFO: ColorCodes.BLUE,
            LogLevel.WARNING: ColorCodes.YELLOW,
            LogLevel.ERROR: ColorCodes.RED
        }
        return color_map.get(level, ColorCodes.RESET)

    def _process_log(self, level: LogLevel, message: str):
        should_log_console = self._should_log_console(level)
        should_log_file = self._should_log_file(level)
        timestamp_implemenration = self._get_timestamp()
        if should_log_console:
            self._log_console_implementation(
                level, message, timestamp_implemenration)
        if should_log_file:
            self._write_to_file_implementation(
                level, message, timestamp_implemenration)

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

        return f"{timestamp} {color}{level:8}{reset} | {message}"
