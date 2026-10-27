"""
FiniexTestingIDE - Store Member Types (#621)
The vocabulary a store declares ONE of its entries in: every file the entry may hold, with the shape
that decides how it is written and read, when it is written, and which run index column lists it.

Pure data, plus matching a path against a declared pattern. The declarations themselves live with
the store, in `python/framework/store/entry_members/`.
"""

import re
import string
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Optional, Tuple, Union

from python.framework.constants.run_layout_constants import (
    DIAGNOSTICS_ROWS_STREAM,
    ORDER_EVENTS_STREAM,
    STATE_FRAMES_STREAM,
    TICK_STREAM_EVENTS_STREAM,
    TRADES_STREAM,
)
from python.framework.exceptions.store_errors import EntryMemberRefusedError
from python.framework.types.run_stream_types import RecordedStreamKind

# A placeholder stands for one variable part of a file name and never spans a directory.
_PLACEHOLDER_REGEX = r'[^/]+'

# What an example fills a placeholder with — invented values, read by the declaration's test and
# by the overview the store prints.
_EXAMPLE_VALUES = {
    'log_prefix': 'scenario',
    'scenario_no': '01',
    'scenario': 'window_01',
    'day': '20261022',
    'sink': 'entry_signals',
    'name': 'last_setup.json',
}


class EntryMemberShape(StrEnum):
    """
    What kind of file a member is. The shape decides how the file is written and read: the writer
    belongs to the shape, as the reader belongs to the retrieval form.
    """
    SNAPSHOT = 'snapshot'            # one document saying what is true now, replaced whole
    STREAM = 'stream'                # records appended line by line, a header line first
    REPORT_OUTPUT = 'report_output'  # derived by the reporting pipeline at the run's end
    LOG = 'log'                      # lines for a person to read, never parsed by the code
    STRATEGY_OUTPUT = 'strategy_output'  # a strategy's own file: kept with the run, never read
    DAY_RECORD = 'day_record'        # a stream's sealed trading day: written once, whole, never again


class EntryMemberMoment(StrEnum):
    """
    When in a run's life a member's file first appears — and so what a run that stopped at that
    point already holds. A file created at the start and filled at the end appears at the start.
    """
    RUN_START = 'run_start'
    AS_IT_RUNS = 'as_it_runs'        # during the run, when something happens — perhaps never
    RUN_END = 'run_end'              # written by the reporting once the run has ended
    ON_DEMAND = 'on_demand'          # by a command run afterwards, not by the run itself


class EntryMemberWriteMode(StrEnum):
    """How a member's bytes reach the disk — and so what a crash in the middle leaves behind."""
    # a temporary file renamed over the old one: old or new, never half
    ATOMIC_REPLACE = 'atomic_replace'
    IN_PLACE = 'in_place'              # written over in place: a crash can leave a cut file
    APPEND_LINES = 'append_lines'      # lines appended and flushed: a crash can cut the last line


class RunIndexColumn(StrEnum):
    """The run index column that lists a member's file — what the API promises a reader."""
    ARTIFACTS = 'artifacts'          # a report section, there once the run has reported
    STREAM_FILES = 'stream_files'    # a stream, served while the run goes on


class RunEntryMemberId(StrEnum):
    """
    Every file a run directory may hold. A run stream's id is the name its header line gives; the
    acceptance studies' captures name no stream in theirs.
    """
    HEADER = 'header'
    BACKTEST_GLOBAL_LOG = 'backtest_global_log'
    BACKTEST_SUMMARY_LOG = 'backtest_summary_log'
    BACKTEST_SYSTEM_INFO_LOG = 'backtest_system_info_log'
    SCENARIO_LOG = 'scenario_log'
    SESSION_GLOBAL_LOG = 'session_global_log'
    SESSION_SUMMARY_LOG = 'session_summary_log'
    SESSION_BOOT_LOG = 'session_boot_log'
    SESSION_DAY_LOG = 'session_day_log'
    SCENARIO_EVENTS = 'scenario_events'
    SESSION_EVENTS = 'session_events'
    SCENARIO_DIAGNOSTICS = 'scenario_diagnostics'
    SESSION_DIAGNOSTICS = 'session_diagnostics'
    SCENARIO_STRATEGY_FILE = 'scenario_strategy_file'
    SESSION_STRATEGY_FILE = 'session_strategy_file'
    FIELD_STUDY_CAPTURE = 'field_study_capture'
    SECURITY_CERT_CAPTURE = 'security_cert_capture'
    AGGREGATED_PORTFOLIO = 'aggregated_portfolio'
    BLOCK_SPLITTING = 'block_splitting'
    BOOKING_PERIODS = 'booking_periods'
    BROKER = 'broker'
    COLD_START = 'cold_start'
    EXECUTION_STATS = 'execution_stats'
    FEED_STABILITY = 'feed_stability'
    ORDER_HISTORY = 'order_history'
    PENDING_ORDERS = 'pending_orders'
    PORTFOLIO = 'portfolio'
    PROFILING = 'profiling'
    ROBUSTNESS = 'robustness'
    RUN_META = 'run_meta'
    RUN_SUMMARY = 'run_summary'
    SAFETY = 'safety'
    SCENARIO_DETAILS = 'scenario_details'
    SIGNAL = 'signal'
    TRADE_HISTORY = 'trade_history'
    VENUE_ACCOUNT = 'venue_account'
    WARNINGS_ERRORS = 'warnings_errors'
    WORKER_DECISION = 'worker_decision'
    EXECUTION_STATS_CSV = 'execution_stats_csv'
    ORDER_HISTORY_CSV = 'order_history_csv'
    TRADE_HISTORY_CSV = 'trade_history_csv'
    ORDER_EVENTS = ORDER_EVENTS_STREAM
    STATE_FRAMES = STATE_FRAMES_STREAM
    TICK_STREAM_EVENTS = TICK_STREAM_EVENTS_STREAM
    TRADES = TRADES_STREAM
    DIAGNOSTICS_ROWS = DIAGNOSTICS_ROWS_STREAM
    ORDER_EVENTS_DAY = 'order_events_day'
    STATE_FRAMES_DAY = 'state_frames_day'
    TICK_STREAM_EVENTS_DAY = 'tick_stream_events_day'
    TRADES_DAY = 'trades_day'
    DIAGNOSTICS_ROWS_DAY = 'diagnostics_rows_day'
    WARMUP_HISTORY = 'warmup_history'
    ALGO_STATE_RESTORED = 'algo_state_restored'
    FOOTPRINT = 'footprint'


class SweepEntryMemberId(StrEnum):
    """Every file a sweep directory holds beside its combinations' run directories."""
    MOUNT_BUILD_LOG = 'mount_build_log'
    RANKING = 'ranking'


@dataclass(frozen=True)
class EntryMemberWrite:
    """
    Which pipeline writes a member, when, and under which condition.

    Args:
        run_type: The run type that writes it — one of the run types in the run layout constants
        moment: When in the run's life its file first appears
        condition: When it is written only sometimes, in a few words; empty when always
    """
    run_type: str
    moment: EntryMemberMoment
    condition: str = ''


@dataclass(frozen=True)
class EntryMember:
    """
    One file a store entry may hold.

    Args:
        member_id: Its identity
        path_pattern: Its path inside the entry; a `{name}` part stands for one variable part of a
            file name — a scenario, a trading day, a sink — and never spans a directory
        shape: What kind of file it is
        purpose: What it holds, in one clause
        written_by: Who writes it, when, and under which condition — one entry per pipeline
        write_mode: How its bytes reach the disk
        doc: The document that explains it, relative to the project root, with a heading anchor
        index_column: The run index column that lists it, or None when none does
        schema_version: The version of its line shapes, written into its header — streams only
        served_route: The path the API serves it under, below a run's report routes, or None when
            no route serves it
        time_fields: The fields a stream's line takes its time from — the first one the line
            carries; empty when its lines carry none, None where nothing was declared. A sealed
            stream declares them
        day_record: The member a session's stream is sealed into at each trading-day boundary
            (#476), or None when it is not sealed
        recorded_stream_kind: The kind of stream a unit's timeline and window serve it as (#580),
            or None when they do not serve it
    """
    member_id: Union[RunEntryMemberId, SweepEntryMemberId]
    path_pattern: str
    shape: EntryMemberShape
    purpose: str
    written_by: Tuple[EntryMemberWrite, ...]
    write_mode: EntryMemberWriteMode
    doc: str
    index_column: Optional[RunIndexColumn] = None
    schema_version: Optional[Union[int, str]] = None
    served_route: Optional[str] = None
    time_fields: Optional[Tuple[str, ...]] = None
    day_record: Optional[RunEntryMemberId] = None
    recorded_stream_kind: Optional[RecordedStreamKind] = None

    def placeholders(self) -> Tuple[str, ...]:
        """
        The variable parts of the pattern, in order.

        Returns:
            Each placeholder's name
        """
        return tuple(field for _, field, _, _ in string.Formatter().parse(self.path_pattern)
                     if field is not None)

    def matches(self, relative_path: str) -> bool:
        """
        Whether a file inside the entry is this member.

        Args:
            relative_path: The file's path inside the entry, with forward slashes

        Returns:
            True when the whole path fits the pattern
        """
        return _pattern_regex(self.path_pattern).fullmatch(relative_path) is not None

    def is_written_by(self, run_type: str) -> bool:
        """
        Whether a pipeline writes this member at all.

        Args:
            run_type: One of the run types in the run layout constants

        Returns:
            True when the member is declared for that run type
        """
        return any(write.run_type == run_type for write in self.written_by)

    def require_shape(self, shape: EntryMemberShape) -> None:
        """
        Refuse a write of another shape than this member is — a log opened as a table, a
        stream written as a snapshot.

        Args:
            shape: The shape the writing method writes
        """
        if self.shape is not shape:
            raise EntryMemberRefusedError(
                f'{self.member_id.value} is a {self.shape.value}, not a {shape.value}')

    def path_for(self, **names: str) -> str:
        """
        The member's path inside the entry, its variable parts filled with the given names.

        Args:
            names: One value per placeholder of the pattern

        Returns:
            The path, with forward slashes
        """
        wanted = set(self.placeholders())
        if set(names) != wanted:
            raise EntryMemberRefusedError(
                f'{self.member_id.value} needs the name parts {sorted(wanted)}, was given '
                f'{sorted(names)}')
        for name, value in names.items():
            if not value or '/' in value or '\\' in value or value in ('.', '..'):
                raise EntryMemberRefusedError(
                    f'{self.member_id.value}: the name part {name}={value!r} must be one plain '
                    f'name — not empty, not "." or "..", and no directory separator')
        return self.path_pattern.format(**names)

    def example(self) -> str:
        """
        The member's path with every placeholder filled by an invented example value.

        Returns:
            A path the member matches
        """
        return self.path_pattern.format(**{name: _EXAMPLE_VALUES[name]
                                           for name in self.placeholders()})


@lru_cache(maxsize=None)
def _pattern_regex(path_pattern: str) -> re.Pattern:
    """
    The compiled form of a member's pattern — built at its first use, never at import.

    Args:
        path_pattern: The declared pattern

    Returns:
        A regular expression matching exactly the paths the pattern stands for
    """
    parts = []
    for literal, field, _, _ in string.Formatter().parse(path_pattern):
        parts.append(re.escape(literal))
        if field is not None:
            parts.append(_PLACEHOLDER_REGEX)
    return re.compile(''.join(parts))
