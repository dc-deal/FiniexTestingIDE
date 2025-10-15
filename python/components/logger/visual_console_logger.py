"""
VisualConsoleLogger - Colorful, compact logging output
Preparation for future TUI (Terminal User Interface)

PHASE 1a (Live Progress):
- Buffered logging mode for clean live progress display
- Custom error types (validation_error, config_error, hard_error)
- Automatic flush on critical errors
- Thread-safe buffering

EXTENDED (C#003): Result rendering delegated to reporting/ package
"""

import logging
import sys
import time
import traceback
import threading
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from python.configuration import AppConfigLoader


class ColorCodes:
    """ANSI Color Codes"""
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    GRAY = '\033[90m'
    BOLD = '\033[1m'
    RESET = '\033[0m'


class VisualConsoleLogger:
    """
    Custom Logger with:
    - Colored log levels (ERROR=Red, WARNING=Yellow, INFO=Blue, DEBUG=Gray)
    - Compact class names (instead of fully qualified)
    - Relative time display (ms since start)
    - Buffered mode for clean live progress display
    - Custom error types with smart handling

    BUFFERING:
    - Call enable_buffering() to buffer all logs (for live progress)
    - Call flush_buffer() to output all buffered logs at once
    - Critical errors (validation, config, hard) auto-flush and exit

    ERROR TYPES:
    - validation_error(): Parameter/input validation errors (no stack trace)
    - config_error(): Configuration file errors (no stack trace)
    - hard_error(): Critical code errors (WITH stack trace)
    """

    def __init__(self, name: str = "FiniexTestingIDE", terminal_height: int = 60):
        self.name = name
        self.terminal_height = terminal_height
        self.start_time = datetime.now()

        # Buffering state
        self._buffered_mode = False
        # (level, message, logger_name)
        self._log_buffer: List[Tuple[str, str, Optional[str], int]] = []
        self._buffer_lock = threading.Lock()

        # Logging Setup
        self._setup_custom_logger()

        # App Config
        self.app_config = AppConfigLoader()

    def _setup_custom_logger(self):
        """Configure Python logging with custom formatter"""
        # Custom Formatter
        formatter = VisualLogFormatter(self.start_time)

        # Console Handler
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)

        # Root Logger configuration
        root_logger = logging.getLogger()
        root_logger.handlers.clear()  # Remove old handlers
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.INFO)

    # ============================================
    # Buffering Control
    # ============================================

    def enable_buffering(self):
        """
        Enable buffered mode.
        All log calls (info, warning, debug, error) will be buffered
        until flush_buffer() is called.

        Use this when showing live progress to avoid log spam.
        """
        with self._buffer_lock:
            self._buffered_mode = True
            self._log_buffer.clear()

    def disable_buffering(self):
        """
        Disable buffered mode.
        Log calls will go directly to console again.
        """
        with self._buffer_lock:
            self._buffered_mode = False

    def flush_buffer(self):
        """Flush all buffered logs to console."""
        with self._buffer_lock:
            if not self._log_buffer:
                return

            root_logger = logging.getLogger()
            if not root_logger.handlers:
                return

            handler = root_logger.handlers[0]

            # Output all buffered logs with ORIGINAL elapsed time
            for level, message, logger_name, elapsed_ms in self._log_buffer:
                record = logging.LogRecord(
                    name=logger_name or self.name,
                    level=getattr(logging, level),
                    pathname="(buffered)",
                    lineno=0,
                    msg=message,
                    args=(),
                    exc_info=None
                )

                # CRITICAL: Store elapsed_ms for formatter
                record.elapsed_ms_buffered = elapsed_ms  # Custom attribute!

                handler.emit(record)

            self._log_buffer.clear()

    def _log_or_buffer(self, level: str, message: str, logger_name: Optional[str] = None):
        """Internal: Either log directly or buffer based on mode."""
        with self._buffer_lock:
            if self._buffered_mode:
                # Calculate elapsed_ms NOW (relative to logger start)
                elapsed_ms = int(
                    (datetime.now() - self.start_time).total_seconds() * 1000)
                self._log_buffer.append(
                    (level, message, logger_name, elapsed_ms))
            else:
                # Direct output (unchanged)
                logger = logging.getLogger(logger_name or self.name)

                if level == 'INFO':
                    logger.info(message)
                elif level == 'WARNING':
                    logger.warning(message)
                elif level == 'ERROR':
                    logger.error(message)
                elif level == 'DEBUG':
                    logger.debug(message)

    # ============================================
    # Standard Logging Methods (Buffer-Aware)
    # ============================================

    def info(self, message: str, logger_name: Optional[str] = None):
        """Log INFO message (respects buffering)"""
        self._log_or_buffer('INFO', message, logger_name)

    def warning(self, message: str, logger_name: Optional[str] = None):
        """Log WARNING message (respects buffering)"""
        self._log_or_buffer('WARNING', message, logger_name)

    def error(self, message: str, logger_name: Optional[str] = None):
        """Log ERROR message (respects buffering)"""
        self._log_or_buffer('ERROR', message, logger_name)

    def debug(self, message: str, logger_name: Optional[str] = None):
        """Log DEBUG message (respects buffering)"""
        if not self.app_config.get_debug_logging():
            return
        self._log_or_buffer('DEBUG', message, logger_name)

    # ============================================
    # Critical Error Types (Always Flush + Exit)
    # ============================================

    def validation_error(self, message: str, context: Optional[Dict[str, Any]] = None):
        """
        Parameter/Input validation error.

        Use this for: Invalid user input, wrong parameter values, constraint violations.
        NO stack trace (error message should be self-explanatory).

        Args:
            message: Human-readable error description
            context: Optional dict with error context (e.g., {'parameter': 'rsi_period', 'value': -5})

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

        Use this for: Missing config files, invalid JSON, wrong config structure.
        NO stack trace (error message should be self-explanatory).

        Args:
            message: Human-readable error description
            file_path: Optional path to the problematic config file

        Behavior:
            - Flushes all buffered logs
            - Prints formatted error message
            - Exits with code 1
        """
        self.flush_buffer()

        print(f"\n{ColorCodes.RED}{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}")
        print(
            f"{ColorCodes.RED}{ColorCodes.BOLD}❌ CONFIGURATION ERROR{ColorCodes.RESET}")
        print(f"{ColorCodes.RED}{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}")
        print(f"\n{ColorCodes.RED}{message}{ColorCodes.RESET}")

        if file_path:
            print(f"\n{ColorCodes.YELLOW}File: {file_path}{ColorCodes.RESET}")

        print(f"\n{ColorCodes.RED}{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}\n")
        sys.exit(1)

    def hard_error(self, message: str, exception: Optional[Exception] = None):
        """
        Critical code error (internal failure).

        Use this for: Unexpected exceptions, system failures, critical bugs.
        WITH full stack trace for debugging.

        Args:
            message: Human-readable error description
            exception: Optional exception object (will extract stack trace)

        Behavior:
            - Flushes all buffered logs
            - Prints formatted error with full stack trace
            - Exits with code 1
        """
        self.flush_buffer()

        print(f"\n{ColorCodes.RED}{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}")
        print(f"{ColorCodes.RED}{ColorCodes.BOLD}❌ CRITICAL ERROR{ColorCodes.RESET}")
        print(f"{ColorCodes.RED}{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}")
        print(f"\n{ColorCodes.RED}{message}{ColorCodes.RESET}")

        # Print stack trace
        if exception:
            print(
                f"\n{ColorCodes.YELLOW}Exception: {type(exception).__name__}{ColorCodes.RESET}")
            print(f"{ColorCodes.YELLOW}Details: {str(exception)}{ColorCodes.RESET}")

        print(f"\n{ColorCodes.YELLOW}Stack Trace:{ColorCodes.RESET}")
        print(traceback.format_exc())

        print(f"\n{ColorCodes.RED}{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}\n")
        sys.exit(1)

    # ============================================
    # Legacy Methods (Kept for Compatibility)
    # ============================================

    def section_header(self, title: str, width: int = 60, char: str = "="):
        """Output section header (NOT buffered - for reports only)"""
        print(f"\n{ColorCodes.BOLD}{char * width}{ColorCodes.RESET}")
        print(f"{ColorCodes.BOLD}{title.center(width)}{ColorCodes.RESET}")
        print(f"{ColorCodes.BOLD}{char * width}{ColorCodes.RESET}")

    def section_separator(self, width: int = 60, char: str = "-"):
        """Output section separator (NOT buffered - for reports only)"""
        print(f"{char * width}")

    def print_results_table(self, batch_results=None):
        """
        DEPRECATED (C#003): Result rendering moved to reporting/ package.

        Use BatchSummary directly instead:
            from python.framework.reporting.batch_summary import BatchSummary
            summary = BatchSummary(performance_log, app_config)
            summary.render_all()

        This method is kept for backward compatibility but does nothing.
        """
        import warnings
        warnings.warn(
            "print_results_table() is deprecated. Use BatchSummary directly.",
            DeprecationWarning,
            stacklevel=2
        )


class VisualLogFormatter(logging.Formatter):
    """
    Custom Formatter:
    - Colored log levels
    - Compact class names (with C/ prefix if class detected)
    - Relative time (ms since start)
    """

    def __init__(self, start_time: datetime):
        super().__init__()
        self.start_time = start_time

        # Level -> Color mapping
        self.level_colors = {
            logging.ERROR: ColorCodes.RED,
            logging.WARNING: ColorCodes.YELLOW,
            logging.INFO: ColorCodes.BLUE,
            logging.DEBUG: ColorCodes.GRAY,
        }

    def format(self, record: logging.LogRecord) -> str:
        """Format log entry"""

        # Check if this is a buffered log with pre-calculated time
        if hasattr(record, 'elapsed_ms_buffered'):
            elapsed_ms = record.elapsed_ms_buffered  # Use stored value!
        else:
            # Calculate live (for non-buffered logs)
            now = datetime.now()
            elapsed_ms = int((now - self.start_time).total_seconds() * 1000)

        # Time format: from 1000ms → "Xs XXXms" for better readability
        if elapsed_ms >= 1000:
            seconds = elapsed_ms // 1000
            millis = elapsed_ms % 1000
            time_display = f"{seconds:>3}s {millis:03d}ms"
        else:
            time_display = f"   {elapsed_ms:>3}ms  "

        # Extract class name and optionally prefix with C/
        logger_name = record.name
        if '.' in logger_name:
            class_name = logger_name.split('.')[-1]
            # Detection: Capital letter at start = class
            if class_name and class_name[0].isupper():
                display_name = f"C/{class_name}"
            else:
                display_name = class_name
        else:
            display_name = logger_name

        # Color for level
        level_color = self.level_colors.get(record.levelno, ColorCodes.RESET)
        level_name = record.levelname

        # Formatting
        formatted = (
            f"{ColorCodes.GRAY}{time_display}{ColorCodes.RESET} - "
            f"{ColorCodes.GRAY}{display_name:<25}{ColorCodes.RESET} - "
            f"{level_color}{level_name:<7}{ColorCodes.RESET} - "
            f"{record.getMessage()}"
        )

        return formatted
