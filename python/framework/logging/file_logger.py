"""
FiniexTestingIDE - File Logger (Per-Run Architecture)
Writes logs to file with run-specific directories

Architecture:
- One run directory per execution (timestamp-based)
- One global.log for all global logs + summary
- One {file_name_prefix}_{index}_{name}.log per scenario
- One config.json snapshot per run

Features:
- Lazy file opening (performance)
- Live writing (safety - survives crashes)
- Log level filtering
- Thread-safe
- Plain text format (no ANSI colors)
"""
from datetime import datetime, timezone
from pathlib import Path

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.types.log_level import LogLevel
from python.framework.types.log_record_types import LogRecord
from python.framework.utils.file_utils import sanitize_filename


class FileLogger:
    """
    File logger for a single log file (global or scenario-specific).

    File structure:
        logs/scenario_sets/eurusd_3_windows/20251021_105359/
            global.log                           (all global logs + summary)
            config.json                          (config snapshot)
            scenario_0_GBPUSD_window_02.log     (scenario 0 logs)
            scenario_1_GBPUSD_window_03.log     (scenario 1 logs)
    """

    def __init__(
        self,
        file_path: Path,
        log_level: LogLevel,
        log_filename: str,
        append_mode: bool = False,
    ):
        """
        Initialize file logger.

        The log level is HEADER TEXT only — this class applies no threshold of its own. The
        gate is the logger's _should_log_file, which reads file_logging.*.log_level; a second
        decision point here would only invite the two to disagree.

        Args:
            file_path: Directory the log file is written to
            log_level: The effective threshold, for the file header
            log_filename: File name (sanitized on the way)
            append_mode: Append to an existing file instead of overwriting
        """
        self.file_path = file_path

        self._sanitized_filename = sanitize_filename(log_filename)

        self.log_file_path = file_path / self._sanitized_filename

        # Open file handle with appropriate mode
        file_mode = 'a' if append_mode else 'w'
        try:
            self.file_handle = open(
                self.log_file_path, file_mode, encoding='utf-8')

            # Write header only if creating new file (not appending)
            if not append_mode:
                self._write_header(log_level)
            else:
                # Add separator when appending
                self._write_append_separator(log_level)

        except Exception as e:
            print(
                f'Warning: Failed to create log file {self.log_file_path}: {e}')
            self.file_handle = None

    def _write_header(self, log_level: LogLevel):
        """
        Write log file header.

        Args:
            log_level: The effective threshold to state in the header
        """
        if not self.file_handle:
            return

        header = '=' * 80 + '\n'
        header += f'Log Name: {self._sanitized_filename}\n'
        header += f'Log Level: {log_level}\n'
        header += '=' * 80 + '\n\n'

        self.file_handle.write(header)
        self.file_handle.flush()

    def _write_append_separator(self, log_level: LogLevel):
        """
        Write separator when appending to existing log.

        Args:
            log_level: The effective threshold to state in the separator
        """
        if not self.file_handle:
            return
        timestamp = datetime.now(timezone.utc) .strftime('%Y-%m-%d %H:%M:%S')

        log_level_str = 'LOG LEVEL: ' + log_level
        separator = (
            f"\n{'='*80}\n"
            f"{'SESSION CONTINUED'.center(80)}\n"
            f"{log_level_str.center(80)}\n"
            f"{timestamp.center(80)}\n"
            f"{'='*80}\n\n"
        )

        self.file_handle.write(separator)
        self.file_handle.flush()

    def write_log(self, record: LogRecord, timestamp: str, event_column: bool = False):
        """
        Write log entry to file.

        Renders through AbstractLogger.format_line — the ONE line formula — with colours off.
        A second literal here is how the log file and the scenario console drifted apart before.

        The timestamp arrives pre-rendered because only the LOGGER knows which form it uses:
        elapsed against its run_timestamp (ScenarioLogger) or absolute (GlobalLogger). The sink
        has neither.

        Args:
            record: The entry to write
            timestamp: Pre-rendered observation timestamp
            event_column: Whether this log carries the event-time column
        """
        if not self.file_handle:
            return

        log_line = AbstractLogger.format_line(
            timestamp, record.level, record.message,
            event_time=record.event_time, event_column=event_column, colored=False) + '\n'

        try:
            self.file_handle.write(log_line)
            self.file_handle.flush()  # Immediate flush for reliability
        except Exception as e:
            # Fail silently - don't break execution on file write errors
            print(f'Warning: Failed to write to log file: {e}')

    def close(self):
        """
        Close file handle.

        CRITICAL: Must be called to prevent ProcessPool shutdown delays!
        Open file handles prevent process termination - Python waits ~11s for timeout.
        """
        if self.file_handle:
            try:
                self.file_handle.flush()
                self.file_handle.close()
                self.file_handle = None
            except Exception as e:
                print(f'Warning: Failed to close log file: {e}')
