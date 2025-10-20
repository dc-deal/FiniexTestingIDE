"""
FiniexTestingIDE - Worker Decision Breakdown Summary (Facts Only)
Pure data output, no recommendations or suggestions.
"""

from typing import List, Dict, Any, Optional
from python.framework.reporting.scenario_set_performance_manager import (
    ScenarioSetPerformanceManager
)
from python.framework.types.performance_metrics_types import (
    WorkerDecisionBreakdown,
)
from python.framework.types.scenario_set_performance_types import ScenarioPerformanceStats


class WorkerDecisionBreakdownSummary:
    """Worker decision breakdown - facts only."""

    def __init__(self, performance_log: ScenarioSetPerformanceManager):
        self.performance_log = performance_log
        self.all_scenarios = performance_log.get_all_scenarios()
        self.breakdowns = self._build_breakdowns()

    def render_per_scenario(self, renderer):
        """Render per scenario breakdown."""
        if not self.breakdowns:
            print("No data")
            return

        for idx, breakdown in enumerate(self.breakdowns, 1):
            if idx > 1:
                print()
                renderer.print_separator(width=120, char="·")
                print()
            self._render_scenario_breakdown(breakdown, renderer)

    def render_aggregated(self, renderer):
        """Render aggregated breakdown."""
        if not self.breakdowns:
            print("No data")
            return

        print()

    def render_overhead_analysis(self, renderer):
        """Render overhead analysis."""
        print()
        renderer.section_separator()
        renderer.print_bold("🔥 OVERHEAD ANALYSIS")
        renderer.section_separator()
        self._render_overhead_details(renderer)
        print()

    def _build_breakdowns(self) -> List[WorkerDecisionBreakdown]:
        """Build breakdowns from scenarios."""
        breakdowns = []
        for scenario in self.all_scenarios:
            breakdown = self._build_breakdown_for_scenario(scenario)
            if breakdown:
                breakdowns.append(breakdown)
        return breakdowns

    def _build_breakdown_for_scenario(
        self, scenario: ScenarioPerformanceStats
    ) -> Optional[WorkerDecisionBreakdown]:
        """Build breakdown for single scenario."""
        profiling_data = scenario.profiling_data
        if not profiling_data:
            return None

        profile_times = profiling_data.get('profile_times', {})
        total_worker_decision_ms = profile_times.get('worker_decision', 0.0)

        if total_worker_decision_ms == 0:
            return None

        worker_stats = scenario.worker_statistics
        worker_data = worker_stats.get('worker_statistics', {})
        workers = worker_data.get('workers', {})

        worker_execution_ms = 0.0
        worker_breakdown = {}
        for worker_name, worker_perf in workers.items():
            worker_time = worker_perf.get('total_time_ms', 0.0)
            worker_execution_ms += worker_time
            worker_breakdown[worker_name] = worker_time

        decision_stats = worker_stats.get('decision_logic_statistics', {})
        decision_logic_ms = decision_stats.get('total_time_ms', 0.0)

        coordination_overhead_ms = total_worker_decision_ms - \
            worker_execution_ms - decision_logic_ms
        coordination_overhead_ms = max(0.0, coordination_overhead_ms)

        return WorkerDecisionBreakdown(
            scenario_index=scenario.scenario_index,
            scenario_name=scenario.scenario_name,
            total_time_ms=total_worker_decision_ms,
            total_ticks=scenario.ticks_processed,
            worker_execution_ms=worker_execution_ms,
            decision_logic_ms=decision_logic_ms,
            coordination_overhead_ms=coordination_overhead_ms,
            worker_breakdown=worker_breakdown
        )

    def _render_scenario_breakdown(self, breakdown: WorkerDecisionBreakdown, renderer):
        """Render scenario breakdown."""
        print(f"{renderer.bold('Scenario:')} {renderer.blue(breakdown.scenario_name)}")
        print(f"{renderer.gray('Total:')} {breakdown.total_time_ms:.2f}ms  |  "
              f"{renderer.gray('Ticks:')} {breakdown.total_ticks:,}")
        print()

        # Component breakdown
        print(renderer.bold("Components:"))
        print("┌─────────────────────────────────────────────────────────┐")

        bar_workers = self._create_bar(breakdown.worker_execution_pct)
        color = renderer.green if breakdown.worker_execution_pct > 50 else renderer.yellow
        print(f"│ Worker Execution      {breakdown.worker_execution_ms:>7.2f}ms  {bar_workers}  "
              f"{color(f'{breakdown.worker_execution_pct:>5.1f}%')}      │")

        bar_decision = self._create_bar(breakdown.decision_logic_pct)
        print(f"│ Decision Logic        {breakdown.decision_logic_ms:>7.2f}ms  {bar_decision}  "
              f"{breakdown.decision_logic_pct:>5.1f}%      │")

        bar_overhead = self._create_bar(breakdown.coordination_overhead_pct)
        color = renderer.red if breakdown.is_high_overhead else renderer.yellow
        indicator = " ⚠️ " if breakdown.is_high_overhead else "    "
        print(f"│ Coordination Overhead {breakdown.coordination_overhead_ms:>7.2f}ms  {bar_overhead}  "
              f"{color(f'{breakdown.coordination_overhead_pct:>5.1f}%')} {indicator}│")

        print("└─────────────────────────────────────────────────────────┘")
        print()

        # Workers
        if breakdown.worker_breakdown:
            print(renderer.bold("Workers:"))
            for worker_name, worker_time in sorted(
                breakdown.worker_breakdown.items(),
                key=lambda x: x[1],
                reverse=True
            ):
                pct = (worker_time / breakdown.total_time_ms) * 100
                print(f"  {worker_name:<20} {worker_time:>7.2f}ms  {pct:>5.1f}%")
            print()

    def _render_overhead_details(self, renderer):
        """Render overhead analysis."""
        if not self.breakdowns:
            return

        high_overhead = [b for b in self.breakdowns if b.is_high_overhead]

        if not high_overhead:
            print(renderer.green("✅ No high overhead"))
            return

        high_overhead.sort(key=lambda b: b.overhead_ratio, reverse=True)

        print(f"High overhead: {len(high_overhead)}")
        print()

        header = f"{'Scenario':<30} {'Overhead':<12} {'Ratio':<10}"
        print(renderer.bold(header))
        print("-" * 55)

        for breakdown in high_overhead:
            name = breakdown.scenario_name[:28]
            overhead = f"{breakdown.coordination_overhead_ms:.2f}ms"
            ratio = f"{breakdown.overhead_ratio:.2f}x"
            status = renderer.red(
                "Critical") if breakdown.overhead_ratio >= 2.0 else renderer.yellow("High")
            print(f"{name:<30} {overhead:<12} {ratio:<10} {status}")

    def _create_bar(self, percentage: float, width: int = 12) -> str:
        """Create ASCII bar."""
        filled = int((percentage / 100) * width)
        empty = width - filled
        return '█' * filled + '░' * empty
