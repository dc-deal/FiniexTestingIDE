"""
FiniexTestingIDE - Batch Summary
Main orchestrator for all batch execution summaries

Architecture:
- BatchSummary orchestrates all sub-summaries
- Delegates to specialized summary classes
- Uses ConsoleRenderer for unified output
"""

from typing import Any, Dict
from python.framework.reporting.broker_summary import BrokerSummary
from python.framework.reporting.grid.console_box_renderer import ConsoleBoxRenderer
from python.framework.reporting.portfolio_aggregator import PortfolioAggregator
from python.framework.reporting.portfolio_summary import PortfolioSummary
from python.framework.reporting.performance_summary import PerformanceSummary
from python.framework.reporting.profiling_summary import ProfilingSummary
from python.framework.reporting.worker_decision_breakdown_summary import WorkerDecisionBreakdownSummary
from python.framework.types.rendering_types import BatchStatus
from python.framework.utils.console_renderer import ConsoleRenderer
from python.configuration.app_config_manager import AppConfigManager
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.scenario_set_performance_types import ProfilingData


class BatchSummary:
    """
    Main summary orchestrator for batch execution results.

    """

    def __init__(
        self,
        batch_execution_summary: BatchExecutionSummary,
        app_config: AppConfigManager
    ):
        """
        Initialize batch summary.

        Args:
            performance_log_coordinator: Performance statistics container (includes portfolio stats)
            app_config: AppConfigManager instance
        """
        self.batch_execution_summary = batch_execution_summary
        self.app_config = app_config

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
        # Renderer for unified console output
        self._renderer = ConsoleRenderer()
        self._box_renderer = ConsoleBoxRenderer(self._renderer)

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
                process_result.tick_loop_results.profiling_data.profile_counts
            )
            profiling_data_map[process_result.scenario_index] = profiling
        return profiling_data_map

    def render_all(self):
        """
        Render complete batch summary.

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
        self._renderer.section_header("🎉 EXECUTION RESULTS")
        self._render_basic_stats(batch_status)

        # Scenario details grid
        self._renderer.section_separator()
        self._renderer.print_bold("🔍 SCENARIO DETAILS")
        self._renderer.section_separator()

        # Pass show_status_line flag
        show_status_line = batch_status != BatchStatus.SUCCESS
        self._box_renderer.render_scenario_grid(
            self.batch_execution_summary,
            show_status_line=show_status_line
        )

        # Portfolio summaries
        self._renderer.section_separator()
        self._renderer.print_bold("💰 PORTFOLIO & TRADING RESULTS")
        self._renderer.section_separator()

        # Pass show_status_line flag
        self.portfolio_summary.render_per_scenario(
            self._box_renderer
        )

        # Aggregate by currency
        aggregator = PortfolioAggregator(
            self.batch_execution_summary.process_result_list)
        aggregated_portfolios = aggregator.aggregate_by_currency()
        self.portfolio_summary.render_aggregated(
            self._renderer, aggregated_portfolios)

        # Broker configuration
        self._renderer.section_separator()
        self._renderer.print_bold("🏦 BROKER CONFIGURATION")
        self._renderer.section_separator()
        self.broker_summary.render(self._renderer)

        # Performance summaries
        self._renderer.section_separator()
        self._renderer.print_bold("📊 PERFORMANCE DETAILS (PER SCENARIO)")
        self._renderer.section_separator()
        self.performance_summary.render_per_scenario(self._renderer)
        self.performance_summary.render_aggregated(self._renderer)
        self.performance_summary.render_bottleneck_analysis(self._renderer)

        # === Profiling Analysis ===
        self._renderer.section_separator()
        self._renderer.print_bold("⚡ PROFILING ANALYSIS")
        self._renderer.section_separator()
        self.profiling_summary.render_per_scenario(self._renderer)
        self.profiling_summary.render_aggregated(self._renderer)
        self.profiling_summary.render_bottleneck_analysis(self._renderer)

        # === Worker Decision Breakdown ===
        self._renderer.section_separator()
        self._renderer.print_bold("🔍 WORKER DECISION BREAKDOWN")
        self._renderer.section_separator()
        self.worker_decision_breakdown.render_per_scenario(self._renderer)
        self.worker_decision_breakdown.render_aggregated()
        self.worker_decision_breakdown.render_overhead_analysis(self._renderer)

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

        # NEW: Format batch status with color
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
