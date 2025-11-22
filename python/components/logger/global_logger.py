"""
FiniexTestingIDE - Global Logger
Logger for application-level logs (startup, config, framework)

Characteristics:
- DateTime timestamps (not elapsed time)
- No buffering (direct console output)
- Direct file output
- Singleton pattern

Usage:
    from python.components.logger.bootstrap_logger import get_logger
    logger = get_logger()
    logger.info("Application started")
"""

from datetime import datetime, timezone

from python.components.logger.abstract_logger import AbstractLogger
from python.components.logger.file_logger import FileLogger
from python.framework.types.log_level import LogLevel


class GlobalLogger(AbstractLogger):
    """
    Global logger for application-level logs.

    Features:
    - DateTime timestamps (e.g., "2025-10-22 14:30:45")
    - Direct console output (no buffering)
    - Direct file output via FileLogger
    - Singleton pattern
    """

    def __init__(self, name: str = "FiniexTestingIDE"):
        """
        Initialize global logger.

        Args:
            name: Logger name (default: "FiniexTestingIDE")
        """
        super().__init__(name=name)

        # report mode for forcing console print regardless of log level
        self.report_mode = False

        # Create file logger if enabled
        if self.file_logging_enabled:
            log_file_path = self._file_logging_config.global_log_path
            log_dir = log_file_path.parent
            log_dir.mkdir(parents=True, exist_ok=True)

            self.file_logger = FileLogger(
                file_path=log_file_path,
                log_filename="global.log",
                log_level=self._file_logging_config.global_log_level,
                append_mode=self._file_logging_config.global_append_mode
            )
        else:
            self.file_logger = None

        # Print log destination
        self.print_log_info()

    def print_log_info(self):
        """Print where logs are being written (or if disabled)"""
        if self.file_logging_enabled and self.file_logger:
            print(f"📝 Global Log: {self.file_logger.log_file_path}")
        elif self.file_logging_enabled:
            print(f"⚠️  Global Log: FAILED to create (check path config)")
        else:
            print(f"ℹ️  Global Log: Disabled")

    def _get_timestamp(self) -> str:
        """
        Get DateTime timestamp.

        Returns:
            DateTime string (e.g., "2025-10-22 14:30:45")
        """
        return datetime.now(timezone.utc) .strftime("%Y-%m-%d %H:%M:%S")

    def _should_log_console(self, level: LogLevel) -> str:
        """
        check if console log is enabled for logger
        """
        return LogLevel.should_log(
            level, self._console_logging_config.global_log_level)

    def _should_log_file(self, level: LogLevel) -> str:
        """
         check if file log is enabled for logger
        """
        return LogLevel.should_log(
            level, self._file_logging_config.global_log_level)

    def _log_console_implementation(self, level: str, message: str, timestamp: str):
        """
          Format Message for Global Log
        Args:
            level: Log level (INFO, DEBUG, WARNING, ERROR)
            message: Log message
        """
        formatted_line = self._format_log_line(level, message, timestamp)
        # Console output (direct print)
        print(formatted_line)

    def _write_to_file_implementation(self, level: str, message: str, timestamp: str):
        """
        Write to global log file.

        Args:
            level: Log level
            message: Log message (plain text, no colors)
            timestamp: DateTime timestamp
        """
        if self.file_logger is not None:
            # Write to file (plain text format with DateTime)
            self.file_logger.write_log(level, message, timestamp)
