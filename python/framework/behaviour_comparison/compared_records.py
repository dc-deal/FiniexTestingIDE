"""
Which records of a backtest's run directory the behaviour comparison holds against each other
(#641), and why every other one is left out — one answer for every entry member a backtest
writes, held complete in both directions against the entry-member declaration by a test.

A record is compared where it says what the strategy and the framework DID with the data: the
orders, their events and fills, the trades, the positions and the account, the booking periods,
what the run logged, what it read. It is left out where it says how fast this machine was, where
it is the run's provenance, a log, or a CSV copy of a compared document.

The ignored fields are measured, not guessed (2026-11-27): one stand run twice on two fixture sets
wrote every compared record identically except `run_id`. The other fields named below are stamps
a writer takes from the wall clock or a fingerprint over a stamped document, which the measured
sets did not reach. A comparison never decides on a stamp of when something was written.
"""

from typing import Dict, Tuple

from python.framework.types.behaviour_comparison_types import ComparedRecord
from python.framework.types.store_member_types import RunEntryMemberId

# The field every record names its own run by — the one value two runs of one stand always differ in
IGNORED_IN_EVERY_RECORD = ('run_id',)

# A field whose difference is a change of the record's shape, not of a value
SHAPE_FIELDS = ('schema_version',)

COMPARED_RECORDS: Tuple[ComparedRecord, ...] = (
    ComparedRecord(RunEntryMemberId.ORDER_EVENTS, 'order events', records_key='',
                   unseen_words='an order placed, filled or cancelled'),
    ComparedRecord(RunEntryMemberId.ORDER_HISTORY, 'orders', records_key='orders',
                   unseen_words='an order'),
    ComparedRecord(RunEntryMemberId.TRADE_HISTORY, 'trades', records_key='trades',
                   unseen_words='a closed position'),
    ComparedRecord(RunEntryMemberId.PENDING_ORDERS, 'resting orders'),
    ComparedRecord(RunEntryMemberId.PORTFOLIO, 'positions and the account'),
    ComparedRecord(RunEntryMemberId.AGGREGATED_PORTFOLIO, 'the account per currency'),
    ComparedRecord(RunEntryMemberId.BOOKING_PERIODS, 'booking periods', records_key='periods',
                   unseen_words='a trading day'),
    ComparedRecord(RunEntryMemberId.EXECUTION_STATS, 'fills and slippage'),
    ComparedRecord(RunEntryMemberId.RUN_SUMMARY, 'the run\'s totals'),
    # `observed_at` is when a warning or an error was logged, read from the wall clock;
    # `event_time` is when it happened in the run, and stays compared
    ComparedRecord(RunEntryMemberId.WARNINGS_ERRORS, 'warnings and errors',
                   ignored_fields=('observed_at',)),
    ComparedRecord(RunEntryMemberId.SIGNAL, 'signal data', records_key='units',
                   unseen_words='a signal'),
    ComparedRecord(RunEntryMemberId.FEED_STABILITY, 'feed disturbances', records_key='units',
                   unseen_words='a disturbed feed'),
    # `broker_config_id` fingerprints the frozen broker document, fetch and export stamps
    # included; the broker's content still reaches every fill, which is compared
    ComparedRecord(RunEntryMemberId.BROKER, 'the broker', ignored_fields=('broker_config_id',)),
    ComparedRecord(RunEntryMemberId.SCENARIO_DETAILS, 'the scenarios',
                   ignored_fields=('execution_time_ms',)),
    ComparedRecord(RunEntryMemberId.ROBUSTNESS, 'robustness'),
    # `written_at` is when a frame was written, read from the wall clock; `frame_time` is the
    # instant it describes, and stays compared
    # Both streams are written only where their recording is switched on, so neither one's
    # absence says that nothing happened
    ComparedRecord(RunEntryMemberId.SCENARIO_STATE_FRAMES, 'state frames',
                   ignored_fields=('written_at',)),
    ComparedRecord(RunEntryMemberId.SCENARIO_INDICATOR_SERIES, 'indicator series'),
    ComparedRecord(RunEntryMemberId.SCENARIO_DIAGNOSTICS, 'diagnostics rows'),
    # A session's recorded warmup, as the scenario rendered it to start from: a framework change
    # to warmup or to bar rendering moves it
    ComparedRecord(RunEntryMemberId.SCENARIO_WARMUP_HISTORY, 'warmup history'),
)

NOT_COMPARED_RECORDS: Dict[RunEntryMemberId, str] = {
    RunEntryMemberId.HEADER: 'the run\'s provenance: its id, its start, its configuration\'s name',
    RunEntryMemberId.FOOTPRINT: 'the run\'s provenance: when it closed, how large its files are',
    RunEntryMemberId.BACKTEST_GLOBAL_LOG: 'a log, stamped by the wall clock',
    RunEntryMemberId.BACKTEST_SUMMARY_LOG: 'a log, stamped by the wall clock',
    RunEntryMemberId.BACKTEST_SYSTEM_INFO_LOG: 'a log of the machine',
    RunEntryMemberId.SCENARIO_LOG: 'a log, stamped by the wall clock',
    RunEntryMemberId.SCENARIO_EVENTS: 'a CSV view of the order events, which are compared',
    RunEntryMemberId.SCENARIO_STRATEGY_FILE: 'a file the strategy writes itself, in a form the '
                                             'framework does not know',
    RunEntryMemberId.BLOCK_SPLITTING: 'how a profile run split its work, not what it computed',
    RunEntryMemberId.PROFILING: 'how long each step took on this machine',
    RunEntryMemberId.RUN_META: 'how long the run took and what the machine held',
    RunEntryMemberId.WORKER_DECISION: 'how long the workers and the decision took',
    RunEntryMemberId.EXECUTION_STATS_CSV: 'a CSV copy of the fills and slippage, which are '
                                          'compared',
    RunEntryMemberId.ORDER_HISTORY_CSV: 'a CSV copy of the orders, which are compared',
    RunEntryMemberId.TRADE_HISTORY_CSV: 'a CSV copy of the trades, which are compared',
}
