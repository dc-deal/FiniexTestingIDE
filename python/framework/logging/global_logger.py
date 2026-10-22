"""
FiniexTestingIDE - Global Logger
Logger for application-level logs (startup, config, framework)

Characteristics:
- DateTime timestamps (not elapsed time)
- No buffering (direct console output)
- Direct file output
- Singleton pattern

Usage:
    from python.framework.logging.bootstrap_logger import get_global_logger
    logger = get_global_logger()
    logger.info("Application started")
"""

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from python.framework.constants.run_layout_constants import GLOBAL_LOG_FILE
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.logging.file_logger import FileLogger
from python.framework.types.log_level import LogLevel
from python.framework.types.log_record_types import LogRecord


class GlobalLogger(AbstractLogger):
    """
    Global logger for application-level logs.

    Features:
    - DateTime timestamps (e.g., "2025-10-22 14:30:45")
    - Direct console output (no buffering)
    - Direct file output via FileLogger
    - Singleton pattern
    """

    def __init__(self, name: str = 'FiniexTestingIDE'):
        """
        Initialize global logger.

        Args:
            name: Logger name (default: "FiniexTestingIDE")
        """
        super().__init__(name=name)

        # Create file logger if enabled
        if self.file_logging_enabled:
            # The DIRECTORY global.log lives in — created here, not its parent. Taking
            # `.parent` of 'logs/' yields '.', so the real directory was never created and the
            # open only worked while it happened to exist already.
            log_dir = self._file_logging_config.global_log_dir
            log_dir.mkdir(parents=True, exist_ok=True)

            self.file_logger = FileLogger(
                file_path=log_dir,
                log_filename=GLOBAL_LOG_FILE,
                log_level=self._file_logging_config.global_log_level,
                append_mode=self._file_logging_config.global_append_mode
            )
        else:
            self.file_logger = None

        # A second file, attached only while tee_to() runs (an import).
        self._tee_logger: Optional[FileLogger] = None
        self._console_muted = False

        # Print log destination
        self.print_log_info()

    @contextmanager
    def tee_to(self, file_path: Path) -> Iterator[Path]:
        """
        Write every line to a second file as well, for as long as the block runs.

        The import's own log comes from here: the importers, index managers and caches all
        log through this logger, so a second sink records all of them without a logger being
        handed to each. global.log keeps receiving every line as before. One block at a time:
        a second one inside the first is refused, because its end would close the outer file
        and give the console back while the outer block still owns it.

        Args:
            file_path: The second log file; its directory is created when missing

        Returns:
            The second log file's path
        """
        if self._tee_logger is not None:
            raise RuntimeError(
                f'tee_to does not nest: {file_path} opened while another tee is open')
        file_path.parent.mkdir(parents=True, exist_ok=True)
        self._tee_logger = FileLogger(
            file_path=file_path.parent,
            log_filename=file_path.name,
            log_level=self._file_logging_config.global_log_level,
        )
        try:
            yield file_path
        finally:
            self._console_muted = False
            tee_logger, self._tee_logger = self._tee_logger, None
            tee_logger.close()

    def set_console_muted(self, muted: bool) -> None:
        """
        Keep lines off the console while a live screen owns it. The files still get them.

        Args:
            muted: True to keep lines off the console

        Returns:
            None
        """
        self._console_muted = muted

    def print_log_info(self):
        """Print where logs are being written (or if disabled)"""
        if self.file_logging_enabled and self.file_logger:
            print(f'📝 Global Log: {self.file_logger.log_file_path}')
        elif self.file_logging_enabled:
            print('⚠️  Global Log: FAILED to create (check path config)')
        else:
            print('ℹ️  Global Log: Disabled')

    def _render_timestamp(self, record: LogRecord) -> str:
        """
        Render the record's observation time as an absolute DateTime.

        Args:
            record: The entry whose timestamp to render

        Returns:
            DateTime string (e.g., "2025-10-22 14:30:45")
        """
        return record.timestamp.strftime('%Y-%m-%d %H:%M:%S')

    def _should_log_console(self, level: LogLevel) -> bool:
        """
        check if console log is enabled for logger
        """
        if self._console_muted:
            return False
        return LogLevel.should_log(
            level, self._console_logging_config.global_log_level)

    def _should_log_file(self, level: LogLevel) -> bool:
        """
         check if file log is enabled for logger
        """
        return LogLevel.should_log(
            level, self._file_logging_config.global_log_level)

    def _log_console_implementation(self, record: LogRecord):
        """
        Print the record directly — the global log is not buffered.

        Args:
            record: The entry to print
        """
        # Render from the record's own instant — reading the clock a second time here would
        # let the printed line and the record disagree.
        print(AbstractLogger.render_record(record))

    def _write_to_file_implementation(self, record: LogRecord):
        """
        Write to global log file.

        The global log carries no event-time column — this logger never gets a clock.

        Args:
            record: The entry to write
        """
        if self.file_logger is not None:
            # Write to file (plain text format with DateTime)
            self.file_logger.write_log(record, self._render_timestamp(record))
        if self._tee_logger is not None:
            self._tee_logger.write_log(record, self._render_timestamp(record))
