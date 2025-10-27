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

import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict

from python.framework.types.log_level import LogLevel


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
        log_type: str,
        run_dir: Path,
        scenario_name: Optional[str] = None,  # scenario_index ENTFERNT!
        log_level: str = LogLevel.DEBUG
    ):
        """
        Initialize file logger.

        Args:
            log_type: "global" or "scenario"
            run_dir: Directory for log files
            scenario_name: Scenario name (for scenario logs)
            log_level: Minimum log level to write
        """
        self.log_type = log_type
        self.run_dir = run_dir
        self.scenario_name = scenario_name
        self.log_level = log_level.upper()

        # Create log file
        if log_type == "global":
            log_filename = "global.log"
        elif log_type == "scenario":
            if not scenario_name:
                raise ValueError("scenario_name required for scenario logs")
            # Format: scenario_GBPUSD_window_01.log
            log_filename = f"scenario_{scenario_name}.log"
        else:
            raise ValueError(f"Unknown log_type: {log_type}")

        self.log_file_path = run_dir / log_filename

        # Open file handle
        try:
            self.file_handle = open(self.log_file_path, 'w', encoding='utf-8')
            self._write_header()
        except Exception as e:
            print(
                f"Warning: Failed to create log file {self.log_file_path}: {e}")
            self.file_handle = None

    def _write_header(self):
        """Write log file header"""
        if not self.file_handle:
            return

        header = "=" * 80 + "\n"

        if self.log_type == "global":
            header += "                    FiniexTestingIDE - Global Log\n"
        else:
            header += "                    FiniexTestingIDE - Scenario Log\n"
            # scenario_index removed
            header += f"Scenario Name: {self.scenario_name}\n"

        header += f"Log Level: {self.log_level}\n"
        header += "=" * 80 + "\n\n"

        self.file_handle.write(header)
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
