"""
FiniexTestingIDE - File Logger (Per-Run Architecture)
Writes logs to file with run-specific directories

Architecture:
- One run directory per execution (timestamp-based)
- One global.log for all global logs + summary
- One scenario_{index}_{name}.log per scenario
- One config.json snapshot per run

Features:
- Lazy file opening (performance)
- Live writing (safety - survives crashes)
- Log level filtering
- Thread-safe
- Plain text format (no ANSI colors)
"""
from datetime import timezone
from pathlib import Path
from python.framework.types.log_level import LogLevel
from datetime import datetime

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
        log_filename: str = "",
        append_mode: bool = False,
    ):
        """
        Initialize file logger.

        Args:
            run_dir: Directory for log files
            scenario_name: Scenario name (for scenario logs)
            log_level: Minimum log level to write
        """
        self.file_path = file_path
        self.log_level = log_level
        self._append_mode = append_mode
        sanitized_filename = sanitize_filename(log_filename)

        self.log_file_path = file_path / sanitized_filename

        # Open file handle with appropriate mode
        file_mode = 'a' if append_mode else 'w'
        try:
            self.file_handle = open(
                self.log_file_path, file_mode, encoding='utf-8')

            # Write header only if creating new file (not appending)
            if not append_mode:
                self._write_header()
            else:
                # Add separator when appending
                self._write_append_separator()

        except Exception as e:
            print(
                f"Warning: Failed to create log file {self.log_file_path}: {e}")
            self.file_handle = None

    def _write_header(self):
        """Write log file header"""
        if not self.file_handle:
            return

        header = "=" * 80 + "\n"
        header += "Global Log\n"
        header += f"Log Level: {self.log_level}\n"
        header += "=" * 80 + "\n\n"

        self.file_handle.write(header)
        self.file_handle.flush()

    def _write_append_separator(self):
        """Write separator when appending to existing log"""
        if not self.file_handle:
            return
        timestamp = datetime.now(timezone.utc) .strftime("%Y-%m-%d %H:%M:%S")

        log_level_str = 'LOG LEVEL: ' + self.log_level
        separator = (
            f"\n{'='*80}\n"
            f"{'SESSION CONTINUED'.center(80)}\n"
            f"{log_level_str.center(80)}\n"
            f"{timestamp.center(80)}\n"
            f"{'='*80}\n\n"
        )

        self.file_handle.write(separator)
        self.file_handle.flush()

    def write_log(self, level: str, message: str, timestamp: str):
        """
        Write log entry to file.

        Used by both GlobalLogger and ScenarioLogger.
        - GlobalLogger: timestamp is DateTime string
        - ScenarioLogger: timestamp is elapsed time string

        Args:
            level: Log level (INFO, DEBUG, WARNING, ERROR)
            message: Plain text message (no colors)
            timestamp: Pre-formatted timestamp string
        """
        if not self.file_handle:
            return

        # Format: [timestamp] LEVEL | message
        log_line = f"{timestamp} {level:8} | {message}\n"

        try:
            self.file_handle.write(log_line)
            self.file_handle.flush()  # Immediate flush for reliability
        except Exception as e:
            # Fail silently - don't break execution on file write errors
            print(f"Warning: Failed to write to log file: {e}")

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
                print(f"Warning: Failed to close log file: {e}")
