"""
VisualConsoleLogger - Colorful, compact logging output
Preparation for future TUI (Terminal User Interface)

PHASE 2 (V0.7): Enhanced Performance Visualization
- New performance structure with per-worker details
- Decision logic performance tracking
- Parameter override warnings
- Batch vs. Scenario parallelism clarity

HOTFIX: Use AppConfigLoader for correct batch mode detection

EXTENDED (C#003): Result rendering delegated to reporting/ package
"""

import logging
import sys
from datetime import datetime
from typing import Optional, Dict, Any
from python.configuration import AppConfigLoader

# NEW (C#003): Import BatchSummary for result rendering
from python.framework.reporting.batch_summary import BatchSummary
from python.framework.reporting.batch_summary import BatchSummary


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
    - Log section grouping
    - Terminal-optimized (~60 lines)

    REFACTORED: Now uses BatchSummary for result rendering
    """

    def __init__(self, name: str = "FiniexTestingIDE", terminal_height: int = 60):
        self.name = name
        self.terminal_height = terminal_height
        self.start_time = datetime.now()
        self.log_buffer = []

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

    def info(self, message: str, logger_name: Optional[str] = None):
        """Log INFO message"""
        logger = logging.getLogger(logger_name or self.name)
        logger.info(message)

    def warning(self, message: str, logger_name: Optional[str] = None):
        """Log WARNING message"""
        logger = logging.getLogger(logger_name or self.name)
        logger.warning(message)

    def error(self, message: str, logger_name: Optional[str] = None):
        """Log ERROR message"""
        logger = logging.getLogger(logger_name or self.name)
        logger.error(message)

    def debug(self, message: str, logger_name: Optional[str] = None):
        """Log DEBUG message"""
        if not self.app_config.get_debug_logging:
            return
        logger = logging.getLogger(logger_name or self.name)
        logger.debug(message)

    def section_header(self, title: str, width: int = 60, char: str = "="):
        """Output section header"""
        print(f"\n{ColorCodes.BOLD}{char * width}{ColorCodes.RESET}")
        print(f"{ColorCodes.BOLD}{title.center(width)}{ColorCodes.RESET}")
        print(f"{ColorCodes.BOLD}{char * width}{ColorCodes.RESET}")

    def section_separator(self, width: int = 60, char: str = "-"):
        """Output section separator"""
        print(f"{char * width}")

    def print_results_table(self, batch_results=None):
        """
        DEPRECATED (C#003): Result rendering moved to reporting/ package.

        Use BatchSummary directly instead:
            from python.framework.reporting.batch_summary import BatchSummary
            summary = BatchSummary(performance_log, trade_simulator, app_config)
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
        # Calculate relative time (ms since start)
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
