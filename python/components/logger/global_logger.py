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

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from python.components.logger.abstract_logger import AbstractLogger, ColorCodes
from python.components.logger.file_logger import FileLogger
from python.framework.types.log_level import LogLevel
from python.configuration import AppConfigLoader


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

        # Setup Python's logging system for console output
        self._setup_console_logging()

    def _setup_console_logging(self):
        """Setup Python's logging system for direct console output"""
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)

        # Remove existing handlers
        root_logger.handlers.clear()

        # Create console handler
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter('%(message)s'))
        root_logger.addHandler(handler)

    def _get_timestamp(self) -> str:
        """
        Get DateTime timestamp.

        Returns:
            DateTime string (e.g., "2025-10-22 14:30:45")
        """
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _log(self, level: str, message: str):
        """
        Log message directly to console and file.

        No buffering - writes immediately.

        Args:
            level: Log level (INFO, DEBUG, WARNING, ERROR)
            message: Log message
        """
        timestamp = self._get_timestamp()
        formatted_line = self._format_log_line(level, message, timestamp)

        # Console output (direct)
        self._write_to_console(level, formatted_line)

        # File output (if enabled)
        if self.file_logging_enabled and LogLevel.should_log(level, self.file_log_level):
            self._write_to_file(level, message, timestamp)

    def _write_to_console(self, level: str, formatted_line: str):
        """
        Write directly to console.

        Args:
            level: Log level
            formatted_line: Pre-formatted log line with colors
        """
        logger = logging.getLogger(self.name)

        if level == LogLevel.INFO:
            logger.info(formatted_line)
        elif level == LogLevel.WARNING:
            logger.warning(formatted_line)
        elif level == LogLevel.ERROR:
            logger.error(formatted_line)
        elif level == LogLevel.DEBUG:
            logger.debug(formatted_line)

    def _write_to_file(self, level: str, message: str, timestamp: str):
        """
        Write to global log file.

        Args:
            level: Log level
            message: Log message (plain text, no colors)
            timestamp: DateTime timestamp
        """
        # Lazy-create file logger
        if self.file_logger is None:
            from python.components.logger.file_logger import FileLogger

            # Create global log directory
            log_dir = Path(self.file_log_root)
            log_dir.mkdir(parents=True, exist_ok=True)

            self.file_logger = FileLogger(
                log_type="global",
                run_dir=log_dir,
                log_level=self.file_log_level
            )

        # Write to file (plain text format with DateTime)
        self.file_logger.write_log(level, message, timestamp)

    def flush_buffer(self):
        """
        No-op for GlobalLogger (no buffering).

        Included for interface compatibility with AbstractLogger.
        """
        pass  # GlobalLogger doesn't buffer

    def close(self):
        """Close file logger if open"""
        if self.file_logger:
            self.file_logger.close()
