"""
FiniexTestingIDE - Run Layout Constants
The run types — the API's `group` values, and the keys of file_logging.run_logs — and the name of
every file a run or a sweep directory holds, with the version of each stream's line shapes.
"""

# A run has exactly ONE type, and the type IS its `group` in the API:
#
#   simulation    a backtest — standalone, or one combination of a parameter sweep
#   autotrader    an AutoTrader session — mock, dry run or real orders alike
#
# TWO values, not three, because type and NESTING are different questions and each has its own
# field. A sweep combination is a simulation with a `parent_id`; a live day record (#476) will be an
# autotrader run with a `parent_id`. Folding nesting into the type would make the most basic
# question — "is this a simulation?" — a two-value comparison, and would need a new value for every
# new kind of parent.
#
# WHERE each type writes is configuration (file_logging.run_logs) — one source read by the
# writers and by ReportStore alike, so a moved log root cannot make runs invisible. The NAMES
# live here because they are a contract: the API publishes them as `group`, and the run index
# lists both types and lets the caller decide, rather than deciding for it.
RUN_TYPE_SIMULATION = 'simulation'
RUN_TYPE_AUTOTRADER = 'autotrader'

# A sweep's combinations nest under this subfolder of the simulation root. A directory level,
# never a run type — the type of a combination is `simulation`, and its `parent_id` names the
# sweep it belongs to.
SWEEPS_SUBDIR = 'sweeps'

# A sweep loads its data ONCE and reuses it across every combination. That load's record is
# sweep-level output, so it lands beside the sweep's ranked.csv rather than in a directory
# shaped like a run — the run index counts runs, and a shared data load is not one.
MOUNT_BUILD_LOG = 'mount_build.log'

# A sweep's ranking, written by the report command beside the mount build's log — an export of
# the ledger's figures, never their source.
SWEEP_RANKING_FILE = 'ranked.csv'

# The subfolder a run's report artifacts live in, inside its run directory. Part of the layout
# contract like the names above: the store resolves artifacts through it, and the run index needs
# it without importing the store.
IO_SUBDIR = 'io'

# Every other name a run directory holds is declared below, once (#621): the writers build their
# paths from these, and the store's declaration of what a run may hold reads the same constants,
# so the two cannot drift apart. This module imports nothing on purpose — both sides import it,
# the run stream appender and the logger among them, and a heavier module here would leave a
# writer unable to import the declaration without an import cycle.

# What the run IS — kind, windows, configuration and code identity — written at its start.
RUN_HEADER_ARTIFACT = 'header.json'

# What the run wrote, and why it left a declared file out — written as its very last step, so a run
# with a footprint is closed and a run without one did not get there (#621).
FOOTPRINT_FILE = 'footprint.json'
FOOTPRINT_SCHEMA_VERSION = 1

# A run's logs are named '<prefix>_<name>.log'. A backtest's prefix comes from configuration
# (file_logging's scenario file-name prefix); an AutoTrader session's is fixed.
RUN_LOG_FILE_PATTERN = '{log_prefix}_{log_name}.log'
BACKTEST_GLOBAL_LOG_NAME = 'global_log'
BACKTEST_SYSTEM_INFO_LOG_NAME = 'system_info'
RUN_SUMMARY_LOG_NAME = 'summary'
# One log per scenario of a backtest, numbered from 01 in the set's order.
SCENARIO_LOGS_SUBDIR = 'scenario_logs'
SCENARIO_LOG_NAME_PATTERN = '{scenario_no}_{scenario}'
SESSION_LOG_PREFIX = 'autotrader'
SESSION_GLOBAL_LOG_NAME = 'global'
# The session's own log: under this name until the first tick, then one file per trading day in
# session_logs/ (#357).
SESSION_LOG_NAME = 'session'
SESSION_LOGS_SUBDIR = 'session_logs'
SESSION_DAY_LOG_FILE_PATTERN = f'{SESSION_LOG_PREFIX}_{SESSION_LOG_NAME}_{{day}}.log'

# The fills and order transitions as one chronological table: a backtest writes one per scenario
# into events/, a session one beside its logs.
EVENTS_SUBDIR = 'events'
SCENARIO_EVENTS_FILE_PATTERN = 'events_{scenario}.csv'
SESSION_EVENTS_FILE = 'events.csv'

# A decision logic's own diagnostics tables, one per sink it declares — per scenario in a backtest.
DIAGNOSTICS_SUBDIR = 'diagnostics'
DIAGNOSTICS_FILE_PATTERN = '{sink}.csv'
SCENARIO_DIAGNOSTICS_FILE_PATTERN = '{sink}_{scenario}.csv'

# The report sections in io/, written at the run's end. The report specs bind each name to the
# model it decodes to.
AGGREGATED_PORTFOLIO_ARTIFACT_FILE = 'aggregated_portfolio.json'
BLOCK_SPLITTING_ARTIFACT_FILE = 'block_splitting.json'
BOOKING_PERIODS_ARTIFACT_FILE = 'booking_periods.json'
BROKER_ARTIFACT_FILE = 'broker.json'
COLD_START_ARTIFACT_FILE = 'cold_start.json'
EXECUTION_STATS_ARTIFACT_FILE = 'execution_stats.json'
FEED_STABILITY_ARTIFACT_FILE = 'feed_stability.json'
ORDER_HISTORY_ARTIFACT_FILE = 'order_history.json'
PENDING_ORDERS_ARTIFACT_FILE = 'pending_orders.json'
PORTFOLIO_ARTIFACT_FILE = 'portfolio.json'
PROFILING_ARTIFACT_FILE = 'profiling.json'
ROBUSTNESS_ARTIFACT_FILE = 'robustness.json'
RUN_META_ARTIFACT_FILE = 'run_meta.json'
RUN_SUMMARY_ARTIFACT_FILE = 'run_summary.json'
SAFETY_ARTIFACT_FILE = 'safety.json'
SCENARIO_DETAILS_ARTIFACT_FILE = 'scenario_details.json'
SIGNAL_ARTIFACT_FILE = 'signal.json'
TRADE_HISTORY_ARTIFACT_FILE = 'trade_history.json'
VENUE_ACCOUNT_ARTIFACT_FILE = 'venue_account.json'
WARNINGS_ERRORS_ARTIFACT_FILE = 'warnings_errors.json'
WORKER_DECISION_ARTIFACT_FILE = 'worker_decision.json'
# Three of the sections again as CSV, for a spreadsheet.
EXECUTION_STATS_CSV = 'execution_stats.csv'
ORDER_HISTORY_CSV = 'order_history.csv'
TRADE_HISTORY_CSV = 'trade_history.csv'

# The run streams in io/, written line by line and served while the run goes on: each one's file,
# the name its header line gives, and the version of its line shapes, both written into that header.
# The order events (#362) — a backtest writes its stream at the end, a session as it runs.
ORDER_EVENTS_FILE = 'order_events.jsonl'
ORDER_EVENTS_STREAM = 'order_events'
ORDER_EVENTS_SCHEMA_VERSION = 2
# What the session held and decided, moment by moment (#579 part A, served since #611).
STATE_FRAMES_FILE = 'state_frames.jsonl'
STATE_FRAMES_STREAM = 'state_frames'
STATE_FRAMES_SCHEMA_VERSION = 1

# The records a live session writes into its io/ folder itself, as it runs — neither a report nor
# a served stream. Named here because the writer and the run index both need the name, and the
# run index must not count them as report artifacts.
# The warmup history the session started from (#588).
WARMUP_HISTORY_FILE = 'warmup_history.json'
# The tick stream's own outages and refused trades (#609).
TICK_STREAM_EVENTS_FILE = 'tick_stream_events.jsonl'
TICK_STREAM_EVENTS_STREAM = 'tick_stream_events'
TICK_STREAM_EVENTS_SCHEMA_VERSION = 1

# The acceptance studies' captures (#332, #358) — streams too, but at the run directory's root
# rather than in io/, and read by their certificates rather than served.
FIELD_STUDY_CAPTURE = 'field_study.jsonl'
SECURITY_CERT_CAPTURE = 'security_cert.jsonl'
# 2.0 — order and venue lines are projected from the live core's record (#566): they name the
# phase that submitted the order, a full close is a line of its own, a refusal made before sending
# is one too, and a fill carries what its slippage is measured against.
ACCEPTANCE_CAPTURE_SCHEMA_VERSION = '2.0'

# The framework-wide log file, inside file_logging.global_log_dir. Named here for the same reason
# as the rest: the logger writes it and the store catalog registers it, and two literals for one
# file is how they drift apart.
GLOBAL_LOG_FILE = 'global.log'

# The subfolder of file_logging.global_log_dir that holds one log file per data import. The
# import coordinator writes it and the store catalog registers it.
IMPORT_LOG_SUBDIR = 'imports'
