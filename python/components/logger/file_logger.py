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
        log_type: str,  # "global" or "scenario"
        run_dir: Path,
        scenario_index: Optional[int] = None,
        scenario_name: Optional[str] = None,
        log_level: str = LogLevel.INFO,
        source_config_path: Optional[Path] = None
    ):
        """
        Initialize file logger (file NOT opened yet - lazy opening).

        Args:
            log_type: "global" or "scenario"
            run_dir: Run directory (e.g., logs/scenario_sets/eurusd_3_windows/20251021_105359)
            scenario_index: Scenario array index (only for type=scenario)
            scenario_name: Scenario name (only for type=scenario)
            log_level: Minimum log level to write (DEBUG, INFO, WARNING, ERROR)
            source_config_path: Path to source config file (only for type=global)
        """
        self.log_type = log_type
        self.run_dir = Path(run_dir)
        self.scenario_index = scenario_index
        self.scenario_name = scenario_name
        self.source_config_path = source_config_path

        # Validate log level
        self.log_level = LogLevel.validate(log_level)

        # Create file paths based on type
        if log_type == "global":
            self.log_file_path = self.run_dir / "global.log"
            self.config_snapshot_path = self.run_dir / "config.json"
        else:  # scenario
            if scenario_index is None or scenario_name is None:
                raise ValueError(
                    "scenario_index and scenario_name required for type=scenario")
            self.log_file_path = self.run_dir / \
                f"scenario_{scenario_index}_{scenario_name}.log"
            self.config_snapshot_path = None

        # File handle (lazy opened)
        self.file_handle = None
        self.file_lock = threading.Lock()

        # Track if config was copied (global only)
        self.config_copied = False

        # Track if header was written
        self.header_written = False

    def _ensure_log_file_ready(self):
        """
        Lazy initialization: Create directory, open file, write header.
        Called on first write.
        """
        with self.file_lock:
            if self.file_handle is not None:
                return  # Already opened

            # Create directory
            self.run_dir.mkdir(parents=True, exist_ok=True)

            # Open log file
            self.file_handle = open(self.log_file_path, 'w', encoding='utf-8')

            # Write header
            self._write_header()

            # Copy config snapshot (global only)
            if self.log_type == "global":
                self._copy_config_snapshot()

    def _write_header(self):
        """Write log file header."""
        if not self.file_handle or self.header_written:
            return

        if self.log_type == "global":
            header_lines = [
                "=" * 80,
                "FiniexTestingIDE - Strategy Run Log (GLOBAL)".center(80),
                "=" * 80,
                f"Run Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"Log Level: {self.log_level}",
                f"Run Directory: {self.run_dir}",
                "=" * 80,
                "",
                "=" * 80,
                "GLOBAL LOGS".center(80),
                "=" * 80
            ]
        else:  # scenario
            header_lines = [
                "=" * 80,
                f"FiniexTestingIDE - Scenario Log".center(80),
                "=" * 80,
                f"Scenario Index: {self.scenario_index}",
                f"Scenario Name: {self.scenario_name}",
                f"Log Level: {self.log_level}",
                "=" * 80,
                ""
            ]

        self.file_handle.write('\n'.join(header_lines) + '\n')
        self.file_handle.flush()
        self.header_written = True

    def _copy_config_snapshot(self):
        """Copy scenario config file as snapshot (global only)."""
        if self.config_copied or not self.source_config_path:
            return

        try:
            if self.source_config_path.exists():
                shutil.copy2(self.source_config_path,
                             self.config_snapshot_path)
                self.config_copied = True
            else:
                # Log warning in file
                if self.file_handle:
                    self.file_handle.write(
                        f"⚠️  WARNING: Config file not found for snapshot: {self.source_config_path}\n\n"
                    )
        except Exception as e:
            # Log error in file
            if self.file_handle:
                self.file_handle.write(
                    f"❌ ERROR: Failed to copy config snapshot: {e}\n\n"
                )

    def write_live_log(self, level: str, message: str, elapsed_ms: int):
        """
        Write single log immediately.
        Called for each log (global or scenario).

        Args:
            level: Log level (INFO, WARNING, ERROR, DEBUG)
            message: Log message
            elapsed_ms: Elapsed milliseconds since logger start
        """
        # Lazy open file
        if self.file_handle is None:
            self._ensure_log_file_ready()

        # Check log level filtering
        if not LogLevel.should_log(level, self.log_level):
            return

        # Format timestamp
        if elapsed_ms >= 1000:
            seconds = elapsed_ms // 1000
            millis = elapsed_ms % 1000
            time_str = f"{seconds:>3}s {millis:03d}ms"
        else:
            time_str = f"   {elapsed_ms:>3}ms  "

        # Format line
        line = f"[{time_str}] {level:<7} | {message}"

        # Write immediately
        with self.file_lock:
            if self.file_handle:
                self.file_handle.write(line + '\n')
                self.file_handle.flush()  # Explicit flush for safety

    def write_error_footer(
        self,
        error_type: str,
        message: str,
        context: Optional[Dict] = None
    ):
        """
        Write error footer to log file before exit.

        Args:
            error_type: Type of error (VALIDATION ERROR, CONFIG ERROR, etc.)
            message: Error message
            context: Optional error context
        """
        # Ensure file is open
        if self.file_handle is None:
            self._ensure_log_file_ready()

        lines = [
            "",
            "=" * 80,
            f"❌ {error_type}".center(80),
            "=" * 80,
            f"{message}",
        ]

        if context:
            lines.append("")
            lines.append("Context:")
            for key, value in context.items():
                lines.append(f"  {key}: {value}")

        lines.append("=" * 80)
        lines.append("")

        with self.file_lock:
            if self.file_handle:
                self.file_handle.write('\n'.join(lines) + '\n')
                self.file_handle.flush()

    def write_summary(self, summary_text: str):
        """
        Write batch summary to log file (global only).

        Args:
            summary_text: Formatted summary text (ANSI codes already stripped)
        """
        if self.log_type != "global":
            return  # Only global log gets summary

        # Ensure file is open
        if self.file_handle is None:
            self._ensure_log_file_ready()

        with self.file_lock:
            if self.file_handle:
                self.file_handle.write('\n')
                self.file_handle.write(summary_text)
                self.file_handle.write('\n')
                self.file_handle.flush()

    def close(self):
        """
        Close log file.
        Safe to call multiple times.
        """
        with self.file_lock:
            if self.file_handle:
                # Write footer
                footer_lines = [
                    "",
                    "=" * 80,
                    f"Log completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    "=" * 80,
                ]
                self.file_handle.write('\n'.join(footer_lines) + '\n')

                # Close file
                self.file_handle.close()
                self.file_handle = None

    def __enter__(self):
        """Context manager support."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager support."""
        self.close()
        return False
