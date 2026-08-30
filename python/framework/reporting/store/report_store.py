"""
Report store (#391) — resolves persisted run-report artifacts under the logs tree.

The API's read-only source: given a run id, find the run's trade-history artifact
(written by either pipeline into its run directory), read it, and apply the shared
filter. Run directories follow `<logs_root>/<set-or-profile>/<run_id>/`, and the
report artifacts live in the run's `io/` subfolder (`IO_SUBDIR`). A run is located through
the run index, never by walking the tree — a directory means nothing to this class (#475).
"""

from datetime import datetime
from pathlib import Path
from typing import List, Optional

from pydantic import ValidationError

from python.configuration.app_config_manager import AppConfigManager
from python.framework.exceptions.report_artifact_errors import ReportArtifactUnreadableError
from python.framework.reporting.io.aggregated_portfolio_report_io import (
    AGGREGATED_PORTFOLIO_ARTIFACT,
    read_aggregated_portfolio_report,
)
from python.framework.reporting.io.broker_report_io import BROKER_ARTIFACT, read_broker_report
from python.framework.reporting.io.execution_stats_report_io import (
    EXECUTION_STATS_ARTIFACT,
    read_execution_stats_report,
)
from python.framework.reporting.io.feed_stability_report_io import (
    FEED_STABILITY_ARTIFACT,
    read_feed_stability_report,
)
from python.framework.reporting.io.order_history_report_io import (
    ORDER_HISTORY_ARTIFACT,
    filter_order_history_report,
    read_order_history_report,
)
from python.framework.reporting.io.pending_orders_report_io import (
    PENDING_ORDERS_ARTIFACT,
    read_pending_orders_report,
)
from python.framework.reporting.io.portfolio_report_io import (
    PORTFOLIO_ARTIFACT,
    read_portfolio_report,
)
from python.framework.reporting.io.profiling_report_io import (
    PROFILING_ARTIFACT,
    read_profiling_report,
)
from python.framework.reporting.io.run_summary_io import RUN_SUMMARY_ARTIFACT, read_run_summary
from python.framework.reporting.io.scenario_details_report_io import (
    SCENARIO_DETAILS_ARTIFACT,
    read_scenario_details_report,
)
from python.framework.reporting.io.signal_report_io import SIGNAL_ARTIFACT, read_signal_report
from python.framework.reporting.io.trade_history_report_io import (
    TRADE_HISTORY_ARTIFACT,
    filter_trade_history_report,
    read_trade_history_report,
)
from python.framework.reporting.io.warnings_errors_report_io import (
    WARNINGS_ERRORS_ARTIFACT,
    read_warnings_errors_report,
)
from python.framework.reporting.io.worker_decision_report_io import (
    WORKER_DECISION_ARTIFACT,
    read_worker_decision_report,
)
from python.framework.reporting.store.run_index import RunIndex
from python.framework.types.api.report_types import (
    AggregatedPortfolioReport,
    BrokerReport,
    ExecutionStatsReport,
    FeedStabilityReport,
    OrderHistoryReport,
    PendingOrdersReport,
    PortfolioReport,
    ProfilingReport,
    RunInfo,
    RunSummary,
    ScenarioDetailsReport,
    SignalReport,
    TradeHistoryReport,
    WarningsErrorsReport,
    WorkerDecisionReport,
)
from python.framework.types.log_layout_types import IO_SUBDIR


class ReportStore:
    """Locates + serves persisted run-report artifacts (simulation + live runs)."""

    def __init__(self, run_index_path: Optional[Path] = None):
        """
        Args:
            run_index_path: The run index to read; from config when not given. Injectable so a
                caller pointed at an isolated tree can be pointed at that tree's index too,
                rather than asking the real one about runs that only exist in tmp
        """
        self._index = RunIndex(
            run_index_path or AppConfigManager().get_file_logging_config_object().run_index)

    def list_runs(self) -> List[RunInfo]:
        """Every indexed run, both types, newest first.

        Not only runs carrying artifacts: `artifacts` says which do, and a caller that wants the
        narrower set filters on it. An index that silently omitted a type would be its own
        surprise.

        Returns:
            One identity row per run — id, run type, owning set / profile, artifacts
        """
        return self._index.list_runs()

    def get_trade_history(
        self,
        run_id: str,
        symbol: Optional[str] = None,
        close_reason: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> Optional[TradeHistoryReport]:
        """
        Read + filter a run's trade-history report.

        Args:
            run_id: The run-timestamp directory name
            symbol / close_reason / start / end: Filters (see filter_trade_history_report)

        Returns:
            The filtered report, or None if the run has no trade-history artifact
        """
        path = self._resolve(run_id, TRADE_HISTORY_ARTIFACT)
        if path is None:
            return None
        report = read_trade_history_report(path)
        return filter_trade_history_report(report, symbol, close_reason, start, end)

    def get_order_history(
        self,
        run_id: str,
        symbol: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Optional[OrderHistoryReport]:
        """
        Read + filter a run's order-history report.

        Args:
            run_id: The run-timestamp directory name
            symbol / status: Filters (see filter_order_history_report)

        Returns:
            The filtered report, or None if the run has no order-history artifact
        """
        path = self._resolve(run_id, ORDER_HISTORY_ARTIFACT)
        if path is None:
            return None
        report = read_order_history_report(path)
        return filter_order_history_report(report, symbol, status)

    def get_portfolio(self, run_id: str) -> Optional[PortfolioReport]:
        """
        Read a run's portfolio report.

        Args:
            run_id: The run-timestamp directory name

        Returns:
            The portfolio report, or None if the run has no portfolio artifact
        """
        path = self._resolve(run_id, PORTFOLIO_ARTIFACT)
        if path is None:
            return None
        return read_portfolio_report(path)

    def get_execution_stats(self, run_id: str) -> Optional[ExecutionStatsReport]:
        """
        Read a run's execution-stats report.

        Args:
            run_id: The run-timestamp directory name

        Returns:
            The execution-stats report, or None if the run has no execution-stats artifact
        """
        path = self._resolve(run_id, EXECUTION_STATS_ARTIFACT)
        if path is None:
            return None
        return read_execution_stats_report(path)

    def get_pending_orders(self, run_id: str) -> Optional[PendingOrdersReport]:
        """
        Read a run's pending-orders report.

        Args:
            run_id: The run-timestamp directory name

        Returns:
            The pending-orders report, or None if the run has no pending-orders artifact
        """
        path = self._resolve(run_id, PENDING_ORDERS_ARTIFACT)
        if path is None:
            return None
        return read_pending_orders_report(path)

    def get_scenario_details(self, run_id: str) -> Optional[ScenarioDetailsReport]:
        """
        Read a run's scenario-details report (sim-only).

        Args:
            run_id: The run-timestamp directory name

        Returns:
            The scenario-details report, or None if the run has no scenario-details artifact
        """
        path = self._resolve(run_id, SCENARIO_DETAILS_ARTIFACT)
        if path is None:
            return None
        return read_scenario_details_report(path)

    def get_run_summary(self, run_id: str) -> Optional[RunSummary]:
        """
        Read a run's cross-section KPI summary.

        Args:
            run_id: The run-timestamp directory name

        Returns:
            The run-summary report, or None if the run has no run-summary artifact
        """
        path = self._resolve(run_id, RUN_SUMMARY_ARTIFACT)
        if path is None:
            return None
        return read_run_summary(path)

    def get_worker_decision(self, run_id: str) -> Optional[WorkerDecisionReport]:
        """
        Read a run's worker/decision report.

        Args:
            run_id: The run-timestamp directory name

        Returns:
            The worker/decision report, or None if the run has no worker-decision artifact
        """
        path = self._resolve(run_id, WORKER_DECISION_ARTIFACT)
        if path is None:
            return None
        return read_worker_decision_report(path)

    def get_profiling(self, run_id: str) -> Optional[ProfilingReport]:
        """
        Read a run's profiling report (sim-only).

        Args:
            run_id: The run-timestamp directory name

        Returns:
            The profiling report, or None if the run has no profiling artifact
        """
        path = self._resolve(run_id, PROFILING_ARTIFACT)
        if path is None:
            return None
        return read_profiling_report(path)

    def get_aggregated_portfolio(self, run_id: str) -> Optional[AggregatedPortfolioReport]:
        """
        Read a run's aggregated per-currency portfolio report (sim).

        Args:
            run_id: The run-timestamp directory name

        Returns:
            The aggregated-portfolio report, or None if the run has no artifact
        """
        path = self._resolve(run_id, AGGREGATED_PORTFOLIO_ARTIFACT)
        if path is None:
            return None
        return read_aggregated_portfolio_report(path)

    def get_warnings_errors(self, run_id: str) -> Optional[WarningsErrorsReport]:
        """
        Read a run's warnings & errors report.

        Args:
            run_id: The run-timestamp directory name

        Returns:
            The warnings/errors report, or None if the run has no artifact
        """
        path = self._resolve(run_id, WARNINGS_ERRORS_ARTIFACT)
        if path is None:
            return None
        try:
            return read_warnings_errors_report(path)
        except ValidationError as e:
            raise ReportArtifactUnreadableError(
                WARNINGS_ERRORS_ARTIFACT, str(path), str(e)) from e

    def get_broker(self, run_id: str) -> Optional[BrokerReport]:
        """
        Read a run's broker-configuration report (sim-only).

        Args:
            run_id: The run-timestamp directory name

        Returns:
            The broker report, or None if the run has no broker artifact
        """
        path = self._resolve(run_id, BROKER_ARTIFACT)
        if path is None:
            return None
        return read_broker_report(path)

    def get_signal(self, run_id: str) -> Optional[SignalReport]:
        """
        Read a run's signal-configuration report (#433).

        Args:
            run_id: The run-timestamp directory name

        Returns:
            The signal report, or None if the run has no signal artifact
        """
        path = self._resolve(run_id, SIGNAL_ARTIFACT)
        if path is None:
            return None
        return read_signal_report(path)

    def get_feed_stability(self, run_id: str) -> Optional[FeedStabilityReport]:
        """
        Read a run's feed-stability report (#451).

        Args:
            run_id: The run-timestamp directory name

        Returns:
            The feed-stability report, or None if the run has no artifact
        """
        path = self._resolve(run_id, FEED_STABILITY_ARTIFACT)
        if path is None:
            return None
        return read_feed_stability_report(path)

    def _resolve(self, run_id: str, artifact: str) -> Optional[Path]:
        """
        Find a named report artifact through the run index.

        The lookup is an EXACT match against the index, and that is the guard. The previous
        implementation interpolated the id — which arrives from a URL — into a glob pattern,
        where `'*'` is a valid-looking id that matches the first run in the tree. Membership in
        a table of known ids is strictly stronger than a shape check: a shape accepts anything
        well-formed, including ids that do not exist.

        The index also replaces the depth-dependent search this used to need: a sweep's
        combination sat one level deeper than a standalone run, so the lookup had to know the
        shape of the tree. It now looks up a row.

        Args:
            run_id: The run's identity
            artifact: The artifact's file name

        Returns:
            The artifact path, or None when the run is unknown or carries no such artifact
        """
        run_dir = self._index.run_dir(run_id)
        if run_dir is None:
            return None
        path = run_dir / IO_SUBDIR / artifact
        return path if path.exists() else None
