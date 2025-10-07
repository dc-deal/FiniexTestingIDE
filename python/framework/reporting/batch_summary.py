"""
FiniexTestingIDE - Batch Summary
Main orchestrator for all batch execution summaries

REFACTORED (C#003):
- Uses ScenarioSetPerformanceManager for all stats (including portfolio)
- No longer requires TradeSimulator reference

Architecture:
- BatchSummary orchestrates all sub-summaries
- Delegates to specialized summary classes
- Uses ConsoleRenderer for unified output
"""

from python.framework.reporting.portfolio_summary import PortfolioSummary
from python.framework.reporting.performance_summary import PerformanceSummary
from python.framework.reporting.console_renderer import ConsoleRenderer

from python.framework.reporting.scenario_set_performance_manager import ScenarioSetPerformanceManager
from python.framework.trading_env.trade_simulator import TradeSimulator
from python.configuration import AppConfigLoader


class BatchSummary:
    """
    Main summary orchestrator for batch execution results.

    REFACTORED (C#003):
    - Receives ScenarioSetPerformanceManager + TradeSimulator
    - No longer uses batch_results dict
    """

    def __init__(
        self,
        performance_log: ScenarioSetPerformanceManager,
        app_config: AppConfigLoader
    ):
        """
        Initialize batch summary.

        Args:
            performance_log: Performance statistics container (includes portfolio stats)
            app_config: AppConfigLoader instance
        """
        self.performance_log = performance_log
        self.app_config = app_config

        # Initialize sub-summaries (portfolio_summary now uses performance_log)
        self.portfolio_summary = PortfolioSummary(performance_log)
        self.performance_summary = PerformanceSummary(performance_log)

        # Renderer for unified console output
        self.renderer = ConsoleRenderer()

    def render_all(self):
        """
        Render complete batch summary.

        Sequence:
        1. Header with basic stats
        2. Scenario details (grid)
        3. Portfolio summaries (per scenario + aggregated)
        4. Performance details (per scenario + aggregated)
        5. Bottleneck analysis
        """
        # Header
        self.renderer.section_header("🎉 EXECUTION RESULTS")
        self._render_basic_stats()

        # Scenario details grid
        self.renderer.section_separator()
        self.renderer.print_bold("SCENARIO DETAILS")
        self.renderer.section_separator()
        self._render_scenario_grid()

        # Portfolio summaries
        self.renderer.section_separator(width=120)
        self.renderer.print_bold("💰 PORTFOLIO & TRADING RESULTS")
        self.renderer.section_separator(width=120)
        self.portfolio_summary.render_per_scenario(self.renderer)
        self.portfolio_summary.render_aggregated(self.renderer)

        # Performance summaries
        self.renderer.section_separator(width=120)
        self.renderer.print_bold("📊 PERFORMANCE DETAILS (PER SCENARIO)")
        self.renderer.section_separator(width=120)
        self.performance_summary.render_per_scenario(self.renderer)
        self.performance_summary.render_aggregated(self.renderer)
        self.performance_summary.render_bottleneck_analysis(self.renderer)

        # Footer
        self.renderer.print_separator(width=120)

    def _render_basic_stats(self):
        """Render basic execution statistics (top-level summary)."""
        metadata = self.performance_log.get_metadata()

        success = metadata.get('success', False)
        scenarios_count = metadata.get('total_scenarios', 0)
        exec_time = metadata.get('execution_time', 0)

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
        all_scenarios = self.performance_log.get_all_scenarios()

        if not all_scenarios:
            print("No scenario results available")
            return

        # Use renderer for grid
        self.renderer.render_scenario_grid(all_scenarios)
