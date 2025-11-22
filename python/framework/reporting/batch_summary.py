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
from python.framework.reporting.portfolio_aggregator import PortfolioAggregator
from python.framework.reporting.portfolio_summary import PortfolioSummary
from python.framework.reporting.performance_summary import PerformanceSummary
from python.framework.reporting.profiling_summary import ProfilingSummary
from python.framework.reporting.worker_decision_breakdown_summary import WorkerDecisionBreakdownSummary
from python.framework.reporting.console_renderer import ConsoleRenderer
from python.configuration import AppConfigManager
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
        self.renderer = ConsoleRenderer()

    def build_profiling_data_map(self, batch_execution_summary: BatchExecutionSummary) -> Dict[Any, Any]:
        # Build ProfilingData für alle Scenarios

        profiling_data_map = {}

        for scenario in batch_execution_summary.scenario_list:
            # Check if profiling data exists
            if not scenario.tick_loop_results.profiling_data:
                return {}
            profiling = ProfilingData.from_dicts(
                scenario.tick_loop_results.profiling_data.profile_times,
                scenario.tick_loop_results.profiling_data.profile_counts
            )
            profiling_data_map[scenario.scenario_index] = profiling
        return profiling_data_map

    def render_all(self):
        """
        Render complete batch summary.

        Sequence:
        1. Header with basic stats
        2. Scenario details (grid)
        3. Portfolio summaries (per scenario + aggregated)
        4. Performance details (per scenario + aggregated)
        5. Bottleneck analysis
        6. Profiling analysis (NEW)
        7. Worker decision breakdown (NEW)
        """
        # Header
        self.renderer.section_header("🎉 EXECUTION RESULTS")
        self._render_basic_stats()

        # Scenario details grid
        self.renderer.section_separator()
        self.renderer.print_bold("🔍 SCENARIO DETAILS")
        self.renderer.section_separator()
        self._render_scenario_grid()

        # Portfolio summaries
        self.renderer.section_separator()
        self.renderer.print_bold("💰 PORTFOLIO & TRADING RESULTS")
        self.renderer.section_separator()
        self.portfolio_summary.render_per_scenario(self.renderer)

        # Aggregate by currency
        aggregator = PortfolioAggregator(
            self.batch_execution_summary.scenario_list)
        aggregated_portfolios = aggregator.aggregate_by_currency()
        self.portfolio_summary.render_aggregated(
            self.renderer, aggregated_portfolios)

        # Broker configuration
        self.renderer.section_separator()
        self.renderer.print_bold("🏦 BROKER CONFIGURATION")
        self.renderer.section_separator()
        self.broker_summary.render(self.renderer)

        # Performance summaries
        self.renderer.section_separator()
        self.renderer.print_bold("📊 PERFORMANCE DETAILS (PER SCENARIO)")
        self.renderer.section_separator()
        self.performance_summary.render_per_scenario(self.renderer)
        self.performance_summary.render_aggregated(self.renderer)
        self.performance_summary.render_bottleneck_analysis(self.renderer)

        # === Profiling Analysis ===
        self.renderer.section_separator()
        self.renderer.print_bold("⚡ PROFILING ANALYSIS")
        self.renderer.section_separator()
        self.profiling_summary.render_per_scenario(self.renderer)
        self.profiling_summary.render_aggregated(self.renderer)
        self.profiling_summary.render_bottleneck_analysis(self.renderer)

        # === Worker Decision Breakdown ===
        self.renderer.section_separator()
        self.renderer.print_bold("🔍 WORKER DECISION BREAKDOWN")
        self.renderer.section_separator()
        self.worker_decision_breakdown.render_per_scenario(self.renderer)
        self.worker_decision_breakdown.render_aggregated(self.renderer)
        self.worker_decision_breakdown.render_overhead_analysis(self.renderer)

        # Footer
        self.renderer.print_separator(width=120)

    def _render_basic_stats(self):
        """Render basic execution statistics (top-level summary)."""
        batch_performance_data = self.batch_execution_summary

        success = batch_performance_data.success
        scenarios_count = batch_performance_data.scenarios_count
        exec_time = batch_performance_data.summary_execution_time

        # Check parallel mode
        batch_parallel = self.app_config.get_default_parallel_scenarios()
        max_parallel = self.app_config.get_default_max_parallel_scenarios()

        # Render basic stats
        success_str = self.renderer.green(f"✅ Success: {success}")
        scenarios_str = self.renderer.blue(f"📊 Scenarios: {scenarios_count}")
        time_str = self.renderer.blue(f"⏱️  Time: {exec_time:.2f}s")

        print(f"{success_str}  |  {scenarios_str}  |  {time_str}")

        # Batch mode
        mode_str = self.renderer.green(
            "Parallel") if batch_parallel else self.renderer.yellow("Sequential")
        print(f"{self.renderer.bold('⚙️  Batch Mode:')} {mode_str}", end="")

        if batch_parallel and scenarios_count > 1:
            concurrent = min(max_parallel, scenarios_count)
            print(f" ({concurrent} scenarios concurrent)")
        else:
            print()

    def _render_scenario_grid(self):
        """Render scenario details in grid format."""
        all_scenarios = self.batch_execution_summary.scenario_list

        if not all_scenarios:
            print("No scenario results available")
            return

        # Use renderer for grid
        self.renderer.render_scenario_grid(all_scenarios)
