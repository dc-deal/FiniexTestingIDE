"""
FiniexTestingIDE - Scenario Logger
Logger for scenario-specific logs (workers, decisions, execution)

Characteristics:
- Elapsed time timestamps (relative to scenario start)
- Console buffering (flush at end for clean output)
- Direct file output (per scenario file)
- One instance per scenario

Usage:
    # Created automatically by SingleScenario
    scenario.logger.info("Worker initialized")
    
    # Flush at end
    scenario.logger.flush_buffer()
"""

from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from python.framework.logging.abstract_logger import AbstractLogger, ColorCodes
from python.framework.logging.file_logger import FileLogger
from python.framework.store.entry_members.run_entry_members import RUN_ENTRY_MEMBERS_BY_ID
from python.framework.store.entry_members.run_entry_writer import RunEntryWriter
from python.framework.types.log_level import LogLevel
from python.framework.types.log_record_types import LogRecord
from python.framework.types.store_member_types import RunEntryMemberId
from python.framework.utils.file_utils import create_directory
from python.framework.utils.time_utils import format_log_elapsed


class ScenarioLogger(AbstractLogger):
    """
    Scenario-specific logger with buffered console output.

    Features:
    - Elapsed time timestamps (e.g., "[ 3s 417ms]")
    - Console buffering (prevents chaos during parallel execution)
    - Direct file output (one file per scenario)
    - Automatic flush on errors
    """

    def __init__(self,
                 scenario_set_name: str,
                 scenario_name: str,
                 run_timestamp: datetime,
                 run_id: str,
                 log_root_override: Optional[Path] = None,
                 log_member: Optional[RunEntryMemberId] = None,
                 log_names: Optional[Dict[str, str]] = None,
                 run_type: Optional[str] = None,
                 use_global_log_level_for_console: bool = False,
                 event_time_column: bool = False,
                 flat_log_path: Optional[Path] = None
                 ):
        """
        Initialize scenario logger.

        Args:
            scenario_set_name: Scenario set name
            scenario_name: The logger's name, shown on its lines (e.g., "01_GBPUSD_window_01")
            run_timestamp: Run start — the baseline the elapsed column is measured against
            run_id: The run's identity, minted ONCE per run and used verbatim as the run
                directory name. Not derived here: every logger of one run must land in the same
                directory, and three loggers deriving three ids would give three directories
            log_root_override: Custom log root path (bypasses config). Used by AutoTrader for separate log tree.
            log_member: The log of the run's record this logger writes — the run store's
                declaration names its file. None writes no file: a run directory holds only
                declared files
            log_names: The variable parts of that member's file name, such as a scenario's
                number; the configured prefix of a backtest log is filled in here
            run_type: The pipeline the run belongs to — what decides which logs it may write
            event_time_column: This log lies on the run's own time axis, so every line carries
                the event-time column. True for the per-scenario logs and the AutoTrader session
                log; false for the run-level logs (global, summary, system info), which describe the
                run from outside rather than from a moment inside it
            flat_log_path: Write this ONE file instead of opening a <owner>/<run_id>/ run
                directory. For output that describes something other than a run — today the
                shared data load a sweep performs once for all its combinations, a member of the
                sweep's own declaration. Without it, work that is not a run leaves a directory
                shaped like one, and the API's run index has no way to tell the two apart
        """
        super().__init__(name=scenario_name, event_time_column=event_time_column)

        self.scenario_set_name = scenario_set_name
        self.run_timestamp = run_timestamp
        self.run_id = run_id
        self._use_global_log_level_for_console = use_global_log_level_for_console

        self.run_dir = None
        self.file_logger = None

        # The caller hands over the root it belongs to — an AutoTrader session, a standalone
        # run, or one sweep's combinations. They come from config (file_logging.run_logs),
        # which is the same source the API reads.
        log_root = (log_root_override if log_root_override
                    else self._file_logging_config.run_logs.simulation)
        if not flat_log_path:
            # Create scenario run directory. It is the run's record — header, reports, streams —
            # so the IDE creates it whether or not log files are written: the switch below
            # decides the log files alone (#621).
            self.run_dir = create_directory(
                log_root / scenario_set_name / run_id,
                holds="the run's record: header, reports and streams",
                setting='file_logging.run_logs')

        if self._file_logging_config.scenario_enabled:
            # One file, straight into the given place when what is logged is not a run;
            # otherwise the file the declaration names for this logger's member, if it has one.
            log_path = flat_log_path or self._declared_log_path(log_member, log_names, run_type)
            if log_path is not None:
                create_directory(log_path.parent, holds=f'the log {log_path.name}',
                                 setting='file_logging.run_logs')
                self.file_logger = FileLogger(
                    log_filename=log_path.name,
                    file_path=log_path.parent,
                    log_level=self._file_logging_config.scenario_log_level
                )
        else:
            # File logging disabled for scenarios
            pass

    def _declared_log_path(self, log_member: Optional[RunEntryMemberId],
                           log_names: Optional[Dict[str, str]],
                           run_type: Optional[str]) -> Optional[Path]:
        """
        Where this logger's member lies in its run directory.

        Args:
            log_member: The member, or None for a logger that writes no file
            log_names: The variable parts of its file name
            run_type: The pipeline the run belongs to

        Returns:
            The path, or None when the logger has no member
        """
        if log_member is None:
            return None
        names = dict(log_names or {})
        # A backtest log's file name begins with a prefix the configuration names; a session
        # log's prefix is part of its declared name.
        if 'log_prefix' in RUN_ENTRY_MEMBERS_BY_ID[log_member].placeholders():
            names['log_prefix'] = self._file_logging_config.scenario_file_name_prefix
        return RunEntryWriter(self.run_dir, run_type).log_path(log_member, **names)

    def _render_timestamp(self, record: LogRecord) -> str:
        """
        Render the record's observation time as elapsed time since scenario start.

        Args:
            record: The entry whose timestamp to render

        Returns:
            Elapsed time string (e.g., "[ 3s 417ms]")
        """
        return format_log_elapsed((record.timestamp - self.run_timestamp).total_seconds())

    def _should_log_console(self, level: LogLevel) -> bool:
        """
        check if console log is enabled for logger
        """
        effective_level = (
            self._console_logging_config.global_log_level
            if self._use_global_log_level_for_console
            else self._console_logging_config.scenario_log_level
        )
        return LogLevel.should_log(level, effective_level)

    def _should_log_file(self, level: LogLevel) -> bool:
        """
         check if file log is enabled for logger
        """
        return LogLevel.should_log(
            level, self._file_logging_config.scenario_log_level)

    def should_log_level(self, level: LogLevel):
        """
        check if any log is active - usecase: scenario silent mode (only file log)
        """
        should_log_console = self._console_logging_config.should_log_scenarios(
        ) and self._should_log_console(level)
        should_log_file = self._file_logging_config.is_file_logging_enabled(
        ) and self._should_log_file(level)
        return should_log_console or should_log_file

    def get_run_timestamp(self):
        return self.run_timestamp

    def get_log_dir(self):
        return self.run_dir

    def _log_console_implementation(self, record: LogRecord):
        """
        Buffer the record for the scenario log.

        Console: Buffered (flush at end) — NO explicit print. Scenario buffers are printed
        after the scenario run, so parallel scenarios do not interleave their output.
        File: Direct write (no buffer), handled separately.

        Args:
            record: The entry to buffer
        """
        self.console_buffer.append(record)

    def _capture_for_report(self, record: LogRecord) -> None:
        """
        Keep a WARNING/ERROR the console threshold suppressed — the run report still needs it.

        Args:
            record: The entry to keep
        """
        self.console_buffer.append(record)

    def _write_to_file_implementation(self, record: LogRecord):
        """
        Write directly to scenario log file.

        The elapsed timestamp is rendered HERE, not in FileLogger: it is measured against this
        logger's run_timestamp, which the file sink does not have.

        Args:
            record: The entry to write
        """
        if self.file_logger is not None:
            # Write to file (plain text, elapsed time, no colours)
            self.file_logger.write_log(
                record, self._render_timestamp(record), self._event_time_column)

    def flush_buffer(self):
        """
        Flush console buffer to stdout.

        Called at end of scenario execution or on errors.
        Outputs all buffered logs in order.
        """
        if not self.console_buffer:
            return

        # Print scenario header
        print(f"\n{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}")
        header_text = f'📊 SCENARIO: {self.name}'
        print(f'{ColorCodes.BOLD}{header_text.center(60)}{ColorCodes.RESET}')
        print(f"{ColorCodes.BOLD}{'='*60}{ColorCodes.RESET}")

        # Output all buffered logs
        effective_level = (
            self._console_logging_config.global_log_level
            if self._use_global_log_level_for_console
            else self._console_logging_config.scenario_log_level
        )
        # The filter is load-bearing: WARNING/ERROR are buffered even when the console
        # threshold suppresses them, because the run report needs them. They are removed
        # HERE, at display time, so the console output is unchanged by that wider capture.
        for record in self.console_buffer:
            if LogLevel.should_log(record.level, effective_level):
                print(AbstractLogger.render_record(
                    record, run_start=self.run_timestamp,
                    event_column=self._event_time_column))

        # Clear buffer
        self.console_buffer.clear()

    def close(self, flush_buffer: bool = False):
        """
        Close logger and flush any remaining buffers.

        Call at end of scenario execution.
        """
        # Flush console buffer
        if flush_buffer:
            self.flush_buffer()
        self.console_buffer.clear()

        # Close file logger
        if self.file_logger:
            self.file_logger.close()

    def swap_file_logger(self, new_file_logger: FileLogger) -> None:
        """
        Replace the current file logger with a new one.

        Used by AutoTrader for daily session log rotation:
        when the date changes, the tick loop creates a new FileLogger
        pointing to a new day's file and swaps it in.

        Args:
            new_file_logger: New FileLogger instance (already opened)
        """
        if self.file_logger:
            self.file_logger.close()
        self.file_logger = new_file_logger

