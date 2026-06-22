"""
Unified reports (#403) — the 7 units-derived report models both pipelines share.

The DTO that SharedReportCoordinator.derive_and_persist returns: the report sections that
are identical across sim + live (built + written by the shared core). Each pipeline reuses
these for its own console + ledger instead of re-building them.
"""

from dataclasses import dataclass

from python.framework.types.api.report_types import (
    ExecutionStatsReport, OrderHistoryReport, PendingOrdersReport, PortfolioReport,
    RunSummary, TradeHistoryReport, WorkerDecisionReport)


@dataclass
class UnifiedReports:
    """The 7 units-derived report models shared by both pipelines (#403)."""
    trade_history: TradeHistoryReport
    order_history: OrderHistoryReport
    portfolio: PortfolioReport
    pending_orders: PendingOrdersReport
    execution_stats: ExecutionStatsReport
    run_summary: RunSummary
    worker_decision: WorkerDecisionReport
