"""
Report store (#391) — resolves persisted run-report artifacts under the logs tree.

The API's read-only source: given a run id, find the run's trade-history artifact
(written by either pipeline into its run directory), read it, and apply the shared
filter. Run directories follow `<logs_root>/<group>/<set-or-profile>/<run_id>/`.
"""

from datetime import datetime
from pathlib import Path
from typing import List, Optional

from python.framework.reporting.run_reports.execution_stats_report_io import (
    EXECUTION_STATS_ARTIFACT, read_execution_stats_report)
from python.framework.reporting.run_reports.order_history_report_io import (
    ORDER_HISTORY_ARTIFACT, filter_order_history_report, read_order_history_report)
from python.framework.reporting.run_reports.pending_orders_report_io import (
    PENDING_ORDERS_ARTIFACT, read_pending_orders_report)
from python.framework.reporting.run_reports.portfolio_report_io import (
    PORTFOLIO_ARTIFACT, read_portfolio_report)
from python.framework.reporting.run_reports.trade_history_report_io import (
    TRADE_HISTORY_ARTIFACT, filter_trade_history_report, read_trade_history_report)
from python.framework.types.api.report_types import (
    ExecutionStatsReport, OrderHistoryReport, PendingOrdersReport, PortfolioReport,
    TradeHistoryReport)


class ReportStore:
    """Locates + serves persisted run-report artifacts (sim + autotrader runs)."""

    # run dirs live at: <logs_root>/<group>/<set-or-profile>/<run_id>/<artifact>
    _GROUPS = ('scenario_sets', 'autotrader')

    def __init__(self, logs_root: Path = Path('logs')):
        self._logs_root = Path(logs_root)

    def list_runs(self) -> List[str]:
        """Run ids (run-timestamp dirs) carrying a trade-history artifact, newest first."""
        run_ids = set()
        for group in self._GROUPS:
            for artifact in (self._logs_root / group).glob(f'*/*/{TRADE_HISTORY_ARTIFACT}'):
                run_ids.add(artifact.parent.name)
        return sorted(run_ids, reverse=True)

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

    def _resolve(self, run_id: str, artifact: str) -> Optional[Path]:
        """Find a named report artifact for a run id across the log groups."""
        for group in self._GROUPS:
            for found in (self._logs_root / group).glob(f'*/{run_id}/{artifact}'):
                return found
        return None
