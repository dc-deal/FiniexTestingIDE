"""
Run console renderer (#403 Phase 2) — the one ordered end-of-run console both pipelines share.

The canonical section order lives here ONCE; each coordinator feeds the sub-presenters it has.
A slot left `None` is skipped (render-if-present) — so the live session omits the sim-only
sections (run-meta header, scenario details, profiling, worker-decision breakdown, block-splitting)
automatically. The per-currency AGGREGATE blocks render only for a multi-unit run
(`unit_count > 1`): they are redundant for a single-scenario sim run and for the always-single live
session. The closing block is pipeline-specific (sim → Executive Summary, live → Session Summary).

The sub-presenters print to stdout via the ConsoleRenderer; the capture/strip/file-log of that output
stays with each coordinator (sim's two-pass detail vs the live single pass).
"""

from typing import Optional, Protocol

from python.framework.reporting.console.block_splitting_disposition import BlockSplittingDisposition
from python.framework.reporting.console.broker_summary import BrokerSummary
from python.framework.reporting.console.execution_header_summary import ExecutionHeaderSummary
from python.framework.reporting.console.performance_summary import PerformanceSummary
from python.framework.reporting.console.portfolio_summary import PortfolioSummary
from python.framework.reporting.console.profiling_summary import ProfilingSummary
from python.framework.reporting.console.scenario_details_summary import ScenarioDetailsSummary
from python.framework.reporting.console.trade_history_summary import TradeHistorySummary
from python.framework.reporting.console.warnings_summary import WarningsSummary
from python.framework.reporting.console.worker_decision_breakdown_summary import WorkerDecisionBreakdownSummary
from python.framework.utils.console_renderer import ConsoleRenderer


class ClosingBlock(Protocol):
    """The pipeline-specific closing section (sim: SimExecutiveSummary, live: LiveSessionSummary)."""

    def render(self, renderer: ConsoleRenderer) -> None: ...


class RunConsoleRenderer:
    """The shared ordered end-of-run console renderer (#403 Phase 2)."""

    def __init__(
        self,
        *,
        unit_count: int,
        threshold: int,
        header_summary: Optional[ExecutionHeaderSummary] = None,
        scenario_details_summary: Optional[ScenarioDetailsSummary] = None,
        portfolio_summary: Optional[PortfolioSummary] = None,
        trade_history_summary: Optional[TradeHistorySummary] = None,
        broker_summary: Optional[BrokerSummary] = None,
        performance_summary: Optional[PerformanceSummary] = None,
        profiling_summary: Optional[ProfilingSummary] = None,
        worker_decision_breakdown: Optional[WorkerDecisionBreakdownSummary] = None,
        warnings_summary: Optional[WarningsSummary] = None,
        block_splitting_disposition: Optional[BlockSplittingDisposition] = None,
        closing_block: Optional[ClosingBlock] = None,
    ):
        """
        Args:
            unit_count: Number of run units (sim: N scenarios; live: 1) — gates the AGGREGATE blocks
            threshold: Scenario-detail threshold (console logging config)
            *_summary / *_disposition: the section sub-presenters; a `None` slot is skipped
            closing_block: the pipeline-specific closing section rendered last
        """
        self._unit_count = unit_count
        self._threshold = threshold
        self._header_summary = header_summary
        self._scenario_details_summary = scenario_details_summary
        self._portfolio_summary = portfolio_summary
        self._trade_history_summary = trade_history_summary
        self._broker_summary = broker_summary
        self._performance_summary = performance_summary
        self._profiling_summary = profiling_summary
        self._worker_decision_breakdown = worker_decision_breakdown
        self._warnings_summary = warnings_summary
        self._block_splitting_disposition = block_splitting_disposition
        self._closing_block = closing_block

    def render_all(self, renderer: ConsoleRenderer, summary_detail: bool) -> None:
        """
        Render every present section in the canonical order, then the closing block.

        Args:
            renderer: ConsoleRenderer instance
            summary_detail: True → also render the per-scenario detail blocks
        """
        # Cross-unit sections (per-currency aggregates + the "worst across scenarios" bottleneck
        # analysis) are redundant for a single unit (1-scenario sim run / the live session).
        is_multi_unit = self._unit_count > 1
        compact = not summary_detail

        # Header + basic execution stats (model-fed sub-presenter)
        if self._header_summary:
            self._header_summary.render(renderer)

        # Scenario details — linear, from the model (#393)
        if self._scenario_details_summary:
            self._scenario_details_summary.render(
                renderer, scenario_detail_threshold=self._threshold)

        # Portfolio summaries
        if self._portfolio_summary:
            if summary_detail:
                self._portfolio_summary.render_per_scenario(renderer)
            # Aggregated per-currency view — redundant for a single unit (#397)
            if is_multi_unit:
                self._portfolio_summary.render_aggregated(renderer)

        # Trade History
        if self._trade_history_summary:
            if summary_detail:
                self._trade_history_summary.render_per_scenario(renderer)
            if is_multi_unit:
                self._trade_history_summary.render_aggregated(renderer)

        # Broker configuration
        if self._broker_summary:
            self._broker_summary.render(renderer, compact=compact, threshold=self._threshold)

        # Performance summaries (aggregate + bottleneck are cross-unit → multi-unit only)
        if self._performance_summary:
            if summary_detail:
                self._performance_summary.render_per_scenario(renderer)
            if is_multi_unit:
                self._performance_summary.render_aggregated(renderer)
                self._performance_summary.render_bottleneck_analysis(renderer)

        # Profiling Analysis (aggregate + bottleneck are cross-unit → multi-unit only)
        if self._profiling_summary:
            if summary_detail:
                self._profiling_summary.render_per_scenario(renderer)
            if is_multi_unit:
                self._profiling_summary.render_aggregated(
                    renderer, compact=compact, threshold=self._threshold)
                self._profiling_summary.render_bottleneck_analysis(renderer)

        # Worker Decision Breakdown — the overhead/bottleneck "too high?" verdicts moved to the
        # post-run validator (#395); the breakdown now shows the calculated split only.
        if self._worker_decision_breakdown:
            if summary_detail:
                self._worker_decision_breakdown.render_per_scenario(renderer)
            if is_multi_unit:
                self._worker_decision_breakdown.render_aggregated()

        # Warmup phase breakdown (summary_detail only) — from the profiling model (#399)
        if self._profiling_summary and summary_detail:
            self._profiling_summary.render_warmup(renderer)

        # Warnings & Notices (always rendered, before the closing block)
        if self._warnings_summary:
            self._warnings_summary.render(renderer)

        # Block Splitting Disposition (Profile Runs only — the model is empty otherwise,
        # so render() no-ops; rendered from the model, #391)
        if self._block_splitting_disposition:
            self._block_splitting_disposition.render(renderer)

        # Closing block (sim → Executive Summary, live → Session Summary)
        if self._closing_block:
            self._closing_block.render(renderer)

        # Footer
        renderer.print_separator(width=120)
