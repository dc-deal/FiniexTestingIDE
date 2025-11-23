"""
FiniexTestingIDE - Profiling Summary
Performance profiling and bottleneck analysis reporting
"""

from typing import Any, Dict, List, Optional
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.performance_metrics_types import (
    TickLoopProfile,
    OperationProfile,
    ProfilingMetrics
)
from python.framework.types.process_data_types import ProcessResult


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

    def __init__(self, batch_execution_summary: BatchExecutionSummary, profiling_data_map: Dict[Any, Any]
                 ):
        """
        Initialize profiling summary.

        Args:
            performance_log_coordinator: Performance statistics container
        """
        self.batch_execution_summary = batch_execution_summary
        self.profiling_data_map = profiling_data_map
        self.all_scenarios = batch_execution_summary.scenario_list

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
        self, scenario: ProcessResult
    ) -> Optional[TickLoopProfile]:
        """
        Direct typed access to profiling_data.
        No more nested dict navigation!

        Args:
            scenario: ScenarioPerformanceStats object

        Returns:
            TickLoopProfile or None if no profiling data
        """
        # Build operation profiles from typed data
        operations = []

        if not self.profiling_data_map:
            return None

        profiling = self.profiling_data_map.get(scenario.scenario_index)
        if not profiling:
            return None

        for op_name, timing in profiling.operations.items():
            # Calculate percentage
            percentage = (timing.total_time_ms / profiling.total_per_tick_ms * 100) \
                if profiling.total_per_tick_ms > 0 else 0.0

            operations.append(OperationProfile(
                operation_name=op_name,
                total_time_ms=timing.total_time_ms,  # Direct property access!
                call_count=timing.call_count,  # Direct property access!
                avg_time_ms=timing.avg_time_ms,  # Property from OperationTiming!
                percentage=percentage
            ))

        # Sort by percentage (highest first)
        operations.sort(key=lambda op: op.percentage, reverse=True)
        ticks_processed = scenario.tick_loop_results.performance_stats.ticks_processed

        return TickLoopProfile(
            scenario_index=scenario.scenario_index,
            scenario_name=scenario.scenario_name,
            total_ticks=ticks_processed,
            operations=operations,
            total_time_ms=profiling.total_per_tick_ms,
            avg_time_per_tick_ms=profiling.total_per_tick_ms / ticks_processed
            if ticks_processed > 0 else 0.0
        )

    def _build_profiling_metrics(self) -> ProfilingMetrics:
        """Build ProfilingMetrics from all scenarios."""
        metrics = ProfilingMetrics()

        for scenario in self.all_scenarios:
            profile = self._extract_profile_from_scenario(
                scenario)
            if profile:
                metrics.add_scenario_profile(profile)

        return metrics

    def _render_scenario_profile(self, profile: TickLoopProfile, renderer):
        """
        Render single scenario's profiling breakdown.

        Uses smart coloring:
        - Expected operations (worker_decision, order_execution) → green/yellow
        - Infrastructure operations → yellow/red when ≥15%
        """
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

        # Expected operations (strategy work - high % is GOOD!)
        EXPECTED_OPERATIONS = {'worker_decision', 'order_execution'}

        # Header
        header = f"{'Operation':<22} {'Total':<15} {'Avg':<15} {'Calls':<10} {'%':<10}"
        print(renderer.bold(header))
        print("-" * 75)

        # Rows
        for op in profile.operations:
            is_expected = op.operation_name in EXPECTED_OPERATIONS

            if is_expected:
                # Expected operation - positive coloring
                if op.percentage >= 50:
                    color_func = renderer.green  # Good - strategy is working!
                elif op.percentage >= 20:
                    color_func = renderer.yellow
                else:
                    def color_func(x): return x
            else:
                # Infrastructure operation - warning at 15%+
                if op.percentage >= 40:
                    color_func = renderer.red  # Critical - infrastructure too slow!
                elif op.percentage >= 15:
                    color_func = renderer.yellow  # Optimize - should investigate
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
            # Icon based on whether it's expected
            is_expected_bottleneck = profile.bottleneck_operation in EXPECTED_OPERATIONS
            icon = renderer.green(
                '✅') if is_expected_bottleneck else renderer.red('🔥')
            print(f"  {icon} {renderer.bold(profile.bottleneck_operation)} "
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
        """
        Render detailed bottleneck analysis.

        Shows ALL operations with their bottleneck frequency.
        Distinguishes between:
        - Expected bottlenecks (strategy work) - marked with ✅
        - Problematic bottlenecks (infrastructure) - marked with ⚠️
        - No bottleneck (0%) - marked with -
        """
        metrics = self.profiling_metrics

        if not self.profiling_metrics.scenario_profiles:
            print("  No data")
            return

        # Expected bottlenecks (strategy work - this is GOOD!)
        EXPECTED_BOTTLENECKS = {
            'worker_decision',  # Strategy logic execution
            'order_execution'   # Active trading (with caution)
        }

        # Collect ALL operations from all scenarios
        all_operations = set()
        for profile in self.profiling_metrics.scenario_profiles:
            for op in profile.operations:
                all_operations.add(op.operation_name)

        # Build stats for each operation
        operation_stats = {}
        for op_name in all_operations:
            bottleneck_count = metrics.bottleneck_frequency.get(op_name, 0)
            operation_stats[op_name] = bottleneck_count

        # Sort by frequency (highest first), then alphabetically
        sorted_operations = sorted(
            operation_stats.items(),
            # Negative for descending, then alphabetical
            key=lambda x: (-x[1], x[0])
        )

        header = f"{'Operation':<25} {'Scenarios':<15} {'%':<10} {'Status':<15}"
        print(renderer.bold(header))
        print("-" * 68)

        for operation, count in sorted_operations:
            pct = (count / metrics.total_scenarios *
                   100) if metrics.total_scenarios > 0 else 0.0

            # Determine if expected or problematic
            is_expected = operation in EXPECTED_BOTTLENECKS

            if count == 0:
                # No bottleneck - neutral
                def color_func(x): return x
                status = "-"
            elif is_expected:
                # Expected bottleneck - positive coloring
                if pct >= 50:
                    color_func = renderer.green  # Good - strategy is working!
                else:
                    color_func = renderer.yellow
                status = "✅ Expected"
            else:
                # Problematic bottleneck - infrastructure should be fast
                # 15%+ is worth investigating for infrastructure
                if pct >= 40:
                    color_func = renderer.red
                    status = "⚠️  Critical"
                elif pct >= 15:
                    color_func = renderer.yellow
                    status = "⚠️  Optimize"
                else:
                    def color_func(x): return x
                    status = "⚠️  Review"

            op_name = color_func(f"{operation:<25}")
            count_str = f"{count}/{metrics.total_scenarios}"
            pct_str = color_func(
                f"{pct:>6.1f}%") if count > 0 else f"{pct:>6.1f}%"

            print(f"{op_name} {count_str:<15} {pct_str:<10} {status:<15}")
