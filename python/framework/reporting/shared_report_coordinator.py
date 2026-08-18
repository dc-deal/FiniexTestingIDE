"""
Shared report coordinator (#403) — the units-derived DERIVE+PERSIST core both pipelines share.

BatchReportCoordinator (sim) and AutotraderReportCoordinator (live) used to repeat the same
build+write sequence for the units-derived report sections (trade / order / portfolio /
pending / execution-stats / run-summary / worker-decision / signal / feed-stability). This unit owns that sequence once;
each pipeline delegates to it and keeps only its pipeline-specific sections + console + ledger.
Stateless by design (composition, not a base class) — see the pipeline coordinators for the flow.
"""

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from python.framework.reporting.builders.execution_stats_report_builder import build_execution_stats_report
from python.framework.reporting.builders.feed_stability_report_builder import build_feed_stability_report
from python.framework.reporting.builders.order_history_report_builder import build_order_history_report
from python.framework.reporting.builders.pending_orders_report_builder import build_pending_orders_report
from python.framework.reporting.builders.portfolio_report_builder import build_portfolio_report
from python.framework.reporting.builders.run_summary_builder import build_run_summary
from python.framework.reporting.builders.run_unit import RunUnit
from python.framework.reporting.builders.signal_report_builder import build_signal_report
from python.framework.reporting.builders.trade_history_report_builder import build_trade_history_report
from python.framework.reporting.builders.unified_reports import UnifiedReports
from python.framework.reporting.builders.worker_decision_report_builder import build_worker_decision_report
from python.framework.reporting.io.execution_stats_report_io import (
    write_execution_stats_csv, write_execution_stats_report)
from python.framework.reporting.io.feed_stability_report_io import write_feed_stability_report
from python.framework.reporting.io.order_history_report_io import (
    write_order_history_csv, write_order_history_report)
from python.framework.reporting.io.pending_orders_report_io import write_pending_orders_report
from python.framework.reporting.io.portfolio_report_io import write_portfolio_report
from python.framework.reporting.io.run_summary_io import write_run_summary
from python.framework.reporting.io.signal_report_io import write_signal_report
from python.framework.reporting.io.trade_history_report_io import (
    write_trade_history_csv, write_trade_history_report)
from python.framework.reporting.io.worker_decision_report_io import write_worker_decision_report
from python.framework.types.scenario_types.scenario_set_types import SignalScenarioInfo


class SharedReportCoordinator:
    """The shared units-derived DERIVE+PERSIST core (#403). Stateless — both pipelines delegate."""

    @staticmethod
    def derive_and_persist(
        units: List[RunUnit],
        io_dir: Path,
        signal_scenario_map: Optional[Dict[Tuple[str, str], SignalScenarioInfo]] = None,
    ) -> UnifiedReports:
        """
        Build + persist the units-derived report sections shared by both pipelines.

        Args:
            units: The run's units (sim: N scenarios; live: 1 session)
            io_dir: The run's io/ subfolder (created if missing)
            signal_scenario_map: The prepared signal sources (#433); both pipelines get it
                from the same MountPreparer run. Empty / None = no SIGNAL source bound

        Returns:
            The built models, for the caller's console + ledger reuse
        """
        io_dir.mkdir(parents=True, exist_ok=True)

        trade_history = build_trade_history_report(units)
        write_trade_history_report(trade_history, io_dir)
        write_trade_history_csv(trade_history, io_dir)

        order_history = build_order_history_report(units)
        write_order_history_report(order_history, io_dir)
        write_order_history_csv(order_history, io_dir)

        # Portfolio full projection — per-unit rows + per-currency roll-up.
        portfolio = build_portfolio_report(units)
        write_portfolio_report(portfolio, io_dir)

        # Pending-orders — per-unit lifecycle + latency + active orders.
        pending_orders = build_pending_orders_report(units)
        write_pending_orders_report(pending_orders, io_dir)

        # Execution-stats headline — per-unit order counts + summed total.
        execution_stats = build_execution_stats_report(units)
        write_execution_stats_report(execution_stats, io_dir)
        write_execution_stats_csv(execution_stats, io_dir)

        # Signal configuration — archive provenance + what the strategy decided on (#433).
        # Built BEFORE the run summary: it supplies the run's weakest fresh ratio.
        signal = build_signal_report(signal_scenario_map or {}, units)
        write_signal_report(signal, io_dir)

        # Feed stability — the observed outage episodes of both staleness domains (#451).
        # Also before the run summary: it supplies the run's disturbance totals.
        feed_stability = build_feed_stability_report(units)
        write_feed_stability_report(feed_stability, io_dir)

        # Run summary — cross-section KPIs composed from the section aggregates (#390 prework).
        run_summary = build_run_summary(
            portfolio, trade_history, execution_stats, signal, feed_stability)
        write_run_summary(run_summary, io_dir)

        # Worker/decision — per-unit worker + decision performance (#398).
        worker_decision = build_worker_decision_report(units)
        write_worker_decision_report(worker_decision, io_dir)

        return UnifiedReports(
            trade_history=trade_history,
            order_history=order_history,
            portfolio=portfolio,
            pending_orders=pending_orders,
            execution_stats=execution_stats,
            run_summary=run_summary,
            worker_decision=worker_decision,
            signal=signal,
            feed_stability=feed_stability,
        )
