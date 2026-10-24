"""
FiniexTestingIDE - Run Entry Members (#621)
What a run directory may hold: every file, declared once, with the shape that decides how it is
written and read. The writers build their paths from the same names in the run layout constants,
and the census holds the two together.

Constants only at import — no configuration is read and no pattern is compiled until something
asks, because every process that imports the run index imports this.
"""

from typing import Dict, FrozenSet, Optional, Tuple

from python.framework.constants.run_layout_constants import (
    ACCEPTANCE_CAPTURE_SCHEMA_VERSION,
    AGGREGATED_PORTFOLIO_ARTIFACT_FILE,
    BACKTEST_GLOBAL_LOG_NAME,
    BACKTEST_SYSTEM_INFO_LOG_NAME,
    BLOCK_SPLITTING_ARTIFACT_FILE,
    BOOKING_PERIODS_ARTIFACT_FILE,
    BROKER_ARTIFACT_FILE,
    COLD_START_ARTIFACT_FILE,
    DIAGNOSTICS_FILE_PATTERN,
    DIAGNOSTICS_SUBDIR,
    EVENTS_SUBDIR,
    EXECUTION_STATS_ARTIFACT_FILE,
    EXECUTION_STATS_CSV,
    FEED_STABILITY_ARTIFACT_FILE,
    FIELD_STUDY_CAPTURE,
    FOOTPRINT_FILE,
    IO_SUBDIR,
    ORDER_EVENTS_FILE,
    ORDER_EVENTS_SCHEMA_VERSION,
    ORDER_HISTORY_ARTIFACT_FILE,
    ORDER_HISTORY_CSV,
    PENDING_ORDERS_ARTIFACT_FILE,
    PORTFOLIO_ARTIFACT_FILE,
    PROFILING_ARTIFACT_FILE,
    ROBUSTNESS_ARTIFACT_FILE,
    RUN_HEADER_ARTIFACT,
    RUN_LOG_FILE_PATTERN,
    RUN_META_ARTIFACT_FILE,
    RUN_SUMMARY_ARTIFACT_FILE,
    RUN_SUMMARY_LOG_NAME,
    RUN_TYPE_AUTOTRADER,
    RUN_TYPE_SIMULATION,
    SAFETY_ARTIFACT_FILE,
    SCENARIO_DETAILS_ARTIFACT_FILE,
    SCENARIO_DIAGNOSTICS_FILE_PATTERN,
    SCENARIO_EVENTS_FILE_PATTERN,
    SCENARIO_LOG_NAME_PATTERN,
    SCENARIO_LOGS_SUBDIR,
    SCENARIO_STRATEGY_FILE_PATTERN,
    SECURITY_CERT_CAPTURE,
    SESSION_DAY_LOG_FILE_PATTERN,
    SESSION_EVENTS_FILE,
    SESSION_GLOBAL_LOG_NAME,
    SESSION_LOG_NAME,
    SESSION_LOG_PREFIX,
    SESSION_LOGS_SUBDIR,
    SIGNAL_ARTIFACT_FILE,
    STATE_FRAMES_FILE,
    STATE_FRAMES_SCHEMA_VERSION,
    STRATEGY_FILE_PATTERN,
    STRATEGY_FILES_SUBDIR,
    TICK_STREAM_EVENTS_FILE,
    TICK_STREAM_EVENTS_SCHEMA_VERSION,
    TRADE_HISTORY_ARTIFACT_FILE,
    TRADE_HISTORY_CSV,
    VENUE_ACCOUNT_ARTIFACT_FILE,
    WARMUP_HISTORY_FILE,
    WARNINGS_ERRORS_ARTIFACT_FILE,
    WORKER_DECISION_ARTIFACT_FILE,
)
from python.framework.exceptions.store_errors import EntryMemberRefusedError
from python.framework.types.store_member_types import (
    EntryMember,
    EntryMemberMoment,
    EntryMemberShape,
    EntryMemberWrite,
    EntryMemberWriteMode,
    RunEntryMemberId,
    RunIndexColumn,
)

_DOC_BACKTEST_LOGS = 'docs/process_execution_guide.md#6-per-scenario-logger-files'
_DOC_EVENTS = 'docs/architecture/trade_execution_visibility.md#event-stream-csv-eventscsv'
_DOC_SESSION_LOGS = ('docs/autotrader/autotrader_architecture.md'
                     '#session-logs--daily-rotation-and-what-is-kept')
_DOC_OBSERVABILITY = 'docs/autotrader/autotrader_observability.md#logging'
_DOC_SECTIONS = 'docs/architecture/reporting_pipeline.md#report-sections--domain--migration-status'
_DOC_DIAGNOSTICS = 'docs/architecture/diagnostics_csv_sink.md#lifecycle-framework-owned'
_DOC_STRATEGY_FILES = 'docs/architecture/store_entry_members.md#strategy-files'


def _backtest_log(log_name: str) -> str:
    """
    A backtest log's file name: the prefix stays a placeholder, because configuration names it.

    Args:
        log_name: The log's own part of the name

    Returns:
        The member's pattern
    """
    return RUN_LOG_FILE_PATTERN.format(log_prefix='{log_prefix}', log_name=log_name)


def _session_log(log_name: str) -> str:
    """
    An AutoTrader session log's file name, its prefix fixed.

    Args:
        log_name: The log's own part of the name

    Returns:
        The file name
    """
    return RUN_LOG_FILE_PATTERN.format(log_prefix=SESSION_LOG_PREFIX, log_name=log_name)


def _at_end(*run_types: str, condition: str = '') -> Tuple[EntryMemberWrite, ...]:
    """
    Written at the run's end by each of the given pipelines.

    Args:
        run_types: The run types that write it
        condition: When it is written only sometimes

    Returns:
        One write per pipeline
    """
    return tuple(EntryMemberWrite(run_type, EntryMemberMoment.RUN_END, condition)
                 for run_type in run_types)


def _section(member_id: RunEntryMemberId, served_route: Optional[str], file_name: str,
             purpose: str, written_by: Tuple[EntryMemberWrite, ...]) -> EntryMember:
    """
    A report section in io/: written once at the run's end and listed among the run's artifacts.

    Args:
        member_id: Its identity
        served_route: The path the API serves it under, or None when no route serves it
        file_name: Its file name in io/
        purpose: What it holds
        written_by: Who writes it

    Returns:
        The member
    """
    return EntryMember(member_id, f'{IO_SUBDIR}/{file_name}', EntryMemberShape.REPORT_OUTPUT,
                       purpose, written_by, EntryMemberWriteMode.IN_PLACE, _DOC_SECTIONS,
                       index_column=RunIndexColumn.ARTIFACTS, served_route=served_route)


_BOTH = (RUN_TYPE_SIMULATION, RUN_TYPE_AUTOTRADER)

# A log is written only while the log switch is on (file_logging.scenario.enabled); the run's
# directory and every other member are written either way.
_LOGS_ON = 'log files are switched on'

RUN_ENTRY_MEMBERS: Tuple[EntryMember, ...] = (
    # --- The run's identity, beside everything else
    EntryMember(
        RunEntryMemberId.HEADER, RUN_HEADER_ARTIFACT, EntryMemberShape.SNAPSHOT,
        'what the run is: kind, windows, configuration and code identity',
        tuple(EntryMemberWrite(run_type, EntryMemberMoment.RUN_START) for run_type in _BOTH),
        EntryMemberWriteMode.IN_PLACE,
        'docs/architecture/run_origin_and_code_identity.md#what-a-run-header-carries',
        served_route='header'),
    EntryMember(
        RunEntryMemberId.FOOTPRINT, FOOTPRINT_FILE, EntryMemberShape.SNAPSHOT,
        'what the run wrote and why it left a declared file out, written as its very last step',
        (EntryMemberWrite(RUN_TYPE_SIMULATION, EntryMemberMoment.RUN_END,
                          'the run was commissioned to report'),
         EntryMemberWrite(RUN_TYPE_AUTOTRADER, EntryMemberMoment.RUN_END)),
        EntryMemberWriteMode.ATOMIC_REPLACE,
        'docs/architecture/store_entry_members.md#the-footprint-a-run-closes-with'),

    # --- A backtest's logs
    EntryMember(
        RunEntryMemberId.BACKTEST_GLOBAL_LOG, _backtest_log(BACKTEST_GLOBAL_LOG_NAME),
        EntryMemberShape.LOG, 'the scenario set\'s own log: preparation, execution, reporting',
        (EntryMemberWrite(RUN_TYPE_SIMULATION, EntryMemberMoment.RUN_START, _LOGS_ON),),
        EntryMemberWriteMode.APPEND_LINES, _DOC_BACKTEST_LOGS),
    EntryMember(
        RunEntryMemberId.BACKTEST_SUMMARY_LOG, _backtest_log(RUN_SUMMARY_LOG_NAME),
        EntryMemberShape.LOG, 'the run\'s printed summary, written at its end',
        (EntryMemberWrite(RUN_TYPE_SIMULATION, EntryMemberMoment.RUN_START, _LOGS_ON),),
        EntryMemberWriteMode.APPEND_LINES, _DOC_BACKTEST_LOGS),
    EntryMember(
        RunEntryMemberId.BACKTEST_SYSTEM_INFO_LOG, _backtest_log(BACKTEST_SYSTEM_INFO_LOG_NAME),
        EntryMemberShape.LOG, 'the machine and versions the run executed on',
        (EntryMemberWrite(RUN_TYPE_SIMULATION, EntryMemberMoment.RUN_START,
                          'log files and system information logging are switched on'),),
        EntryMemberWriteMode.APPEND_LINES, _DOC_BACKTEST_LOGS),
    EntryMember(
        RunEntryMemberId.SCENARIO_LOG,
        f'{SCENARIO_LOGS_SUBDIR}/{_backtest_log(SCENARIO_LOG_NAME_PATTERN)}',
        EntryMemberShape.LOG, 'one scenario\'s own log, written by its process',
        (EntryMemberWrite(RUN_TYPE_SIMULATION, EntryMemberMoment.AS_IT_RUNS,
                          f'one per scenario, when {_LOGS_ON}'),),
        EntryMemberWriteMode.APPEND_LINES, _DOC_BACKTEST_LOGS),

    # --- An AutoTrader session's logs
    EntryMember(
        RunEntryMemberId.SESSION_GLOBAL_LOG, _session_log(SESSION_GLOBAL_LOG_NAME),
        EntryMemberShape.LOG, 'the session\'s start, shutdown and framework errors',
        (EntryMemberWrite(RUN_TYPE_AUTOTRADER, EntryMemberMoment.RUN_START, _LOGS_ON),),
        EntryMemberWriteMode.APPEND_LINES, _DOC_OBSERVABILITY),
    EntryMember(
        RunEntryMemberId.SESSION_SUMMARY_LOG, _session_log(RUN_SUMMARY_LOG_NAME),
        EntryMemberShape.LOG, 'the session\'s printed summary, written at its end',
        (EntryMemberWrite(RUN_TYPE_AUTOTRADER, EntryMemberMoment.RUN_START, _LOGS_ON),),
        EntryMemberWriteMode.APPEND_LINES, _DOC_OBSERVABILITY),
    EntryMember(
        RunEntryMemberId.SESSION_BOOT_LOG, _session_log(SESSION_LOG_NAME),
        EntryMemberShape.LOG, 'the session log until the first tick',
        (EntryMemberWrite(RUN_TYPE_AUTOTRADER, EntryMemberMoment.RUN_START, _LOGS_ON),),
        EntryMemberWriteMode.APPEND_LINES, _DOC_SESSION_LOGS),
    EntryMember(
        RunEntryMemberId.SESSION_DAY_LOG, f'{SESSION_LOGS_SUBDIR}/{SESSION_DAY_LOG_FILE_PATTERN}',
        EntryMemberShape.LOG, 'the session log of one trading day',
        (EntryMemberWrite(RUN_TYPE_AUTOTRADER, EntryMemberMoment.AS_IT_RUNS,
                          f'one per trading day, when {_LOGS_ON}; the retention removes '
                          f'the oldest'),),
        EntryMemberWriteMode.APPEND_LINES, _DOC_SESSION_LOGS),

    # --- The event tables and the strategy's own diagnostics
    EntryMember(
        RunEntryMemberId.SCENARIO_EVENTS, f'{EVENTS_SUBDIR}/{SCENARIO_EVENTS_FILE_PATTERN}',
        EntryMemberShape.REPORT_OUTPUT, 'one scenario\'s fills and order transitions as one table',
        _at_end(RUN_TYPE_SIMULATION, condition='one per scenario that placed an order'),
        EntryMemberWriteMode.IN_PLACE, _DOC_EVENTS),
    EntryMember(
        RunEntryMemberId.SESSION_EVENTS, SESSION_EVENTS_FILE,
        EntryMemberShape.REPORT_OUTPUT, 'the session\'s fills and order transitions as one table',
        _at_end(RUN_TYPE_AUTOTRADER, condition='the session placed an order'),
        EntryMemberWriteMode.IN_PLACE, _DOC_EVENTS),
    EntryMember(
        RunEntryMemberId.SCENARIO_DIAGNOSTICS,
        f'{DIAGNOSTICS_SUBDIR}/{SCENARIO_DIAGNOSTICS_FILE_PATTERN}',
        EntryMemberShape.REPORT_OUTPUT, 'a decision logic\'s own diagnostics table, per scenario',
        _at_end(RUN_TYPE_SIMULATION, condition='the logic declares a sink and recorded a row'),
        EntryMemberWriteMode.IN_PLACE, _DOC_DIAGNOSTICS),
    EntryMember(
        RunEntryMemberId.SESSION_DIAGNOSTICS, f'{DIAGNOSTICS_SUBDIR}/{DIAGNOSTICS_FILE_PATTERN}',
        EntryMemberShape.REPORT_OUTPUT, 'a decision logic\'s own diagnostics table',
        _at_end(RUN_TYPE_AUTOTRADER, condition='the logic declares a sink and recorded a row'),
        EntryMemberWriteMode.IN_PLACE, _DOC_DIAGNOSTICS),

    # --- The strategy's own files, written through its helper as it runs
    EntryMember(
        RunEntryMemberId.SCENARIO_STRATEGY_FILE,
        f'{STRATEGY_FILES_SUBDIR}/{SCENARIO_STRATEGY_FILE_PATTERN}',
        EntryMemberShape.STRATEGY_OUTPUT, 'a file the strategy keeps with the run, per scenario',
        (EntryMemberWrite(RUN_TYPE_SIMULATION, EntryMemberMoment.AS_IT_RUNS,
                          'the strategy writes a file of its own'),),
        EntryMemberWriteMode.ATOMIC_REPLACE, _DOC_STRATEGY_FILES),
    EntryMember(
        RunEntryMemberId.SESSION_STRATEGY_FILE, f'{STRATEGY_FILES_SUBDIR}/{STRATEGY_FILE_PATTERN}',
        EntryMemberShape.STRATEGY_OUTPUT, 'a file the strategy keeps with the run',
        (EntryMemberWrite(RUN_TYPE_AUTOTRADER, EntryMemberMoment.AS_IT_RUNS,
                          'the strategy writes a file of its own'),),
        EntryMemberWriteMode.ATOMIC_REPLACE, _DOC_STRATEGY_FILES),

    # --- The acceptance studies' captures
    EntryMember(
        RunEntryMemberId.FIELD_STUDY_CAPTURE, FIELD_STUDY_CAPTURE, EntryMemberShape.STREAM,
        'what the field study saw and what the venue said, phase by phase',
        (EntryMemberWrite(RUN_TYPE_AUTOTRADER, EntryMemberMoment.RUN_START,
                          'a field-study profile'),),
        EntryMemberWriteMode.APPEND_LINES,
        'docs/tests/live_field_study/field_study_guide.md#jsonl-schema',
        schema_version=ACCEPTANCE_CAPTURE_SCHEMA_VERSION),
    EntryMember(
        RunEntryMemberId.SECURITY_CERT_CAPTURE, SECURITY_CERT_CAPTURE, EntryMemberShape.STREAM,
        'what one stage of the security study provoked and what fired',
        (EntryMemberWrite(RUN_TYPE_AUTOTRADER, EntryMemberMoment.RUN_START,
                          'a security-study stage'),),
        EntryMemberWriteMode.APPEND_LINES,
        'docs/tests/security_cert/security_cert_guide.md#the-capture',
        schema_version=ACCEPTANCE_CAPTURE_SCHEMA_VERSION),

    # --- The report sections in io/, written by the reporting pipeline at the run's end
    _section(RunEntryMemberId.AGGREGATED_PORTFOLIO, 'aggregated-portfolio',
             AGGREGATED_PORTFOLIO_ARTIFACT_FILE,
             'the scenarios\' portfolios rolled up per currency', _at_end(RUN_TYPE_SIMULATION)),
    _section(RunEntryMemberId.BLOCK_SPLITTING, None, BLOCK_SPLITTING_ARTIFACT_FILE,
             'how a profile run split its blocks',
             _at_end(RUN_TYPE_SIMULATION, condition='a profile run that split blocks')),
    _section(RunEntryMemberId.BOOKING_PERIODS, 'booking-periods', BOOKING_PERIODS_ARTIFACT_FILE,
             'the run\'s booking periods and their reconciliation', _at_end(*_BOTH)),
    _section(RunEntryMemberId.BROKER, 'broker', BROKER_ARTIFACT_FILE,
             'the broker configuration the run traded with',
             (EntryMemberWrite(RUN_TYPE_SIMULATION, EntryMemberMoment.RUN_END),
              EntryMemberWrite(RUN_TYPE_AUTOTRADER, EntryMemberMoment.RUN_END,
                               'the session built its broker configuration'))),
    _section(RunEntryMemberId.COLD_START, None, COLD_START_ARTIFACT_FILE,
             'what the session inherited at boot and what it decided',
             _at_end(RUN_TYPE_AUTOTRADER, condition='the boot found something to inherit')),
    _section(RunEntryMemberId.EXECUTION_STATS, 'execution-stats', EXECUTION_STATS_ARTIFACT_FILE,
             'order counts per unit', _at_end(*_BOTH)),
    _section(RunEntryMemberId.FEED_STABILITY, 'feed-stability', FEED_STABILITY_ARTIFACT_FILE,
             'the outages of the tick and signal feeds', _at_end(*_BOTH)),
    _section(RunEntryMemberId.ORDER_HISTORY, 'order-history', ORDER_HISTORY_ARTIFACT_FILE,
             'every order and how it ended', _at_end(*_BOTH)),
    _section(RunEntryMemberId.PENDING_ORDERS, 'pending-orders', PENDING_ORDERS_ARTIFACT_FILE,
             'the order lifecycle, its latencies and the orders still open', _at_end(*_BOTH)),
    _section(RunEntryMemberId.PORTFOLIO, 'portfolio', PORTFOLIO_ARTIFACT_FILE,
             'each unit\'s account at the end and its course', _at_end(*_BOTH)),
    _section(RunEntryMemberId.PROFILING, 'profiling', PROFILING_ARTIFACT_FILE,
             'where the run spent its time', _at_end(RUN_TYPE_SIMULATION)),
    _section(RunEntryMemberId.ROBUSTNESS, None, ROBUSTNESS_ARTIFACT_FILE,
             'the robustness validation of the run',
             _at_end(RUN_TYPE_SIMULATION, condition='robustness mode is on')),
    _section(RunEntryMemberId.RUN_META, None, RUN_META_ARTIFACT_FILE,
             'facts about the batch itself', _at_end(RUN_TYPE_SIMULATION)),
    _section(RunEntryMemberId.RUN_SUMMARY, 'run-summary', RUN_SUMMARY_ARTIFACT_FILE,
             'the run\'s headline figures across sections', _at_end(*_BOTH)),
    _section(RunEntryMemberId.SAFETY, None, SAFETY_ARTIFACT_FILE,
             'the risk baseline the session measured against',
             _at_end(RUN_TYPE_AUTOTRADER, condition='a risk baseline was taken')),
    _section(RunEntryMemberId.SCENARIO_DETAILS, 'scenario-details', SCENARIO_DETAILS_ARTIFACT_FILE,
             'each scenario\'s window, data and outcome', _at_end(RUN_TYPE_SIMULATION)),
    _section(RunEntryMemberId.SIGNAL, 'signal', SIGNAL_ARTIFACT_FILE,
             'the signal sources and what the strategy decided on', _at_end(*_BOTH)),
    _section(RunEntryMemberId.TRADE_HISTORY, 'trade-history', TRADE_HISTORY_ARTIFACT_FILE,
             'every trade with its analytics', _at_end(*_BOTH)),
    _section(RunEntryMemberId.VENUE_ACCOUNT, 'venue-account', VENUE_ACCOUNT_ARTIFACT_FILE,
             'what the venue held at the session\'s start and end',
             _at_end(RUN_TYPE_AUTOTRADER, condition='the session asked its venue')),
    _section(RunEntryMemberId.WARNINGS_ERRORS, 'warnings-errors', WARNINGS_ERRORS_ARTIFACT_FILE,
             'the warnings and errors, tiered, and the run\'s outcome', _at_end(*_BOTH)),
    _section(RunEntryMemberId.WORKER_DECISION, 'worker-decision', WORKER_DECISION_ARTIFACT_FILE,
             'the workers\' and the decision\'s performance', _at_end(*_BOTH)),
    _section(RunEntryMemberId.EXECUTION_STATS_CSV, None, EXECUTION_STATS_CSV,
             'the order counts as a table', _at_end(*_BOTH)),
    _section(RunEntryMemberId.ORDER_HISTORY_CSV, None, ORDER_HISTORY_CSV,
             'the orders as a table', _at_end(*_BOTH)),
    _section(RunEntryMemberId.TRADE_HISTORY_CSV, None, TRADE_HISTORY_CSV,
             'the trades as a table', _at_end(*_BOTH)),

    # --- The run streams and the records a session writes as it runs, in io/
    EntryMember(
        RunEntryMemberId.ORDER_EVENTS, f'{IO_SUBDIR}/{ORDER_EVENTS_FILE}', EntryMemberShape.STREAM,
        'every order transition, and what the venue reported',
        (EntryMemberWrite(RUN_TYPE_SIMULATION, EntryMemberMoment.RUN_END,
                          'a scenario recorded an order event'),
         EntryMemberWrite(RUN_TYPE_AUTOTRADER, EntryMemberMoment.RUN_START,
                          'the session got through its startup checks')),
        EntryMemberWriteMode.APPEND_LINES, 'docs/architecture/order_event_stream.md#storage',
        index_column=RunIndexColumn.STREAM_FILES, schema_version=ORDER_EVENTS_SCHEMA_VERSION,
        served_route='order-events'),
    EntryMember(
        RunEntryMemberId.STATE_FRAMES, f'{IO_SUBDIR}/{STATE_FRAMES_FILE}', EntryMemberShape.STREAM,
        'what the session held and decided, moment by moment',
        (EntryMemberWrite(RUN_TYPE_AUTOTRADER, EntryMemberMoment.RUN_START,
                          'the profile records state frames'),),
        EntryMemberWriteMode.APPEND_LINES,
        'docs/architecture/live_telemetry_architecture.md#the-recorded-state-frame--not-this-stream',
        index_column=RunIndexColumn.STREAM_FILES, schema_version=STATE_FRAMES_SCHEMA_VERSION,
        served_route='state-frames/latest'),
    EntryMember(
        RunEntryMemberId.TICK_STREAM_EVENTS, f'{IO_SUBDIR}/{TICK_STREAM_EVENTS_FILE}',
        EntryMemberShape.STREAM, 'the tick stream\'s outages and the trades it refused',
        (EntryMemberWrite(RUN_TYPE_AUTOTRADER, EntryMemberMoment.RUN_START,
                          'the session reads its ticks from a venue'),),
        EntryMemberWriteMode.APPEND_LINES,
        'docs/autotrader/autotrader_architecture.md#what-a-session-records',
        schema_version=TICK_STREAM_EVENTS_SCHEMA_VERSION),
    EntryMember(
        RunEntryMemberId.WARMUP_HISTORY, f'{IO_SUBDIR}/{WARMUP_HISTORY_FILE}',
        EntryMemberShape.SNAPSHOT,
        'the warmup bars the session started from, written again when the first tick closes '
        'the seam',
        (EntryMemberWrite(RUN_TYPE_AUTOTRADER, EntryMemberMoment.RUN_START,
                          'a worker requires bars'),),
        EntryMemberWriteMode.ATOMIC_REPLACE,
        'docs/architecture/warmup_history.md#what-a-session-records'),
)

RUN_ENTRY_MEMBERS_BY_ID: Dict[RunEntryMemberId, EntryMember] = {
    member.member_id: member for member in RUN_ENTRY_MEMBERS}

# The members each run index column lists — what the API promises a reader of the run list.
RUN_INDEX_COLUMN_MEMBERS: Dict[RunIndexColumn, Tuple[EntryMember, ...]] = {
    column: tuple(member for member in RUN_ENTRY_MEMBERS if member.index_column is column)
    for column in RunIndexColumn}

# The directories a run directory holds: the first part of every member path that has one. They go
# with the run that contains them and are never a run of their own.
RUN_ENTRY_SUBDIRS: FrozenSet[str] = frozenset(
    member.path_pattern.split('/', 1)[0] for member in RUN_ENTRY_MEMBERS
    if '/' in member.path_pattern)


def section_member_of(file_name: str) -> EntryMember:
    """
    The member a report section in io/ is, by the section's file name.

    Args:
        file_name: The section's file name

    Returns:
        Its member
    """
    path = f'{IO_SUBDIR}/{file_name}'
    for member in RUN_ENTRY_MEMBERS:
        if member.path_pattern == path:
            return member
    raise EntryMemberRefusedError(f'no member of a run is the report section {file_name}')
