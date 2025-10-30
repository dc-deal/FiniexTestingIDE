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

from datetime import datetime
from pathlib import Path
import sys
import traceback
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

from python.components.logger.file_logger import FileLogger
from python.configuration.app_config_loader import AppConfigLoader
from python.framework.types.log_level import LogLevel


class ColorCodes:
    """ANSI Color Codes for console output"""
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    GRAY = '\033[90m'
    BOLD = '\033[1m'
    RESET = '\033[0m'


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
            console_log_level: Minimum log level for console output
        """
        self.name = name

        # Load config for log levels
        config = AppConfigLoader.get_config()
        console_log_level = config.get('logging', {}).get(
            'log_level', LogLevel.INFO)

        self.console_log_level = console_log_level.upper()

        # Timing
        self.start_time = datetime.now()

        # Console buffering
        # [(level, formatted_line)]
        self.console_buffer: List[Tuple[str, str]] = []

        # File logging config
        self.file_logging_enabled = config.get(
            'file_logging', {}).get('enabled', False)
        self.file_log_level = config.get('file_logging', {}).get(
            'log_level', LogLevel.DEBUG)
        self.file_log_root = config.get(
            'file_logging', {}).get('log_root_path', 'logs')

        # Validate log level from config str
        if not LogLevel.validate(self.console_log_level):
            raise ValueError(f"Invalid log level: {self.console_log_level}")

    @abstractmethod
    def _log(self, level: str, message: str):
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
    def flush_buffer(self):
        """
        Flush any buffered logs.

        Different implementations:
        - GlobalLogger: No-op (no buffering)
        - ScenarioLogger: Flush console buffer
        """
        pass

    # ============================================
    # Public Logging API
    # ============================================

    def info(self, message: str):
        """Log INFO message (respects log level filtering)"""
        if LogLevel.should_log(LogLevel.INFO, self.console_log_level):
            self._log(LogLevel.INFO, message)

    def debug(self, message: str):
        """Log DEBUG message (respects log level filtering)"""
        if LogLevel.should_log(LogLevel.DEBUG, self.console_log_level):
            self._log(LogLevel.DEBUG, message)

    def warning(self, message: str):
        """Log WARNING message (respects log level filtering)"""
        if LogLevel.should_log(LogLevel.WARNING, self.console_log_level):
            self._log(LogLevel.WARNING, message)

    def error(self, message: str):
        """Log ERROR message (respects log level filtering)"""
        if LogLevel.should_log(LogLevel.ERROR, self.console_log_level):
            self._log(LogLevel.ERROR, message)

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
        self.flush_buffer()

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
        self.flush_buffer()

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
        self.flush_buffer()

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
            LogLevel.DEBUG: ColorCodes.GRAY,
            LogLevel.INFO: ColorCodes.BLUE,
            LogLevel.WARNING: ColorCodes.YELLOW,
            LogLevel.ERROR: ColorCodes.RED
        }
        return color_map.get(level, ColorCodes.RESET)

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
