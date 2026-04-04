"""
FiniexTestingIDE - Profiling Summary
Performance profiling and bottleneck analysis reporting
"""

from typing import Any, Dict, List, Optional
from python.framework.batch_reporting.abstract_batch_summary_section import AbstractBatchSummarySection
from python.framework.utils.console_renderer import ConsoleRenderer
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.performance_types.performance_metrics_types import (
    InterTickIntervalStats,
    TickLoopProfile,
    OperationProfile,
    ProfilingMetrics
)
from python.framework.types.process_data_types import ClippingStats, ProcessResult


class ProfilingSummary(AbstractBatchSummarySection):
    """
    Performance profiling and bottleneck analysis.

    Renders:
    - Per-scenario operation breakdowns
    - Timing percentages
    - Bottleneck identification
    - Cross-scenario comparison
    - Optimization recommendations
    """

    _section_title = '⚡ PROFILING ANALYSIS'

    def __init__(self, batch_execution_summary: BatchExecutionSummary, profiling_data_map: Dict[Any, Any]
                 ):
        """
        Initialize profiling summary.

        Args:
            performance_log_coordinator: Performance statistics container
        """
        self.batch_execution_summary = batch_execution_summary
        self.profiling_data_map = profiling_data_map
        self._process_results = batch_execution_summary.process_result_list

        # Build profiling metrics from scenarios
        self.profiling_metrics = self._build_profiling_metrics()

    def render_per_scenario(self, renderer: ConsoleRenderer):
        """
        Render profiling breakdown per scenario.

        Shows:
        - Operation timing table
        - Percentage breakdown
        - Bottleneck identification

        Args:
            renderer: ConsoleRenderer instance
        """
        self._render_section_header(renderer)

        if not self._process_results:
            print("No profiling data available")
            return

        for idx, scenario in enumerate(self._process_results, 1):
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

    def render_aggregated(self, renderer: ConsoleRenderer, compact: bool = False, threshold: int = 9):
        """
        Render aggregated profiling across all scenarios.

        Shows:
        - Total ticks processed
        - Average tick time
        - Most common bottleneck
        - Cross-scenario comparison

        Args:
            renderer: ConsoleRenderer instance
            compact: If True, truncate budget warnings list to threshold entries
            threshold: Max entries to show before truncating
        """
        if not self.profiling_metrics.scenario_profiles:
            print("No aggregated profiling data available")
            return

        print()
        renderer.section_separator()
        renderer.print_bold("⚡ AGGREGATED PROFILING (ALL SCENARIOS)")
        renderer.section_separator()

        self._render_aggregated_details(renderer, compact=compact, threshold=threshold)
        print()

    def render_bottleneck_analysis(self, renderer: ConsoleRenderer):
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
        ticks_processed = scenario.tick_loop_results.coordination_statistics.ticks_processed

        return TickLoopProfile(
            scenario_index=scenario.scenario_index,
            scenario_name=scenario.scenario_name,
            total_ticks=ticks_processed,
            operations=operations,
            total_time_ms=profiling.total_per_tick_ms,
            avg_time_per_tick_ms=profiling.total_per_tick_ms / ticks_processed
            if ticks_processed > 0 else 0.0,
            interval_stats=profiling.interval_stats
        )

    def _build_profiling_metrics(self) -> ProfilingMetrics:
        """Build ProfilingMetrics from all scenarios."""
        metrics = ProfilingMetrics()

        for scenario in self._process_results:
            profile = self._extract_profile_from_scenario(
                scenario)
            if profile:
                metrics.add_scenario_profile(profile)

        return metrics

    def _render_scenario_profile(self, profile: TickLoopProfile, renderer: ConsoleRenderer):
        """
        Render single scenario's profiling breakdown.

        Uses smart coloring:
        - Expected operations (worker_decision, order_execution) → green/yellow
        - Infrastructure operations → yellow/red when ≥15%
        """
        # Header
        print(f"{renderer.bold('Scenario:')} {renderer.blue(profile.scenario_name)}")

        # Tick count line — with clipping info if budget was active
        clipping = self.batch_execution_summary.clipping_stats_map.get(
            profile.scenario_index
        )
        if clipping and clipping.ticks_clipped > 0:
            print(f"{renderer.gray('Ticks:')} {profile.total_ticks:,} / {clipping.ticks_total:,} "
                  f"{renderer.yellow(f'(clipped: {clipping.ticks_clipped:,} = {clipping.clipping_rate_pct:.1f}%)')}  |  "
                  f"{renderer.gray('Budget:')} {clipping.budget_ms}ms  |  "
                  f"{renderer.gray('Avg/Tick:')} {profile.avg_time_per_tick_ms:.3f}ms  |  "
                  f"{renderer.gray('Total:')} {profile.total_time_ms:.2f}ms")
        else:
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

        # Inter-tick interval stats
        if profile.interval_stats:
            self._render_interval_stats(profile.interval_stats, renderer)

    def _render_interval_stats(
        self,
        stats: InterTickIntervalStats,
        renderer: ConsoleRenderer
    ) -> None:
        """
        Render inter-tick interval distribution statistics.

        Args:
            stats: Computed interval statistics
            renderer: ConsoleRenderer instance
        """
        print()
        print(renderer.bold('Inter-Tick Intervals (market-side time between consecutive ticks):'))
        print(f"  Min: {stats.min_ms:.1f}ms  |  "
              f"P5 (5th pctl): {stats.p5_ms:.1f}ms  |  "
              f"Median: {stats.median_ms:.1f}ms  |  "
              f"Mean: {stats.mean_ms:.1f}ms  |  "
              f"P95 (95th pctl): {stats.p95_ms:.1f}ms  |  "
              f"Max: {stats.max_ms:.1f}ms")
        gaps_info = f", gaps removed: {stats.gaps_removed}, threshold: {stats.gap_threshold_s:.0f}s" \
            if stats.gaps_removed > 0 else ''
        print(f"  Intervals: {stats.filtered_intervals:,}{gaps_info}")
        print(renderer.gray(
            '  Note: P5 = fastest 5% of tick arrivals. '
            'If avg processing > P5, the algorithm can\'t keep up with peak tick rate.'))

    def _render_aggregated_details(self, renderer, compact: bool = False, threshold: int = 9):
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

        # Budget warnings (avg processing vs fastest tick intervals)
        self._render_budget_warnings(renderer, compact=compact, threshold=threshold)

        # Per-operation averages
        self._render_cross_scenario_averages(renderer)

    def _render_budget_warnings(self, renderer: ConsoleRenderer, compact: bool = False, threshold: int = 9) -> None:
        """
        Render budget warnings when avg tick processing exceeds fastest tick intervals.

        Args:
            renderer: ConsoleRenderer instance
            compact: If True, truncate warnings list to threshold entries (sorted by severity)
            threshold: Max warnings to display before collapsing
        """
        warnings = []
        p5_values = []
        has_budget_active = bool(self.batch_execution_summary.clipping_stats_map)

        for profile in self.profiling_metrics.scenario_profiles:
            if not profile.interval_stats:
                continue
            p5_values.append(profile.interval_stats.p5_ms)
            if profile.avg_time_per_tick_ms > profile.interval_stats.p5_ms:
                warnings.append(profile)

        # Only show per-scenario warnings when NO budget is configured.
        # When budget is active, clipping is already being simulated — warning is redundant.
        if warnings and not has_budget_active:
            warnings.sort(key=lambda p: p.avg_time_per_tick_ms, reverse=True)
            visible = warnings[:threshold] if compact and len(warnings) > threshold else warnings
            for profile in visible:
                print(renderer.red(
                    f"  ⚠️  BUDGET WARNING: avg tick processing ({profile.avg_time_per_tick_ms:.3f}ms) "
                    f"exceeds fastest 5% tick interval ({profile.interval_stats.p5_ms:.1f}ms) "
                    f"in {profile.scenario_name} — risk of clipping in live"))
            if compact and len(warnings) > threshold:
                remaining = len(warnings) - threshold
                print(renderer.red(
                    f"  ⚠️  +{remaining} more scenarios exceed budget — see log for full list"))
            print()

        if p5_values:
            min_p5 = min(p5_values)
            max_p5 = max(p5_values)
            print(f"  {renderer.bold('P5 range across scenarios:')} "
                  f"{min_p5:.1f}ms — {max_p5:.1f}ms")
            print()

        has_warnings = bool(warnings)

        # Recommendation only when there's a reason (warning or active budget)
        if has_warnings or has_budget_active:
            self._render_budget_recommendation(renderer)

        # Clipping summary (only when budget was active)
        if has_budget_active:
            self._render_clipping_summary(renderer)

        # All green — no warnings, no budget active
        if not has_warnings and not has_budget_active:
            print(renderer.green(
                '  ✅ Tick processing within budget — no clipping risk detected'))
            print()

    def _get_p95_processing_ms(self) -> Optional[float]:
        """
        Calculate P95 of avg_time_per_tick across all scenarios.

        Returns:
            P95 processing time in ms, or None if no data
        """
        avg_times = [
            p.avg_time_per_tick_ms
            for p in self.profiling_metrics.scenario_profiles
            if p.avg_time_per_tick_ms > 0
        ]
        if not avg_times:
            return None
        avg_times_sorted = sorted(avg_times)
        p95_idx = min(int(len(avg_times_sorted) * 0.95), len(avg_times_sorted) - 1)
        return avg_times_sorted[p95_idx]

    def _render_budget_recommendation(self, renderer: ConsoleRenderer) -> None:
        """
        Render tick processing budget recommendation based on measured P95 processing time.

        Args:
            renderer: ConsoleRenderer instance
        """
        p95_processing = self._get_p95_processing_ms()
        if p95_processing is None:
            return

        suggested_budget = round(p95_processing * 1.1, 3)  # +10% safety margin
        has_budget_active = bool(self.batch_execution_summary.clipping_stats_map)

        print(f"  {renderer.bold('💡 Tick Processing Budget Recommendation:')} ")
        print(f"     P95 processing time: {p95_processing:.3f}ms")
        print(f"     Suggested budget: {suggested_budget:.3f}ms (P95 + 10% safety margin)")
        if not has_budget_active:
            print(renderer.gray(
                '     Set execution_config.tick_processing_budget_ms in scenario config '
                'to simulate live clipping behavior.'))
        print()

    def _render_clipping_summary(self, renderer: ConsoleRenderer) -> None:
        """
        Render aggregated clipping summary when budget filtering was active.

        Args:
            renderer: ConsoleRenderer instance
        """
        clipping_map = self.batch_execution_summary.clipping_stats_map
        if not clipping_map:
            return

        total_ticks = sum(c.ticks_total for c in clipping_map.values())
        total_clipped = sum(c.ticks_clipped for c in clipping_map.values())
        total_kept = sum(c.ticks_kept for c in clipping_map.values())

        if total_ticks == 0:
            return

        budget_values = set(c.budget_ms for c in clipping_map.values())
        budget_str = ', '.join(f'{b}ms' for b in sorted(budget_values))

        if total_clipped == 0:
            # Check if budget is below data granularity (< 1.0ms with integer-ms timestamps)
            max_budget = max(budget_values)
            if max_budget < 1.0:
                print(renderer.yellow(
                    f"  ⚠️  Tick Processing Budget Active ({budget_str}) — "
                    f"no ticks clipped, but budget < 1.0ms has no effect with integer-ms timestamps"))
                print(renderer.gray(
                    '     collected_msc has millisecond granularity — minimum effective budget is 1.0ms'))
            else:
                print(renderer.green(
                    f"  ✅ Tick Processing Budget Active ({budget_str}) — "
                    f"no ticks clipped ({total_ticks:,} ticks, {len(clipping_map)} scenarios)"))
            print()
        else:
            overall_rate = total_clipped / total_ticks * 100
            print(f"  {renderer.bold('✂️  Tick Processing Budget Active:')}")
            print(f"     Budget: {budget_str}  |  "
                  f"Scenarios: {len(clipping_map)}")
            print(f"     Total: {total_kept:,} / {total_ticks:,} ticks kept  |  "
                  f"Clipped: {total_clipped:,} ({overall_rate:.1f}%)")
            print()

        # Check if budget is too high (> 2× P95 processing time)
        p95 = self._get_p95_processing_ms()
        if p95 is not None:
            max_budget = max(budget_values)
            if max_budget > p95 * 2:
                print(renderer.yellow(
                    f"  ⚠️  Budget ({max_budget}ms) exceeds 2× P95 processing time ({p95:.3f}ms) "
                    f"— ticks may be clipped unnecessarily, reducing simulation accuracy"))
                print()

    def _render_cross_scenario_averages(self, renderer: ConsoleRenderer):
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

    def _render_bottleneck_details(self, renderer: ConsoleRenderer):
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
