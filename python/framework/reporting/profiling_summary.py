"""
FiniexTestingIDE - Profiling Summary
Performance profiling and bottleneck analysis reporting

CREATED (New):
- Renders profiling data from batch_orchestrator
- Shows per-operation timing breakdowns
- Identifies performance bottlenecks
- Cross-scenario performance comparison

Architecture:
- Uses ScenarioSetPerformanceManager for profiling data
- Creates TickLoopProfile from raw profiling data
- Renders via ConsoleRenderer
"""

from typing import List, Dict, Any, Optional
from python.framework.reporting.scenario_set_performance_manager import (
    ScenarioSetPerformanceManager,
    ScenarioPerformanceStats
)
from python.framework.types.performance_metrics_types import (
    TickLoopProfile,
    OperationProfile,
    ProfilingMetrics
)


class ProfilingSummary:
    """
    Performance profiling and bottleneck analysis.

    Renders:
    - Per-scenario operation breakdowns
    - Timing percentages
    - Bottleneck identification
    - Cross-scenario comparison
    - Optimization recommendations
    """

    def __init__(self, performance_log: ScenarioSetPerformanceManager):
        """
        Initialize profiling summary.

        Args:
            performance_log: Performance statistics container
        """
        self.performance_log = performance_log
        self.all_scenarios = performance_log.get_all_scenarios()

        # Build profiling metrics from scenarios
        self.profiling_metrics = self._build_profiling_metrics()

    def render_per_scenario(self, renderer):
        """
        Render profiling breakdown per scenario.

        Shows:
        - Operation timing table
        - Percentage breakdown
        - Bottleneck identification

        Args:
            renderer: ConsoleRenderer instance
        """
        if not self.all_scenarios:
            print("No profiling data available")
            return

        for idx, scenario in enumerate(self.all_scenarios, 1):
            # Separator between scenarios
            if idx > 1:
                print()
                renderer.print_separator(width=120, char="·")
                print()

            # Get profiling data from scenario
            profile = self._extract_profile_from_scenario(scenario)

            if not profile:
                print(f"No profiling data for {scenario.scenario_name}")
                continue

            self._render_scenario_profile(profile, renderer)

    def render_aggregated(self, renderer):
        """
        Render aggregated profiling across all scenarios.

        Shows:
        - Total ticks processed
        - Average tick time
        - Most common bottleneck
        - Cross-scenario comparison

        Args:
            renderer: ConsoleRenderer instance
        """
        if not self.profiling_metrics.scenario_profiles:
            print("No aggregated profiling data available")
            return

        print()
        renderer.section_separator()
        renderer.print_bold("⚡ AGGREGATED PROFILING (ALL SCENARIOS)")
        renderer.section_separator()

        self._render_aggregated_details(renderer)
        print()

    def render_bottleneck_analysis(self, renderer):
        """
        Render bottleneck analysis and optimization recommendations.

        Shows:
        - Bottleneck frequency across scenarios
        - Worst performers
        - Optimization suggestions

        Args:
            renderer: ConsoleRenderer instance
        """
        print()
        renderer.section_separator()
        print(f"{renderer.bold(renderer.red('🔥 BOTTLENECK ANALYSIS'))} "
              f"{renderer.gray('(Performance Optimization Targets)')}")
        renderer.section_separator()

        self._render_bottleneck_details(renderer)
        print()

    def _extract_profile_from_scenario(
        self, scenario: ScenarioPerformanceStats
    ) -> Optional[TickLoopProfile]:
        """
        Extract TickLoopProfile from ScenarioPerformanceStats.

        Checks if scenario has profiling data in worker_statistics
        or as a separate field.

        Args:
            scenario: ScenarioPerformanceStats object

        Returns:
            TickLoopProfile or None if no profiling data
        """
        # Check if profiling data exists in worker_statistics
        worker_stats = scenario.worker_statistics

        # Look for profiling data (could be in different places)
        # Option 1: Direct profiling field (if added to ScenarioPerformanceStats)
        if hasattr(scenario, 'profiling_data') and scenario.profiling_data:
            return self._build_profile_from_dict(
                scenario.profiling_data,
                scenario.scenario_index,
                scenario.scenario_name,
                scenario.ticks_processed
            )

        # Option 2: In worker_statistics
        if 'profiling' in worker_stats:
            return self._build_profile_from_dict(
                worker_stats['profiling'],
                scenario.scenario_index,
                scenario.scenario_name,
                scenario.ticks_processed
            )

        # No profiling data found
        return None

    def _build_profile_from_dict(
        self,
        profiling_dict: Dict[str, Any],
        scenario_index: int,
        scenario_name: str,
        total_ticks: int
    ) -> TickLoopProfile:
        """
        Build TickLoopProfile from raw profiling dictionary.

        Expected dict structure (from batch_orchestrator):
        {
            'profile_times': {
                'trade_simulator': float (ms),
                'bar_rendering': float (ms),
                ...
            },
            'profile_counts': {
                'trade_simulator': int,
                'bar_rendering': int,
                ...
            },
            'total_per_tick': float (ms)
        }

        Args:
            profiling_dict: Raw profiling data
            scenario_index: Scenario index
            scenario_name: Scenario name
            total_ticks: Total ticks processed

        Returns:
            TickLoopProfile object
        """
        profile_times = profiling_dict.get('profile_times', {})
        profile_counts = profiling_dict.get('profile_counts', {})
        total_time = profile_times.get('total_per_tick', 0.0)

        # Build operation profiles
        operations = []

        operation_names = [
            'trade_simulator',
            'bar_rendering',
            'bar_history',
            'worker_decision',
            'order_execution',
            'stats_update'
        ]

        for op_name in operation_names:
            if op_name not in profile_times:
                continue

            op_time = profile_times[op_name]
            op_count = profile_counts.get(op_name, 0)

            if op_count == 0:
                continue

            avg_time = op_time / op_count
            percentage = (op_time / total_time *
                          100) if total_time > 0 else 0.0

            operations.append(OperationProfile(
                operation_name=op_name,
                total_time_ms=op_time,
                call_count=op_count,
                avg_time_ms=avg_time,
                percentage=percentage
            ))

        # Sort by percentage (highest first)
        operations.sort(key=lambda op: op.percentage, reverse=True)

        return TickLoopProfile(
            scenario_index=scenario_index,
            scenario_name=scenario_name,
            total_ticks=total_ticks,
            operations=operations,
            total_time_ms=total_time,
            avg_time_per_tick_ms=total_time / total_ticks if total_ticks > 0 else 0.0
        )

    def _build_profiling_metrics(self) -> ProfilingMetrics:
        """Build ProfilingMetrics from all scenarios."""
        metrics = ProfilingMetrics()

        for scenario in self.all_scenarios:
            profile = self._extract_profile_from_scenario(scenario)
            if profile:
                metrics.add_scenario_profile(profile)

        return metrics

    def _render_scenario_profile(self, profile: TickLoopProfile, renderer):
        """Render single scenario's profiling breakdown."""
        # Header
        print(f"{renderer.bold('Scenario:')} {renderer.blue(profile.scenario_name)}")
        print(f"{renderer.gray('Ticks:')} {profile.total_ticks:,}  |  "
              f"{renderer.gray('Avg/Tick:')} {profile.avg_time_per_tick_ms:.3f}ms  |  "
              f"{renderer.gray('Total:')} {profile.total_time_ms:.2f}ms")
        print()

        # Operations table
        if not profile.operations:
            print("  No data")
            return

        # Header
        header = f"{'Operation':<22} {'Total':<15} {'Avg':<15} {'Calls':<10} {'%':<10}"
        print(renderer.bold(header))
        print("-" * 75)

        # Rows
        for op in profile.operations:
            # Color-code
            if op.percentage >= 40:
                color_func = renderer.red
            elif op.percentage >= 20:
                color_func = renderer.yellow
            else:
                def color_func(x): return x

            op_name = color_func(f"{op.operation_name:<22}")
            total = f"{op.total_time_ms:>10.2f}ms"
            avg = f"{op.avg_time_ms:>10.3f}ms"
            calls = f"{op.call_count:>8,}"
            pct = color_func(f"{op.percentage:>6.1f}%")

            print(f"{op_name} {total:<15} {avg:<15} {calls:<10} {pct:<10}")

        # Bottleneck
        if profile.bottleneck_operation:
            print()
            print(f"  {renderer.red('🔥')} {renderer.bold(profile.bottleneck_operation)} "
                  f"({profile.bottleneck_percentage:.1f}%)")

    def _render_aggregated_details(self, renderer):
        """Render aggregated profiling statistics."""
        metrics = self.profiling_metrics

        # Summary
        print(f"{renderer.bold('Scenarios:')} {metrics.total_scenarios}  |  "
              f"{renderer.bold('Ticks:')} {metrics.total_ticks_processed:,}  |  "
              f"{renderer.bold('Time:')} {metrics.total_execution_time_ms / 1000:.2f}s  |  "
              f"{renderer.bold('Avg/Tick:')} {metrics.avg_tick_time_ms:.3f}ms")
        print()

        # Bottleneck
        if metrics.most_common_bottleneck:
            freq = metrics.bottleneck_frequency[metrics.most_common_bottleneck]
            pct = (freq / metrics.total_scenarios * 100)
            print(f"{renderer.red('🔥')} {renderer.bold(metrics.most_common_bottleneck)} "
                  f"({freq}/{metrics.total_scenarios}, {pct:.0f}%)")
            print()

        # Per-operation averages
        self._render_cross_scenario_averages(renderer)

    def _render_cross_scenario_averages(self, renderer):
        """Render average operation times across all scenarios."""
        # Aggregate
        operation_totals = {}
        operation_counts = {}

        for profile in self.profiling_metrics.scenario_profiles:
            for op in profile.operations:
                name = op.operation_name
                if name not in operation_totals:
                    operation_totals[name] = 0.0
                    operation_counts[name] = 0
                operation_totals[name] += op.avg_time_ms
                operation_counts[name] += 1

        # Calculate averages
        operation_averages = []
        for name in operation_totals:
            avg = operation_totals[name] / operation_counts[name]
            operation_averages.append((name, avg))

        operation_averages.sort(key=lambda x: x[1], reverse=True)

        # Table
        print(renderer.bold("Avg Operation Times:"))
        print()

        header = f"{'Operation':<25} {'Avg/Call':<15}"
        print(renderer.bold(header))
        print("-" * 42)

        for name, avg_time in operation_averages:
            # Color
            if avg_time >= 1.0:
                color_func = renderer.red
            elif avg_time >= 0.5:
                color_func = renderer.yellow
            else:
                def color_func(x): return x

            op_name = color_func(f"{name:<25}")
            time_str = color_func(f"{avg_time:>12.3f}ms")
            print(f"{op_name} {time_str:<15}")

    def _render_bottleneck_details(self, renderer):
        """Render detailed bottleneck analysis."""
        metrics = self.profiling_metrics

        if not metrics.bottleneck_frequency:
            print("  No data")
            return

        # Sort by frequency
        sorted_bottlenecks = sorted(
            metrics.bottleneck_frequency.items(),
            key=lambda x: x[1],
            reverse=True
        )

        header = f"{'Operation':<25} {'Scenarios':<15} {'%':<10}"
        print(renderer.bold(header))
        print("-" * 52)

        for operation, count in sorted_bottlenecks:
            pct = (count / metrics.total_scenarios * 100)

            # Color
            if pct >= 50:
                color_func = renderer.red
            elif pct >= 25:
                color_func = renderer.yellow
            else:
                def color_func(x): return x

            op_name = color_func(f"{operation:<25}")
            count_str = f"{count}/{metrics.total_scenarios}"
            pct_str = color_func(f"{pct:>6.1f}%")

            print(f"{op_name} {count_str:<15} {pct_str:<10}")
