"""
VisualConsoleLogger - Colorful, compact logging output

PHASE 1a (Live Progress):
- Buffered logging mode for clean live progress display
- Scenario-grouped log output with relative timestamps
- Custom error types (validation_error, config_error, hard_error)
- Automatic flush on critical errors
- Thread-safe buffering

PHASE 1b (File Logging):
- Integrated file logging with log level filtering
- Automatic config snapshot
- Performance-optimized batch writes
"""

import logging
import sys
import traceback
import threading
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple

from python.configuration import AppConfigLoader
from python.framework.types.log_level import LogLevel
# Create global file logger
from python.components.logger.file_logger import FileLogger
from pathlib import Path


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
    - Scenario-grouped log output
    - Custom error types with smart handling
    - Integrated file logging with log level filtering

    BUFFERING & SCENARIOS:
    - Call enable_buffering() to buffer all logs (for live progress)
    - Call start_scenario_logging(index, name) to tag logs per scenario
    - Call flush_buffer() to output all logs grouped by scenario
    - Critical errors (validation, config, hard) auto-flush and exit

    FILE LOGGING:
    - Call attach_scenario_set(name) to enable file logging
    - Logs are written to file on flush_buffer()
    - Automatic config snapshot
    - Separate log level filtering for file

    ERROR TYPES:
    - validation_error(): Parameter/input validation errors (no stack trace)
    - config_error(): Configuration file errors (no stack trace)
    - hard_error(): Critical code errors (WITH stack trace)
    """

    def __init__(self, name: str):
        self.name = name
        self.start_time = datetime.now()

        # Buffering state
        self._buffered_mode = False
        self._log_buffer: List[Tuple[str, str,
                                     Optional[str], int, Optional[int]]] = []
        # Structure: (level, message, logger_name, elapsed_ms, scenario_index)
        self._buffer_lock = threading.Lock()

        # Scenario tracking for logging
        self._current_scenario_index: Optional[int] = None
        self._thread_local = threading.local()
        self._scenario_start_times: Dict[int, datetime] = {}
        self._scenario_names: Dict[int, str] = {}

        # Logging Setup
        self._setup_custom_logger()

        # App Config (cached singleton)
        self.app_config = AppConfigLoader()

        # Console log level (validated)
        raw_console_level = self.app_config.get_console_log_level()
        self.console_log_level = LogLevel.validate(raw_console_level)

        # File logging (lazy initialization per run)
        self.global_file_logger = None
        # scenario_index -> FileLogger
        self._scenario_file_loggers: Dict[int, Any] = {}
        self.run_timestamp = None
        self.run_dir = None
        self.file_logging_enabled = self.app_config.is_file_logging_enabled()
        if self.file_logging_enabled:
            self.file_log_level = self.app_config.get_file_log_level()
            self.file_log_root = self.app_config.get_file_log_root_path()

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
        root_logger.setLevel(logging.DEBUG)

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
            self._current_scenario_index = None
            self._scenario_start_times.clear()
            self._scenario_names.clear()

    def disable_buffering(self):
        """
        Disable buffered mode.
        Log calls will go directly to console again.
        """
        with self._buffer_lock:
            self._buffered_mode = False

    def start_scenario_logging(self, scenario_index: int, scenario_name: str):
        """Start logging for a new scenario."""
        # Set THREAD-LOCAL scenario index
        self._thread_local.scenario_index = scenario_index

        with self._buffer_lock:
            self._scenario_start_times[scenario_index] = datetime.now()
            self._scenario_names[scenario_index] = scenario_name

            # NEW: Create scenario-specific FileLogger
            if self.file_logging_enabled and self.run_dir is not None:
                from python.components.logger.file_logger import FileLogger
                self._scenario_file_loggers[scenario_index] = FileLogger(
                    log_type="scenario",
                    run_dir=self.run_dir,
                    scenario_index=scenario_index,
                    scenario_name=scenario_name,
                    log_level=self.file_log_level
                )

    # ============================================
    # File Logging Integration
    # ============================================

    def attach_scenario_set(self, scenario_set_name: str):
        """
        Attach file logger for this scenario set.
        Creates run directory and global file logger.
        Called from strategy_runner after loading config.

        Args:
            scenario_set_name: Scenario config filename (e.g., "eurusd_3_windows.json")
        """
        if not self.file_logging_enabled:
            return

        # Generate run timestamp (used for all files in this run)
        self.run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Create run directory path
        scenario_base_name = scenario_set_name.replace('.json', '')
        self.run_dir = Path(self.file_log_root) / \
            scenario_base_name / self.run_timestamp

        # Create run directory
        self.run_dir.mkdir(parents=True, exist_ok=True)

        source_config_path = Path("configs/scenario_sets") / scenario_set_name

        self.global_file_logger = FileLogger(
            log_type="global",
            run_dir=self.run_dir,
            log_level=self.file_log_level,
            source_config_path=source_config_path
        )

    def flush_buffer(self):
        """
        Flush all buffered logs to console, grouped by scenario.
        Each scenario gets its own header and relative timestamps (starting at 0ms).

        NEW: Also writes to file logger if attached.
        """
        with self._buffer_lock:
            if not self._log_buffer:
                return

            # Group logs by scenario_index
            logs_by_scenario: Dict[Optional[int], List] = {}
            for level, message, logger_name, elapsed_ms, scenario_index in self._log_buffer:
                if scenario_index not in logs_by_scenario:
                    logs_by_scenario[scenario_index] = []
                logs_by_scenario[scenario_index].append(
                    (level, message, logger_name, elapsed_ms))

            # ============================================
            # CONSOLE OUTPUT
            # ============================================
            root_logger = logging.getLogger()
            if root_logger.handlers:
                handler = root_logger.handlers[0]

                for scenario_index in sorted(logs_by_scenario.keys(), key=lambda x: (x is None, x)):
                    # Print scenario header
                    if scenario_index is not None:
                        scenario_name = self._scenario_names.get(
                            scenario_index, f"Scenario {scenario_index}")
                        self._print_scenario_header(
                            scenario_index, scenario_name)
                    else:
                        # Logs without scenario (global logs)
                        print(f"\n{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}")
                        print(
                            f"{ColorCodes.BOLD}{'GLOBAL LOGS'.center(60)}{ColorCodes.RESET}")
                        print(f"{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}")

                    # Get scenario start time for relative timestamps
                    scenario_start = self._scenario_start_times.get(
                        scenario_index, self.start_time)

                    # Output logs for this scenario with relative time
                    for level, message, logger_name, original_elapsed_ms in logs_by_scenario[scenario_index]:
                        # Calculate relative time from scenario start
                        if scenario_index is not None:
                            # Reconstruct original log time
                            original_time = self.start_time + \
                                timedelta(milliseconds=original_elapsed_ms)
                            # Calculate relative to scenario start
                            relative_ms = int(
                                (original_time - scenario_start).total_seconds() * 1000)
                        else:
                            relative_ms = original_elapsed_ms

                        # Create LogRecord
                        record = logging.LogRecord(
                            name=logger_name or self.name,
                            level=getattr(logging, level),
                            pathname="(buffered)",
                            lineno=0,
                            msg=message,
                            args=(),
                            exc_info=None
                        )

                        # Store relative time for formatter
                        record.elapsed_ms_buffered = relative_ms

                        # Emit through handler
                        handler.emit(record)

            # Clear buffer
            self._log_buffer.clear()
            self._scenario_start_times.clear()
            self._scenario_names.clear()

    def _print_scenario_header(self, scenario_index: int, scenario_name: str):
        """Print scenario header for log block."""
        print(f"\n{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}")
        header_text = f"📊 SCENARIO {scenario_index + 1}: {scenario_name}"
        print(f"{ColorCodes.BOLD}{header_text.center(60)}{ColorCodes.RESET}")
        print(f"{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}")

    def _log_or_buffer(self, level: str, message: str):
        """
        Internal: Either log directly or buffer based on mode.

        Args:
            level: Log level (INFO, WARNING, ERROR, DEBUG)
            message: Log message
            logger_name: Optional logger name
        """
        with self._buffer_lock:
            # Calculate elapsed_ms NOW (relative to logger start)
            elapsed_ms = int(
                (datetime.now() - self.start_time).total_seconds() * 1000)

            # Tag with current scenario index (if set)
            scenario_index = getattr(
                self._thread_local, 'scenario_index', None)

            # CRITICAL: Write to file LIVE (global or scenario-specific)
            if LogLevel.should_log(level, self.file_log_level):
                if scenario_index is None:
                    # Global log → write to global file logger
                    if self.global_file_logger:
                        self.global_file_logger.write_live_log(
                            level, message, elapsed_ms)
                else:
                    # Scenario log → write to scenario-specific file logger
                    if scenario_index in self._scenario_file_loggers:
                        self._scenario_file_loggers[scenario_index].write_live_log(
                            level, message, elapsed_ms)

            if LogLevel.should_log(level, self.console_log_level):
                # Now handle console output (buffered or direct)
                if self._buffered_mode:
                    # Buffer the log with scenario tag
                    self._log_buffer.append(
                        (level, message, self.name, elapsed_ms, scenario_index))
                else:
                    # Direct output
                    logger = logging.getLogger(self.name)

                    if level == LogLevel.INFO:
                        logger.info(message)
                    elif level == LogLevel.WARNING:
                        logger.warning(message)
                    elif level == LogLevel.ERROR:
                        logger.error(message)
                    elif level == LogLevel.DEBUG:
                        logger.debug(message)

    def get_scenario_elapsed_time(self, scenario_index: int) -> Optional[float]:
        """
        Get elapsed time for a scenario in seconds.

        Args:
            scenario_index: Scenario array index

        Returns:
            Elapsed time in seconds, or None if scenario not started
        """
        with self._buffer_lock:
            start_time = self._scenario_start_times.get(scenario_index)
            if start_time is None:
                return None

            return (datetime.now() - start_time).total_seconds()

    # ============================================
    # Standard Logging Methods (Buffer-Aware + Log Level Filtering)
    # ============================================

    def info(self, message: str):
        """Log INFO message (respects buffering and log level)"""
        self._log_or_buffer(LogLevel.INFO, message)

    def warning(self, message: str):
        """Log WARNING message (respects buffering and log level)"""
        self._log_or_buffer(LogLevel.WARNING, message)

    def error(self, message: str):
        """Log ERROR message (respects buffering and log level)"""
        self._log_or_buffer(LogLevel.ERROR, message)

    def debug(self, message: str):
        """Log DEBUG message (respects buffering and log level)"""
        self._log_or_buffer(LogLevel.DEBUG, message)

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
            - Writes error to file log
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

        # Write to file log
        if self.file_logger:
            self.file_logger.write_error_footer(
                "VALIDATION ERROR", message, context)
            self.file_logger.close()

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
            - Writes error to file log
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

        # Write to file log
        if self.file_logger:
            context = {"file_path": file_path} if file_path else None
            self.file_logger.write_error_footer(
                "CONFIGURATION ERROR", message, context)
            self.file_logger.close()

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
            - Writes error to file log
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
        stack_trace = traceback.format_exc()
        print(stack_trace)

        print(f"\n{ColorCodes.RED}{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}\n")

        # Write to file log
        if self.file_logger:
            error_msg = f"{message}\n\nException: {type(exception).__name__}\nDetails: {str(exception)}\n\nStack Trace:\n{stack_trace}" if exception else message
            self.file_logger.write_error_footer("CRITICAL ERROR", error_msg)
            self.file_logger.close()

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


class VisualLogFormatter(logging.Formatter):
    """
    Custom Formatter:
    - Colored log levels
    - Compact class names (with C/ prefix if class detected)
    - Relative time (ms since start or scenario start)
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

        # Color for level
        level_color = self.level_colors.get(record.levelno, ColorCodes.RESET)
        level_name = record.levelname

        # Formatting
        formatted = (
            f"{ColorCodes.GRAY}{time_display}{ColorCodes.RESET} - "
            f"{level_color}{level_name:<7}{ColorCodes.RESET} - "
            f"{record.getMessage()}"
        )

        return formatted
