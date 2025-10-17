"""
FiniexTestingIDE - Worker Decision Breakdown Summary
Detailed analysis of worker_decision bottleneck

CREATED (New):
- Analyzes the worker_decision operation in detail
- Shows worker execution vs coordination overhead
- Identifies performance inefficiencies
- Provides targeted optimization recommendations

Architecture:
- Uses ScenarioSetPerformanceManager for data
- Combines TickLoopProfile (macro) with PerformanceLogCoordinator (micro)
- Calculates coordination overhead as difference
"""

from typing import List, Dict, Any, Optional
from python.framework.reporting.scenario_set_performance_manager import (
    ScenarioSetPerformanceManager,
    ScenarioPerformanceStats
)
from python.framework.types.performance_metrics_types import (
    WorkerDecisionBreakdown
)


class WorkerDecisionBreakdownSummary:
    """
    Detailed breakdown of worker_decision performance.

    Analyzes the largest bottleneck (typically 70-90% of tick time):
    - How much time is actual computation (workers + decision logic)
    - How much time is coordination overhead
    - Where optimization efforts should focus
    """

    def __init__(self, performance_log: ScenarioSetPerformanceManager):
        """
        Initialize worker decision breakdown summary.

        Args:
            performance_log: Performance statistics container
        """
        self.performance_log = performance_log
        self.all_scenarios = performance_log.get_all_scenarios()

        # Build breakdowns from scenarios
        self.breakdowns = self._build_breakdowns()

    def render_per_scenario(self, renderer):
        """
        Render worker decision breakdown per scenario.

        Shows:
        - Component time breakdown
        - Worker-level details
        - Overhead analysis
        - Optimization targets

        Args:
            renderer: ConsoleRenderer instance
        """
        if not self.breakdowns:
            print("No worker decision breakdown data available")
            return

        for idx, breakdown in enumerate(self.breakdowns, 1):
            # Separator between scenarios
            if idx > 1:
                print()
                renderer.print_separator(width=120, char="·")
                print()

            self._render_scenario_breakdown(breakdown, renderer)

    def render_aggregated(self, renderer):
        """
        Render aggregated breakdown across all scenarios.

        Shows:
        - Average overhead across scenarios
        - Common patterns
        - Overall efficiency metrics

        Args:
            renderer: ConsoleRenderer instance
        """
        if not self.breakdowns:
            print("No aggregated breakdown data available")
            return

        print()
        renderer.section_separator()
        renderer.print_bold("⚡ AGGREGATED WORKER DECISION ANALYSIS")
        renderer.section_separator()

        self._render_aggregated_details(renderer)
        print()

    def render_overhead_analysis(self, renderer):
        """
        Render overhead analysis and optimization recommendations.

        Shows:
        - High overhead scenarios
        - Root cause analysis
        - Specific optimization strategies

        Args:
            renderer: ConsoleRenderer instance
        """
        print()
        renderer.section_separator()
        print(f"{renderer.bold(renderer.red('🔥 OVERHEAD ANALYSIS'))} "
              f"{renderer.gray('(Coordination Inefficiency)')}")
        renderer.section_separator()

        self._render_overhead_details(renderer)
        print()

    def _build_breakdowns(self) -> List[WorkerDecisionBreakdown]:
        """
        Build WorkerDecisionBreakdown from scenario data.

        Combines:
        - Tick loop profiling (worker_decision total time)
        - PerformanceLogCoordinator (worker + decision logic details)

        Returns:
            List of WorkerDecisionBreakdown objects
        """
        breakdowns = []

        for scenario in self.all_scenarios:
            breakdown = self._build_breakdown_for_scenario(scenario)
            if breakdown:
                breakdowns.append(breakdown)

        return breakdowns

    def _build_breakdown_for_scenario(
        self, scenario: ScenarioPerformanceStats
    ) -> Optional[WorkerDecisionBreakdown]:
        """
        Build breakdown for a single scenario.

        Args:
            scenario: ScenarioPerformanceStats object

        Returns:
            WorkerDecisionBreakdown or None if insufficient data
        """
        # Get profiling data (tick loop level)
        profiling_data = scenario.profiling_data
        if not profiling_data:
            return None

        profile_times = profiling_data.get('profile_times', {})
        total_worker_decision_ms = profile_times.get('worker_decision', 0.0)

        if total_worker_decision_ms == 0:
            return None

        # Get worker statistics (micro level)
        worker_stats = scenario.worker_statistics
        worker_data = worker_stats.get('worker_statistics', {})
        workers = worker_data.get('workers', {})

        # Calculate worker execution time
        worker_execution_ms = 0.0
        worker_breakdown = {}

        for worker_name, worker_perf in workers.items():
            worker_time = worker_perf.get('total_time_ms', 0.0)
            worker_execution_ms += worker_time
            worker_breakdown[worker_name] = worker_time

        # Get decision logic time
        decision_stats = worker_stats.get('decision_logic_statistics', {})
        decision_logic_ms = decision_stats.get('total_time_ms', 0.0)

        # Calculate overhead (the mystery!)
        coordination_overhead_ms = total_worker_decision_ms - \
            worker_execution_ms - decision_logic_ms

        # Ensure overhead is not negative (can happen due to timing precision)
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
        """Render breakdown for single scenario."""
        # Header
        print(f"{renderer.bold('Scenario:')} {renderer.blue(breakdown.scenario_name)}")
        print(f"{renderer.gray('Total Worker Decision Time:')} {breakdown.total_time_ms:.2f}ms  |  "
              f"{renderer.gray('Ticks:')} {breakdown.total_ticks:,}")
        print()

        # Component breakdown
        print(renderer.bold("Component Breakdown:"))
        print("┌─────────────────────────────────────────────────────────┐")

        # Worker execution
        bar_workers = self._create_bar(breakdown.worker_execution_pct)
        worker_color = renderer.green if breakdown.worker_execution_pct > 50 else renderer.yellow
        print(f"│ Worker Execution      {breakdown.worker_execution_ms:>7.2f}ms  {bar_workers}  "
              f"{worker_color(f'{breakdown.worker_execution_pct:>5.1f}%')}      │")

        # Decision logic
        bar_decision = self._create_bar(breakdown.decision_logic_pct)
        print(f"│ Decision Logic        {breakdown.decision_logic_ms:>7.2f}ms  {bar_decision}  "
              f"{breakdown.decision_logic_pct:>5.1f}%      │")

        # Overhead
        bar_overhead = self._create_bar(breakdown.coordination_overhead_pct)
        overhead_color = renderer.red if breakdown.is_high_overhead else renderer.yellow
        overhead_indicator = " ⚠️ " if breakdown.is_high_overhead else "    "
        print(f"│ Coordination Overhead {breakdown.coordination_overhead_ms:>7.2f}ms  {bar_overhead}  "
              f"{overhead_color(f'{breakdown.coordination_overhead_pct:>5.1f}%')} {overhead_indicator}│")

        print("└─────────────────────────────────────────────────────────┘")
        print()

        # Individual workers
        if breakdown.worker_breakdown:
            print(renderer.bold("Individual Workers:"))
            for worker_name, worker_time in sorted(
                breakdown.worker_breakdown.items(),
                key=lambda x: x[1],
                reverse=True
            ):
                worker_pct = (worker_time / breakdown.total_time_ms) * 100
                print(
                    f"  {worker_name:<20} {worker_time:>7.2f}ms  ({worker_pct:>5.1f}% of worker_decision)")
            print()

        # Overhead warning
        if breakdown.is_high_overhead:
            print(renderer.red("⚠️  HIGH OVERHEAD DETECTED!"))
            print(f"   Coordination overhead ({breakdown.coordination_overhead_ms:.2f}ms) is "
                  f"{breakdown.overhead_ratio:.1f}x the actual computation time!")
            print()
            print("   Possible causes:")
            print("     • Excessive data copying/marshalling")
            print("     • Bar history retrieval inefficiency")
            print("     • Python GIL contention")
            print("     • Thread coordination overhead")

    def _render_aggregated_details(self, renderer):
        """Render aggregated breakdown statistics."""
        if not self.breakdowns:
            return

        # Calculate averages
        avg_worker_execution = sum(
            b.worker_execution_pct for b in self.breakdowns) / len(self.breakdowns)
        avg_decision_logic = sum(
            b.decision_logic_pct for b in self.breakdowns) / len(self.breakdowns)
        avg_overhead = sum(
            b.coordination_overhead_pct for b in self.breakdowns) / len(self.breakdowns)

        total_worker_time = sum(b.worker_execution_ms for b in self.breakdowns)
        total_decision_time = sum(b.decision_logic_ms for b in self.breakdowns)
        total_overhead = sum(
            b.coordination_overhead_ms for b in self.breakdowns)
        total_time = sum(b.total_time_ms for b in self.breakdowns)

        # Average overhead ratio
        avg_overhead_ratio = sum(
            b.overhead_ratio for b in self.breakdowns) / len(self.breakdowns)
        high_overhead_count = sum(
            1 for b in self.breakdowns if b.is_high_overhead)

        # Summary stats
        print(f"{renderer.bold('Total Scenarios:')} {len(self.breakdowns)}")
        print(
            f"{renderer.bold('High Overhead Scenarios:')} {high_overhead_count}/{len(self.breakdowns)}")
        print()

        # Average percentages
        print(renderer.bold("Average Time Distribution:"))
        print(f"  Worker Execution:      {avg_worker_execution:>5.1f}%")
        print(f"  Decision Logic:        {avg_decision_logic:>5.1f}%")
        print(f"  Coordination Overhead: {avg_overhead:>5.1f}%")
        print()

        # Total times
        print(renderer.bold("Total Times (all scenarios):"))
        print(f"  Worker Execution:      {total_worker_time:>10.2f}ms")
        print(f"  Decision Logic:        {total_decision_time:>10.2f}ms")
        print(f"  Coordination Overhead: {total_overhead:>10.2f}ms")
        print(f"  {renderer.bold('Total:')}                   {total_time:>10.2f}ms")
        print()

        # Efficiency metrics
        computation_time = total_worker_time + total_decision_time
        efficiency = (computation_time / total_time *
                      100) if total_time > 0 else 0

        print(renderer.bold("Efficiency Metrics:"))
        print(
            f"  Actual Computation:    {computation_time:>10.2f}ms ({efficiency:.1f}% of total)")
        print(
            f"  Overhead:              {total_overhead:>10.2f}ms ({100-efficiency:.1f}% of total)")
        print(
            f"  Average Overhead Ratio: {avg_overhead_ratio:.2f}x computation time")

        if avg_overhead_ratio > 1.0:
            print()
            print(renderer.red(
                f"  ⚠️  Overhead is {avg_overhead_ratio:.1f}x the computation time!"))

    def _render_overhead_details(self, renderer):
        """Render detailed overhead analysis."""
        if not self.breakdowns:
            return

        # Find high overhead scenarios
        high_overhead = [b for b in self.breakdowns if b.is_high_overhead]

        if not high_overhead:
            print(renderer.green(
                "✅ No high overhead detected! All scenarios performing efficiently."))
            return

        # Sort by overhead ratio
        high_overhead.sort(key=lambda b: b.overhead_ratio, reverse=True)

        print(f"Found {len(high_overhead)} scenario(s) with high overhead:")
        print()

        # Table header
        header = f"{'Scenario':<30} {'Overhead':<12} {'Ratio':<10} {'Status':<15}"
        print(renderer.bold(header))
        print("-" * 70)

        for breakdown in high_overhead:
            scenario_name = breakdown.scenario_name[:28]
            overhead_ms = f"{breakdown.coordination_overhead_ms:.2f}ms"
            ratio = f"{breakdown.overhead_ratio:.2f}x"

            if breakdown.overhead_ratio >= 2.0:
                status = renderer.red("Critical")
            elif breakdown.overhead_ratio >= 1.0:
                status = renderer.yellow("High")
            else:
                status = renderer.yellow("Medium")

            print(f"{scenario_name:<30} {overhead_ms:<12} {ratio:<10} {status:<15}")

        print()

        # Root cause analysis
        self._render_root_cause_analysis(renderer, high_overhead)

    def _render_root_cause_analysis(self, renderer, high_overhead: List[WorkerDecisionBreakdown]):
        """Render root cause analysis for high overhead."""
        print(renderer.bold(renderer.yellow("💡 Root Cause Analysis:")))
        print()

        # Analyze patterns
        avg_workers = sum(len(b.worker_breakdown)
                          for b in high_overhead) / len(high_overhead)

        print("Based on the overhead patterns, likely causes are:")
        print()

        if avg_workers >= 3:
            print(
                f"  1. {renderer.bold('Multiple Workers')} ({avg_workers:.0f} workers on average)")
            print("     → Consider reducing number of workers")
            print("     → Enable parallel execution if not already active")
            print()

        print(f"  2. {renderer.bold('Data Marshalling Overhead')}")
        print("     → Bar history is copied/passed around inefficiently")
        print("     → Consider using views instead of copies")
        print("     → Profile memory allocations in worker_coordinator.py")
        print()

        print(f"  3. {renderer.bold('Worker Coordination')}")
        print("     → Thread pool overhead (if parallel)")
        print("     → Worker state management overhead")
        print("     → Result collection inefficiency")
        print()

        # Optimization recommendations
        print(renderer.bold("🎯 Optimization Strategies:"))
        print()
        print("  Priority 1 (Quick Wins):")
        print("    • Profile bar_history retrieval in batch_orchestrator")
        print("    • Check if bar_history can be cached or passed by reference")
        print("    • Review worker result collection (minimize copying)")
        print()
        print("  Priority 2 (Medium Effort):")
        print("    • Add detailed profiling inside worker_coordinator.process_tick()")
        print("    • Identify exact source of coordination overhead")
        print("    • Consider C extension for hot paths")
        print()
        print("  Priority 3 (Major Refactor):")
        print("    • Consider Cython for worker coordination layer")
        print("    • Implement zero-copy bar history access")
        print("    • Use shared memory for multi-worker scenarios")

    def _create_bar(self, percentage: float, width: int = 12) -> str:
        """
        Create ASCII progress bar.

        Args:
            percentage: Percentage (0-100)
            width: Bar width in characters

        Returns:
            ASCII bar string
        """
        filled = int((percentage / 100) * width)
        empty = width - filled
        return '█' * filled + '░' * empty
