"""
Report artifact specs (#486) — every artifact a run writes, declared once.

Data, not machinery: a file name bound to the model it decodes to. This is the whole of what
the eighteen former `*_io.py` units carried between them, minus the three that also own a CSV
surface (`report_csv_io.py`) or a row filter (`report_filters.py`). The file names themselves are
declared with every other name a run directory holds, in the run layout constants (#621), so the store's
declaration of a run reads them without importing a model.

The run header is deliberately NOT here. It is the run's identity rather than a report artifact:
it is written at the run's START, lives beside the io/ directory rather than in it, and the run
index reads it without going through the report store.
"""

from typing import FrozenSet

from python.framework.constants.run_layout_constants import (
    AGGREGATED_PORTFOLIO_ARTIFACT_FILE,
    BLOCK_SPLITTING_ARTIFACT_FILE,
    BOOKING_PERIODS_ARTIFACT_FILE,
    BROKER_ARTIFACT_FILE,
    COLD_START_ARTIFACT_FILE,
    EXECUTION_STATS_ARTIFACT_FILE,
    FEED_STABILITY_ARTIFACT_FILE,
    ORDER_EVENTS_FILE,
    ORDER_HISTORY_ARTIFACT_FILE,
    PENDING_ORDERS_ARTIFACT_FILE,
    PORTFOLIO_ARTIFACT_FILE,
    PROFILING_ARTIFACT_FILE,
    ROBUSTNESS_ARTIFACT_FILE,
    RUN_META_ARTIFACT_FILE,
    RUN_SUMMARY_ARTIFACT_FILE,
    SAFETY_ARTIFACT_FILE,
    SCENARIO_DETAILS_ARTIFACT_FILE,
    SIGNAL_ARTIFACT_FILE,
    STATE_FRAMES_FILE,
    TICK_STREAM_EVENTS_FILE,
    TRADE_HISTORY_ARTIFACT_FILE,
    VENUE_ACCOUNT_ARTIFACT_FILE,
    WARMUP_HISTORY_FILE,
    WARNINGS_ERRORS_ARTIFACT_FILE,
    WORKER_DECISION_ARTIFACT_FILE,
)
from python.framework.reporting.io.report_artifact_io import ArtifactSpec
from python.framework.types.api.report_types import (
    AggregatedPortfolioReport,
    BlockSplittingReport,
    BookingPeriodsReport,
    BrokerReport,
    ColdStartReport,
    ExecutionStatsReport,
    FeedStabilityReport,
    OrderHistoryReport,
    PendingOrdersReport,
    PortfolioReport,
    ProfilingReport,
    RobustnessReport,
    RunMetaReport,
    RunSummary,
    SafetyReport,
    ScenarioDetailsReport,
    SignalReport,
    TradeHistoryReport,
    VenueAccountReport,
    WarningsErrorsReport,
    WorkerDecisionReport,
)

AGGREGATED_PORTFOLIO_ARTIFACT: ArtifactSpec[AggregatedPortfolioReport] = ArtifactSpec(
    AGGREGATED_PORTFOLIO_ARTIFACT_FILE, AggregatedPortfolioReport)
BLOCK_SPLITTING_ARTIFACT: ArtifactSpec[BlockSplittingReport] = ArtifactSpec(
    BLOCK_SPLITTING_ARTIFACT_FILE, BlockSplittingReport)
# The run's booking periods (#537). It was the one section that was derived and then thrown away —
# rendered to the console and the summary log, persisted nowhere — so nothing but a human reading
# that log could see it. Its RECONCILIATION is the reason persisting it matters rather than
# re-deriving it later: the check compares the periods against the run's own independently
# derived total, and that second figure exists only while the run does (#539).
BOOKING_PERIODS_ARTIFACT: ArtifactSpec[BookingPeriodsReport] = ArtifactSpec(
    BOOKING_PERIODS_ARTIFACT_FILE, BookingPeriodsReport)
BROKER_ARTIFACT: ArtifactSpec[BrokerReport] = ArtifactSpec(
    BROKER_ARTIFACT_FILE, BrokerReport)
COLD_START_ARTIFACT: ArtifactSpec[ColdStartReport] = ArtifactSpec(
    COLD_START_ARTIFACT_FILE, ColdStartReport)
EXECUTION_STATS_ARTIFACT: ArtifactSpec[ExecutionStatsReport] = ArtifactSpec(
    EXECUTION_STATS_ARTIFACT_FILE, ExecutionStatsReport)
FEED_STABILITY_ARTIFACT: ArtifactSpec[FeedStabilityReport] = ArtifactSpec(
    FEED_STABILITY_ARTIFACT_FILE, FeedStabilityReport)
ORDER_HISTORY_ARTIFACT: ArtifactSpec[OrderHistoryReport] = ArtifactSpec(
    ORDER_HISTORY_ARTIFACT_FILE, OrderHistoryReport)
PENDING_ORDERS_ARTIFACT: ArtifactSpec[PendingOrdersReport] = ArtifactSpec(
    PENDING_ORDERS_ARTIFACT_FILE, PendingOrdersReport)
PORTFOLIO_ARTIFACT: ArtifactSpec[PortfolioReport] = ArtifactSpec(
    PORTFOLIO_ARTIFACT_FILE, PortfolioReport)
PROFILING_ARTIFACT: ArtifactSpec[ProfilingReport] = ArtifactSpec(
    PROFILING_ARTIFACT_FILE, ProfilingReport)
ROBUSTNESS_ARTIFACT: ArtifactSpec[RobustnessReport] = ArtifactSpec(
    ROBUSTNESS_ARTIFACT_FILE, RobustnessReport)
RUN_META_ARTIFACT: ArtifactSpec[RunMetaReport] = ArtifactSpec(
    RUN_META_ARTIFACT_FILE, RunMetaReport)
RUN_SUMMARY_ARTIFACT: ArtifactSpec[RunSummary] = ArtifactSpec(
    RUN_SUMMARY_ARTIFACT_FILE, RunSummary)
SAFETY_ARTIFACT: ArtifactSpec[SafetyReport] = ArtifactSpec(
    SAFETY_ARTIFACT_FILE, SafetyReport)
SCENARIO_DETAILS_ARTIFACT: ArtifactSpec[ScenarioDetailsReport] = ArtifactSpec(
    SCENARIO_DETAILS_ARTIFACT_FILE, ScenarioDetailsReport)
SIGNAL_ARTIFACT: ArtifactSpec[SignalReport] = ArtifactSpec(
    SIGNAL_ARTIFACT_FILE, SignalReport)
TRADE_HISTORY_ARTIFACT: ArtifactSpec[TradeHistoryReport] = ArtifactSpec(
    TRADE_HISTORY_ARTIFACT_FILE, TradeHistoryReport)
# What the venue held at a live session's start and end, and what the reconciliation recorded in
# between (#362) — derived from the broker-truth lines of the order-event stream.
VENUE_ACCOUNT_ARTIFACT: ArtifactSpec[VenueAccountReport] = ArtifactSpec(
    VENUE_ACCOUNT_ARTIFACT_FILE, VenueAccountReport)
WARNINGS_ERRORS_ARTIFACT: ArtifactSpec[WarningsErrorsReport] = ArtifactSpec(
    WARNINGS_ERRORS_ARTIFACT_FILE, WarningsErrorsReport)
WORKER_DECISION_ARTIFACT: ArtifactSpec[WorkerDecisionReport] = ArtifactSpec(
    WORKER_DECISION_ARTIFACT_FILE, WorkerDecisionReport)


# Run STREAMS (#362) — written line by line while a run is in progress, and served while it is.
# Not ArtifactSpecs: a spec decodes one JSON document, a stream is many. Kept apart from the report
# artifacts for a reason beyond format: a live session writes its order events from its first order
# and its state frames (#579 part A, served since #611) from its first pass, so a session that died
# before its report has them — counted as artifacts, it would read as reported.
STREAM_FILENAMES: FrozenSet[str] = frozenset({ORDER_EVENTS_FILE, STATE_FRAMES_FILE})

# Records a live session writes into io/ itself, as it runs — the warmup history it started from
# (#588) and its tick stream's outages (#609). Neither a report artifact nor a served stream:
# counted as an artifact, a session that died before its report would read as reported, and listed
# among the streams it would promise a route that does not exist.
RUN_RECORD_FILENAMES: FrozenSet[str] = frozenset({WARMUP_HISTORY_FILE, TICK_STREAM_EVENTS_FILE})
