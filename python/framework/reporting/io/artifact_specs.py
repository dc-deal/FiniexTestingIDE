"""
Report artifact specs (#486) — every artifact a run writes, declared once.

Data, not machinery: a file name bound to the model it decodes to. This is the whole of what
the eighteen former `*_io.py` units carried between them, minus the three that also own a CSV
surface (`report_csv_io.py`) or a row filter (`report_filters.py`).

The run header is deliberately NOT here. It is the run's identity rather than a report artifact:
it is written at the run's START, lives beside the io/ directory rather than in it, and the run
index reads it without going through the report store.
"""

from python.framework.reporting.io.report_artifact_io import ArtifactSpec
from python.framework.types.api.report_types import (
    AggregatedPortfolioReport,
    BlockSplittingReport,
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
    ScenarioDetailsReport,
    SignalReport,
    TradeHistoryReport,
    WarningsErrorsReport,
    WorkerDecisionReport,
)

AGGREGATED_PORTFOLIO_ARTIFACT: ArtifactSpec[AggregatedPortfolioReport] = ArtifactSpec(
    'aggregated_portfolio.json', AggregatedPortfolioReport)
BLOCK_SPLITTING_ARTIFACT: ArtifactSpec[BlockSplittingReport] = ArtifactSpec(
    'block_splitting.json', BlockSplittingReport)
BROKER_ARTIFACT: ArtifactSpec[BrokerReport] = ArtifactSpec(
    'broker.json', BrokerReport)
COLD_START_ARTIFACT: ArtifactSpec[ColdStartReport] = ArtifactSpec(
    'cold_start.json', ColdStartReport)
EXECUTION_STATS_ARTIFACT: ArtifactSpec[ExecutionStatsReport] = ArtifactSpec(
    'execution_stats.json', ExecutionStatsReport)
FEED_STABILITY_ARTIFACT: ArtifactSpec[FeedStabilityReport] = ArtifactSpec(
    'feed_stability.json', FeedStabilityReport)
ORDER_HISTORY_ARTIFACT: ArtifactSpec[OrderHistoryReport] = ArtifactSpec(
    'order_history.json', OrderHistoryReport)
PENDING_ORDERS_ARTIFACT: ArtifactSpec[PendingOrdersReport] = ArtifactSpec(
    'pending_orders.json', PendingOrdersReport)
PORTFOLIO_ARTIFACT: ArtifactSpec[PortfolioReport] = ArtifactSpec(
    'portfolio.json', PortfolioReport)
PROFILING_ARTIFACT: ArtifactSpec[ProfilingReport] = ArtifactSpec(
    'profiling.json', ProfilingReport)
ROBUSTNESS_ARTIFACT: ArtifactSpec[RobustnessReport] = ArtifactSpec(
    'robustness.json', RobustnessReport)
RUN_META_ARTIFACT: ArtifactSpec[RunMetaReport] = ArtifactSpec(
    'run_meta.json', RunMetaReport)
RUN_SUMMARY_ARTIFACT: ArtifactSpec[RunSummary] = ArtifactSpec(
    'run_summary.json', RunSummary)
SCENARIO_DETAILS_ARTIFACT: ArtifactSpec[ScenarioDetailsReport] = ArtifactSpec(
    'scenario_details.json', ScenarioDetailsReport)
SIGNAL_ARTIFACT: ArtifactSpec[SignalReport] = ArtifactSpec(
    'signal.json', SignalReport)
TRADE_HISTORY_ARTIFACT: ArtifactSpec[TradeHistoryReport] = ArtifactSpec(
    'trade_history.json', TradeHistoryReport)
WARNINGS_ERRORS_ARTIFACT: ArtifactSpec[WarningsErrorsReport] = ArtifactSpec(
    'warnings_errors.json', WarningsErrorsReport)
WORKER_DECISION_ARTIFACT: ArtifactSpec[WorkerDecisionReport] = ArtifactSpec(
    'worker_decision.json', WorkerDecisionReport)
