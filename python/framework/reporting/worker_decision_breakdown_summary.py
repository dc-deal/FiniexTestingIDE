"""
FiniexTestingIDE - Worker Decision Breakdown Summary (Facts Only)
Pure data output, no recommendations or suggestions.

:
- Uses typed ProfilingData instead of Dict[str, Any]
- Clean direct property access: profiling_data.get_operation_time()
- No more nested dict navigation

FULLY TYPED: Uses BatchPerformanceStats with direct attribute access.
"""

from typing import Any, Dict, List, Optional
from python.framework.utils.console_renderer import ConsoleRenderer
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.performance_metrics_types import (
    WorkerDecisionBreakdown,
)
from python.framework.types.process_data_types import ProcessResult


class WorkerDecisionBreakdownSummary:
    """
    Worker decision breakdown - facts only.

    FULLY TYPED: Uses BatchPerformanceStats instead of dicts.
    Uses typed ProfilingData for clean access.
    """

    def __init__(self, batch_execution_summary: BatchExecutionSummary, profiling_data_map: Dict[Any, Any]):
        self.batch_execution_summary = batch_execution_summary
        self.profiling_data_map = profiling_data_map
        self.scenario_list = batch_execution_summary.scenario_list
        self.breakdowns = self._build_breakdowns()

    def render_per_scenario(self, renderer: ConsoleRenderer):
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

    def render_aggregated(self):
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
        for scenario in self.scenario_list:
            breakdown = self._build_breakdown_for_scenario(
                scenario)
            if breakdown:
                breakdowns.append(breakdown)
        return breakdowns

    def _build_breakdown_for_scenario(
        self, scenario: ProcessResult
    ) -> Optional[WorkerDecisionBreakdown]:
        """
        Build breakdown for single scenario.

        Uses typed ProfilingData for clean access.
        No more: profiling_data.get('profile_times', {}).get('worker_decision', 0.0)
        Now: profiling_data.get_operation_time('worker_decision')
        """
        profiling = self.profiling_data_map.get(scenario.scenario_index)

        if not profiling:
            return None

        # Get total worker_decision time using typed access
        total_worker_decision_ms = profiling.get_operation_time(
            'worker_decision')

        if total_worker_decision_ms == 0:
            return None

        # Access BatchPerformanceStats directly
        batch_stats = scenario.tick_loop_results.performance_stats

        # Calculate worker execution time from WorkerPerformanceStats objects
        worker_execution_ms = sum(
            w.worker_total_time_ms for w in batch_stats.workers.values()
        )

        # Build worker breakdown dict
        worker_breakdown = {
            name: perf.worker_total_time_ms
            for name, perf in batch_stats.workers.items()
        }

        # Get decision logic time
        decision_logic_ms = 0.0
        if batch_stats.decision_logic:
            decision_logic_ms = batch_stats.decision_logic.decision_total_time_ms

        # Calculate coordination overhead
        coordination_overhead_ms = total_worker_decision_ms - \
            worker_execution_ms - decision_logic_ms
        coordination_overhead_ms = max(0.0, coordination_overhead_ms)

        return WorkerDecisionBreakdown(
            scenario_index=scenario.scenario_index,
            scenario_name=scenario.scenario_name,
            total_time_ms=total_worker_decision_ms,
            total_ticks=scenario.tick_loop_results.performance_stats.ticks_processed,
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
        print("┌────────────────────────────────────────────────────┐")

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

        print("└────────────────────────────────────────────────────┘")
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
