"""
FiniexTestingIDE - Batch Summary
Main orchestrator for all batch execution summaries

Architecture:
- BatchSummary orchestrates all sub-summaries
- Delegates to specialized summary classes
- Uses ConsoleRenderer for unified output
"""

from typing import Any, Dict, List, Optional
from python.framework.batch_reporting.block_splitting_disposition import BlockSplittingDisposition
from python.framework.batch_reporting.broker_summary import BrokerSummary
from python.framework.batch_reporting.executive_summary import ExecutiveSummary
from python.framework.batch_reporting.grid.console_box_renderer import ConsoleBoxRenderer
from python.framework.batch_reporting.portfolio_aggregator import PortfolioAggregator
from python.framework.batch_reporting.portfolio_summary import PortfolioSummary
from python.framework.batch_reporting.performance_summary import PerformanceSummary
from python.framework.batch_reporting.profiling_summary import ProfilingSummary
from python.framework.batch_reporting.trade_history_summary import TradeHistorySummary
from python.framework.batch_reporting.warnings_summary import WarningsSummary
from python.framework.batch_reporting.warmup_phase_summary import WarmupPhaseSummary
from python.framework.batch_reporting.worker_decision_breakdown_summary import WorkerDecisionBreakdownSummary
from python.framework.types.rendering_types import BatchStatus
from python.framework.utils.console_renderer import ConsoleRenderer
from python.configuration.app_config_manager import AppConfigManager
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.scenario_types.generator_profile_types import GeneratorProfile
from python.framework.types.scenario_types.scenario_set_performance_types import ProfilingData


class BatchSummary:
    """
    Main summary orchestrator for batch execution results.

    """

    def __init__(
        self,
        batch_execution_summary: BatchExecutionSummary,
        app_config: AppConfigManager,
        generator_profiles: Optional[List[GeneratorProfile]] = None
    ):
        """
        Initialize batch summary.

        Args:
            batch_execution_summary: Batch execution results
            app_config: AppConfigManager instance
            generator_profiles: Generator profiles for Profile Run disposition (None for normal runs)
        """
        self.batch_execution_summary = batch_execution_summary
        self.app_config = app_config
        self._generator_profiles = generator_profiles

        # Initialize sub-summaries
        self.portfolio_summary = PortfolioSummary(batch_execution_summary)
        self.performance_summary = PerformanceSummary(batch_execution_summary)

        # this must happen only onece, due the "pop" mechanic in ProfilingData.from_dicts
        profiling_data_map = self.build_profiling_data_map(
            batch_execution_summary)

        self.profiling_summary = ProfilingSummary(
            batch_execution_summary=batch_execution_summary, profiling_data_map=profiling_data_map)
        self.worker_decision_breakdown = WorkerDecisionBreakdownSummary(
            batch_execution_summary=batch_execution_summary, profiling_data_map=profiling_data_map)

        # Broker summary
        self.broker_summary = BrokerSummary(
            batch_summary=batch_execution_summary,
            app_config=app_config
        )

        # Trade history summary
        self.trade_history_summary = TradeHistorySummary(
            batch_execution_summary)

        # Warnings summary (always rendered)
        self.warnings_summary = WarningsSummary(
            batch_execution_summary, profiling_data_map=profiling_data_map)

        # Warmup phase breakdown (summary_detail only)
        self.warmup_phase_summary = WarmupPhaseSummary(batch_execution_summary)

        # Renderer for unified console output
        self._renderer = ConsoleRenderer()
        self._box_renderer = ConsoleBoxRenderer(self._renderer)

    def _detect_profile_run(self) -> bool:
        """
        Detect if this batch is a Profile Run.

        Returns:
            True if first scenario has is_profile_run=True
        """
        scenarios = self.batch_execution_summary.single_scenario_list
        if scenarios and hasattr(scenarios[0], 'is_profile_run'):
            return scenarios[0].is_profile_run
        return False

    def _calculate_batch_status(self) -> BatchStatus:
        """
        Calculate batch execution status based on process_result results.

        Returns:
            BatchStatus.SUCCESS: All process_results successful
            BatchStatus.PARTIAL: Some process_results failed
            BatchStatus.FAILED: All process_results failed
        """
        process_results = self.batch_execution_summary.process_result_list

        if not process_results:
            return BatchStatus.SUCCESS

        failed_count = sum(1 for s in process_results if not s.success)

        if failed_count == 0:
            return BatchStatus.SUCCESS
        elif failed_count == len(process_results):
            return BatchStatus.FAILED
        else:
            return BatchStatus.PARTIAL

    def build_profiling_data_map(self, batch_execution_summary: BatchExecutionSummary) -> Dict[Any, Any]:
        # Build ProfilingData für alle Scenarios

        profiling_data_map = {}

        for process_result in batch_execution_summary.process_result_list:
            # Check if profiling data exists
            if (not process_result.tick_loop_results or
                    not process_result.tick_loop_results.profiling_data):
                continue
            profiling = ProfilingData.from_dicts(
                process_result.tick_loop_results.profiling_data.profile_times,
                process_result.tick_loop_results.profiling_data.profile_counts,
                inter_tick_intervals_ms=process_result.tick_loop_results.profiling_data.inter_tick_intervals_ms,
                gap_threshold_s=process_result.tick_loop_results.profiling_data.gap_threshold_s,
                ticks_total=process_result.tick_loop_results.profiling_data.ticks_total
            )
            profiling_data_map[process_result.scenario_index] = profiling
        return profiling_data_map

    def render_all(self, summary_detail: Optional[bool] = None):
        """
        Render complete batch summary.

        Args:
            summary_detail: Override for per-scenario detail rendering.
                            None = use config value, True/False = force.

        Sequence:
        1. Header with basic stats (INCLUDING batch status)
        2. Scenario details (grid)
        3. Portfolio summaries (per process_result + aggregated)
        4. Performance details (per process_result + aggregated)
        5. Bottleneck analysis
        6. Profiling analysis
        7. Worker decision breakdown
        """
        # Calculate batch status
        batch_status = self._calculate_batch_status()

        # Header with batch status
        is_profile_run = self._detect_profile_run()
        if is_profile_run:
            scenarios = self.batch_execution_summary.single_scenario_list
            symbols = sorted(set(s.symbol for s in scenarios))
            profile_info = f"{len(scenarios)} blocks, {len(symbols)} symbol(s)"
            self._renderer.section_header(f"🎉 EXECUTION RESULTS — Profile Run ({profile_info})")
        else:
            self._renderer.section_header("🎉 EXECUTION RESULTS")
        self._render_basic_stats(batch_status)

        # Scenario details grid
        self._renderer.section_separator()
        self._renderer.print_bold("🔍 SCENARIO DETAILS")
        self._renderer.section_separator()

        # Pass show_status_line flag
        show_status_line = batch_status != BatchStatus.SUCCESS
        threshold = self.app_config.get_console_logging_config_object().scenario_detail_threshold
        self._box_renderer.render_scenario_grid(
            self.batch_execution_summary,
            show_status_line=show_status_line,
            scenario_detail_threshold=threshold
        )

        # Summary detail flag (per-scenario vs aggregated only)
        if summary_detail is None:
            summary_detail = self.app_config.get_summary_detail()

        compact = not summary_detail

        # Portfolio summaries
        if summary_detail:
            self.portfolio_summary.render_per_scenario(
                self._renderer, self._box_renderer
            )

        # Aggregate by currency
        aggregator = PortfolioAggregator(
            self.batch_execution_summary.process_result_list)
        aggregated_portfolios = aggregator.aggregate_by_currency()
        self.portfolio_summary.render_aggregated(
            self._renderer, aggregated_portfolios)

        # Trade History
        if summary_detail:
            self.trade_history_summary.render_per_scenario(self._renderer)
        self.trade_history_summary.render_aggregated(self._renderer)

        # Broker configuration
        self.broker_summary.render(self._renderer, compact=compact, threshold=threshold)

        # Performance summaries
        if summary_detail:
            self.performance_summary.render_per_scenario(self._renderer)
        self.performance_summary.render_aggregated(self._renderer)
        self.performance_summary.render_bottleneck_analysis(self._renderer)

        # Profiling Analysis
        if summary_detail:
            self.profiling_summary.render_per_scenario(self._renderer)
        self.profiling_summary.render_aggregated(self._renderer, compact=compact, threshold=threshold)
        self.profiling_summary.render_bottleneck_analysis(self._renderer)

        # Worker Decision Breakdown
        if summary_detail:
            self.worker_decision_breakdown.render_per_scenario(self._renderer)
        self.worker_decision_breakdown.render_aggregated()
        self.worker_decision_breakdown.render_overhead_analysis(self._renderer, compact=compact, threshold=threshold)

        # Warmup phase breakdown (summary_detail only)
        if summary_detail:
            self.warmup_phase_summary.render(self._renderer)

        # Warnings & Notices (always rendered, before executive summary)
        self.warnings_summary.render(self._renderer)

        # Block Splitting Disposition (Profile Runs only, always rendered)
        if self._generator_profiles:
            disposition = BlockSplittingDisposition(
                self.batch_execution_summary, self._generator_profiles
            )
            disposition.render(self._renderer)

        # Executive Summary
        executive = ExecutiveSummary(
            self.batch_execution_summary, self.app_config,
            generator_profiles=self._generator_profiles
        )
        executive.render(self._renderer)

        # Footer
        self._renderer.print_separator(width=120)

        # Footer
        self._renderer.print_separator(width=120)

    def _render_basic_stats(self, batch_status: BatchStatus):
        """
        Render basic execution statistics (top-level summary).

        Args:
            batch_status: Overall batch execution status
        """
        batch_performance_data = self.batch_execution_summary

        scenarios_count = len(batch_performance_data.single_scenario_list)
        exec_time = batch_performance_data.batch_execution_time

        # Check parallel mode
        batch_parallel = self.app_config.get_default_parallel_scenarios()
        max_parallel = self.app_config.get_default_max_parallel_scenarios()

        # Format batch status with color
        if batch_status == BatchStatus.SUCCESS:
            status_str = self._renderer.green("✅ Success: True")
        elif batch_status == BatchStatus.PARTIAL:
            status_str = self._renderer.yellow("⚠️ Success: Partial")
        else:  # FAILED
            status_str = self._renderer.red("❌ Success: False")

        scenarios_str = self._renderer.blue(f"📊 Scenarios: {scenarios_count}")
        time_str = self._renderer.blue(f"⏱️  Time: {exec_time:.2f}s")

        # MODIFIED: Use new status_str
        print(f"{status_str}  |  {scenarios_str}  |  {time_str}")

        # Batch mode
        mode_str = self._renderer.green(
            "Parallel") if batch_parallel else self._renderer.yellow("Sequential")
        print(f"{self._renderer.bold('⚙️  Batch Mode:')} {mode_str}", end="")

        if batch_parallel and scenarios_count > 1:
            concurrent = min(max_parallel, scenarios_count)
            print(f" ({concurrent} concurrent)")
        else:
            print()
