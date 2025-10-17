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
        print(f"{renderer.gray('Total Ticks:')} {profile.total_ticks:,}  |  "
              f"{renderer.gray('Avg per Tick:')} {profile.avg_time_per_tick_ms:.3f}ms  |  "
              f"{renderer.gray('Total Time:')} {profile.total_time_ms:.2f}ms")
        print()

        # Operations table
        if not profile.operations:
            print("  No operation data available")
            return

        # Table header
        header = f"{'Operation':<22} {'Total Time':<15} {'Avg Time':<15} {'Calls':<10} {'% of Total':<12}"
        print(renderer.bold(header))
        print("-" * 80)

        # Table rows
        for op in profile.operations:
            # Color-code based on percentage
            if op.percentage >= 40:
                color_func = renderer.red
            elif op.percentage >= 20:
                color_func = renderer.yellow
            else:
                def color_func(x): return x  # No color

            op_name = color_func(f"{op.operation_name:<22}")
            total_time = f"{op.total_time_ms:>10.2f}ms"
            avg_time = f"{op.avg_time_ms:>10.3f}ms"
            calls = f"{op.call_count:>8,}"
            percentage = color_func(f"{op.percentage:>8.1f}%")

            print(
                f"{op_name} {total_time:<15} {avg_time:<15} {calls:<10} {percentage:<12}")

        # Bottleneck highlight
        if profile.bottleneck_operation:
            print()
            print(f"  {renderer.red('🔥 Bottleneck:')} "
                  f"{renderer.bold(profile.bottleneck_operation)} "
                  f"({profile.bottleneck_percentage:.1f}%)")

    def _render_aggregated_details(self, renderer):
        """Render aggregated profiling statistics."""
        metrics = self.profiling_metrics

        # Summary stats
        print(f"{renderer.bold('Total Scenarios:')} {metrics.total_scenarios}")
        print(
            f"{renderer.bold('Total Ticks Processed:')} {metrics.total_ticks_processed:,}")
        print(
            f"{renderer.bold('Total Execution Time:')} {metrics.total_execution_time_ms / 1000:.2f}s")
        print(f"{renderer.bold('Average Tick Time:')} {metrics.avg_tick_time_ms:.3f}ms")
        print()

        # Most common bottleneck
        if metrics.most_common_bottleneck:
            frequency = metrics.bottleneck_frequency[metrics.most_common_bottleneck]
            percentage = (frequency / metrics.total_scenarios * 100)

            print(f"{renderer.red('🔥 Most Common Bottleneck:')} "
                  f"{renderer.bold(metrics.most_common_bottleneck)} "
                  f"({frequency}/{metrics.total_scenarios} scenarios, {percentage:.0f}%)")
            print()

        # Per-operation averages across all scenarios
        self._render_cross_scenario_averages(renderer)

    def _render_cross_scenario_averages(self, renderer):
        """Render average operation times across all scenarios."""
        # Aggregate operation stats
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

        # Sort by average time (highest first)
        operation_averages.sort(key=lambda x: x[1], reverse=True)

        # Render table
        print(renderer.bold("Average Operation Times (across all scenarios):"))
        print()

        header = f"{'Operation':<25} {'Avg Time per Call':<20}"
        print(renderer.bold(header))
        print("-" * 50)

        for name, avg_time in operation_averages:
            # Color-code based on time
            if avg_time >= 1.0:
                color_func = renderer.red
            elif avg_time >= 0.5:
                color_func = renderer.yellow
            else:
                def color_func(x): return x  # No color

            op_name = color_func(f"{name:<25}")
            time_str = color_func(f"{avg_time:>15.3f}ms")

            print(f"{op_name} {time_str:<20}")

    def _render_bottleneck_details(self, renderer):
        """Render detailed bottleneck analysis."""
        metrics = self.profiling_metrics

        if not metrics.bottleneck_frequency:
            print("  No bottleneck data available")
            return

        # Bottleneck frequency table
        print(renderer.bold("Bottleneck Frequency:"))
        print()

        # Sort by frequency
        sorted_bottlenecks = sorted(
            metrics.bottleneck_frequency.items(),
            key=lambda x: x[1],
            reverse=True
        )

        header = f"{'Operation':<25} {'Scenarios':<15} {'Percentage':<15}"
        print(renderer.bold(header))
        print("-" * 60)

        for operation, count in sorted_bottlenecks:
            percentage = (count / metrics.total_scenarios * 100)

            # Color-code based on frequency
            if percentage >= 50:
                color_func = renderer.red
            elif percentage >= 25:
                color_func = renderer.yellow
            else:
                def color_func(x): return x  # No color

            op_name = color_func(f"{operation:<25}")
            count_str = f"{count}/{metrics.total_scenarios}"
            percentage_str = color_func(f"{percentage:>10.1f}%")

            print(f"{op_name} {count_str:<15} {percentage_str:<15}")

        print()

        # Optimization recommendations
        self._render_optimization_recommendations(renderer)

    def _render_optimization_recommendations(self, renderer):
        """Render optimization recommendations based on bottlenecks."""
        metrics = self.profiling_metrics

        if not metrics.most_common_bottleneck:
            return

        print(renderer.bold(renderer.yellow("💡 Optimization Recommendations:")))
        print()

        bottleneck = metrics.most_common_bottleneck
        recommendations = self._get_recommendations_for_bottleneck(bottleneck)

        for idx, rec in enumerate(recommendations, 1):
            print(f"  {idx}. {rec}")

    def _get_recommendations_for_bottleneck(self, bottleneck: str) -> List[str]:
        """Get optimization recommendations for a specific bottleneck."""
        recommendations = {
            'worker_decision': [
                "Consider reducing indicator calculation complexity",
                "Cache repeated calculations within decision logic",
                "Use parallel worker execution if not already enabled",
                "Profile individual worker performance to identify slow workers"
            ],
            'bar_rendering': [
                "Consider reducing number of timeframes",
                "Optimize bar aggregation logic",
                "Check if synthetic bar generation is necessary",
                "Review bar storage efficiency"
            ],
            'bar_history': [
                "Optimize bar history retrieval (use views instead of copies)",
                "Consider reducing lookback period if possible",
                "Check if all timeframes are actually needed",
                "Profile memory access patterns"
            ],
            'order_execution': [
                "Simplify order validation logic",
                "Reduce logging verbosity in order execution",
                "Check if order execution is overly complex",
                "Consider batching order updates"
            ],
            'trade_simulator': [
                "Optimize price update logic",
                "Reduce position management overhead",
                "Check if portfolio calculations can be cached",
                "Profile individual simulator methods"
            ],
            'stats_update': [
                "Reduce stats update frequency (already at 100 ticks)",
                "Defer non-critical stat calculations",
                "Consider async stats updates",
                "Profile stat calculation methods"
            ]
        }

        return recommendations.get(
            bottleneck,
            ["Profile this operation in detail to identify specific bottlenecks"]
        )
