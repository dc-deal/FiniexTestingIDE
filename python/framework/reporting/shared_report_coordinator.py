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

from python.configuration.app_config_manager import AppConfigManager
from python.framework.reporting.builders.execution_stats_report_builder import (
    build_execution_stats_report,
)
from python.framework.reporting.builders.feed_stability_report_builder import (
    build_feed_stability_report,
)
from python.framework.reporting.builders.order_history_report_builder import (
    build_order_history_report,
)
from python.framework.reporting.builders.pending_orders_report_builder import (
    build_pending_orders_report,
)
from python.framework.reporting.builders.portfolio_report_builder import build_portfolio_report
from python.framework.reporting.builders.run_summary_builder import build_run_summary
from python.framework.reporting.builders.run_unit import RunUnit
from python.framework.reporting.builders.signal_report_builder import build_signal_report
from python.framework.reporting.builders.trade_history_report_builder import (
    build_trade_history_report,
)
from python.framework.reporting.builders.unified_reports import UnifiedReports
from python.framework.reporting.builders.worker_decision_report_builder import (
    build_worker_decision_report,
)
from python.framework.reporting.io.artifact_specs import (
    EXECUTION_STATS_ARTIFACT,
    FEED_STABILITY_ARTIFACT,
    ORDER_HISTORY_ARTIFACT,
    PENDING_ORDERS_ARTIFACT,
    PORTFOLIO_ARTIFACT,
    RUN_SUMMARY_ARTIFACT,
    SIGNAL_ARTIFACT,
    TRADE_HISTORY_ARTIFACT,
    WORKER_DECISION_ARTIFACT,
)
from python.framework.reporting.io.report_artifact_io import write_artifact
from python.framework.reporting.io.report_csv_io import (
    write_execution_stats_csv,
    write_order_history_csv,
    write_trade_history_csv,
)
from python.framework.reporting.io.run_header_io import (
    RUN_HEADER_ARTIFACT,
    read_run_header,
)
from python.framework.reporting.store.run_index import RunIndex
from python.framework.types.scenario_types.scenario_set_types import SignalScenarioInfo
from python.framework.types.signal_data_types import SignalObservedSeries


class SharedReportCoordinator:
    """The shared units-derived DERIVE+PERSIST core (#403). Stateless — both pipelines delegate."""

    @staticmethod
    def record_run_artifacts(run_dir: Path) -> None:
        """
        Record the run's persisted artifacts in the index — called LAST, by each pipeline.

        Deliberately not part of `derive_and_persist`: both pipelines write further artifacts of
        their own after that returns, so a list taken there would be short by exactly those. The
        id comes from the run's own header, never from the directory name — identity is a field,
        not a path (#475).

        Args:
            run_dir: The run's own directory
        """
        header_path = run_dir / RUN_HEADER_ARTIFACT
        if not header_path.exists():
            return
        RunIndex(AppConfigManager().get_file_logging_config_object().run_index) \
            .record_artifacts(read_run_header(header_path).run_id, run_dir)

    @staticmethod
    def derive_and_persist(
        run_id: str,
        units: List[RunUnit],
        io_dir: Path,
        signal_scenario_map: Optional[Dict[Tuple[str, str], SignalScenarioInfo]] = None,
        observed_feed: Optional[SignalObservedSeries] = None,
    ) -> UnifiedReports:
        """
        Build + persist the units-derived report sections shared by both pipelines.

        Args:
            run_id: The run these reports belong to — every artifact names it, so a consumer
                can check what it received instead of trusting the route it asked on (#475)
            units: The run's units (sim: N scenarios; live: 1 session)
            io_dir: The run's io/ subfolder (created if missing)
            observed_feed: What a live transport accumulated while the session ran — the
                live counterpart of the prepared map, since a live session has no archive
            signal_scenario_map: The prepared signal sources (#433); both pipelines get it
                from the same MountPreparer run. Empty / None = no SIGNAL source bound

        Returns:
            The built models, for the caller's console + ledger reuse
        """
        io_dir.mkdir(parents=True, exist_ok=True)

        trade_history = build_trade_history_report(run_id, units)
        write_artifact(trade_history, io_dir, TRADE_HISTORY_ARTIFACT)
        write_trade_history_csv(trade_history, io_dir)

        order_history = build_order_history_report(run_id, units)
        write_artifact(order_history, io_dir, ORDER_HISTORY_ARTIFACT)
        write_order_history_csv(order_history, io_dir)

        # Portfolio full projection — per-unit rows + per-currency roll-up.
        portfolio = build_portfolio_report(run_id, units)
        write_artifact(portfolio, io_dir, PORTFOLIO_ARTIFACT)

        # Pending-orders — per-unit lifecycle + latency + active orders.
        pending_orders = build_pending_orders_report(run_id, units)
        write_artifact(pending_orders, io_dir, PENDING_ORDERS_ARTIFACT)

        # Execution-stats headline — per-unit order counts + summed total.
        execution_stats = build_execution_stats_report(run_id, units)
        write_artifact(execution_stats, io_dir, EXECUTION_STATS_ARTIFACT)
        write_execution_stats_csv(execution_stats, io_dir)

        # Signal configuration — archive provenance + what the strategy decided on (#433).
        # Built BEFORE the run summary: it supplies the run's weakest fresh ratio.
        signal = build_signal_report(run_id, signal_scenario_map or {}, units, observed_feed)
        write_artifact(signal, io_dir, SIGNAL_ARTIFACT)

        # Feed stability — the observed outage episodes of both staleness domains (#451).
        # Also before the run summary: it supplies the run's disturbance totals.
        feed_stability = build_feed_stability_report(run_id, units)
        write_artifact(feed_stability, io_dir, FEED_STABILITY_ARTIFACT)

        # Run summary — cross-section KPIs composed from the section aggregates (#390 prework).
        run_summary = build_run_summary(
            run_id, portfolio, trade_history, execution_stats, signal, feed_stability)
        write_artifact(run_summary, io_dir, RUN_SUMMARY_ARTIFACT)

        # Worker/decision — per-unit worker + decision performance (#398).
        worker_decision = build_worker_decision_report(run_id, units)
        write_artifact(worker_decision, io_dir, WORKER_DECISION_ARTIFACT)

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
