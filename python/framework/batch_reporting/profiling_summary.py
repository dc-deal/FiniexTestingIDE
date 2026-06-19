"""
FiniexTestingIDE - Profiling Summary
Performance profiling and bottleneck analysis reporting.

Thin presenter over the unified ProfilingReport model (#399): per-scenario operation timing,
inter-tick distribution, clipping, and the run-level aggregate (budget recommendation,
bottleneck frequency, cross-scenario averages) are all read from the model — no re-derivation.
"""

from python.framework.batch_reporting.abstract_batch_summary_section import AbstractBatchSummarySection
from python.framework.types.api.report_types import (
    InterTickStatsRow, ProfilingReport, ProfilingUnitRow)
from python.framework.utils.console_renderer import ConsoleRenderer


# Expected operations (strategy work — a high share here is GOOD, not a bottleneck).
_EXPECTED_OPERATIONS = {'worker_decision', 'order_execution'}


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

    def __init__(self, profiling_report: ProfilingReport):
        """
        Initialize profiling summary.

        Args:
            profiling_report: The unified profiling report (#399)
        """
        self._report = profiling_report

    def _layer_b_has_data(self) -> bool:
        """
        Returns True if at least one unit produced tick-loop profiling data.
        When Layer B (tick_loop_profiling) is off, no operations are present and
        this summary's sections are suppressed (#137).
        """
        return any(unit.operations for unit in self._report.units)

    def render_per_scenario(self, renderer: ConsoleRenderer):
        """
        Render profiling breakdown per scenario.

        Args:
            renderer: ConsoleRenderer instance
        """
        if not self._layer_b_has_data():
            return

        self._render_section_header(renderer)

        if not self._report.units:
            print("No profiling data available")
            return

        for idx, unit in enumerate(self._report.units, 1):
            # Separator between scenarios
            if idx > 1:
                print()
                renderer.print_separator(width=120, char="·")
                print()

            self._render_scenario_profile(unit, renderer)

    def render_aggregated(self, renderer: ConsoleRenderer, compact: bool = False, threshold: int = 9):
        """
        Render aggregated profiling across all scenarios.

        Args:
            renderer: ConsoleRenderer instance
            compact: If True, truncate budget warnings list to threshold entries
            threshold: Max entries to show before truncating
        """
        if not self._layer_b_has_data():
            return

        if not self._report.units:
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

        Args:
            renderer: ConsoleRenderer instance
        """
        if not self._layer_b_has_data():
            return

        print()
        renderer.section_separator()
        print(f"{renderer.bold(renderer.red('🔥 BOTTLENECK ANALYSIS'))} "
              f"{renderer.gray('(Performance Optimization Targets)')}")
        renderer.section_separator()

        self._render_bottleneck_details(renderer)
        print()

    def _render_scenario_profile(self, unit: ProfilingUnitRow, renderer: ConsoleRenderer):
        """
        Render single scenario's profiling breakdown.

        Uses smart coloring:
        - Expected operations (worker_decision, order_execution) → green/yellow
        - Infrastructure operations → yellow/red when ≥15%
        """
        # Header
        print(f"{renderer.bold('Scenario:')} {renderer.blue(unit.name)}")

        # Tick count line — with clipping info if budget was active
        clipping = unit.clipping
        if clipping and clipping.ticks_clipped > 0:
            print(f"{renderer.gray('Ticks:')} {unit.total_ticks:,} / {clipping.ticks_total:,} "
                  f"{renderer.yellow(f'(clipped: {clipping.ticks_clipped:,} = {clipping.clipping_rate_pct:.1f}%)')}  |  "
                  f"{renderer.gray('Budget:')} {clipping.budget_ms}ms  |  "
                  f"{renderer.gray('Avg/Tick:')} {unit.avg_per_tick_ms:.3f}ms  |  "
                  f"{renderer.gray('Total:')} {unit.total_ms:.2f}ms")
        else:
            print(f"{renderer.gray('Ticks:')} {unit.total_ticks:,}  |  "
                  f"{renderer.gray('Avg/Tick:')} {unit.avg_per_tick_ms:.3f}ms  |  "
                  f"{renderer.gray('Total:')} {unit.total_ms:.2f}ms")
        print()

        # Operations table
        if not unit.operations:
            print("  No data")
            return

        # Header
        header = f"{'Operation':<22} {'Total':<15} {'Avg':<15} {'Calls':<10} {'%':<10}"
        print(renderer.bold(header))
        print("-" * 75)

        # Rows
        for op in unit.operations:
            is_expected = op.operation in _EXPECTED_OPERATIONS

            if is_expected:
                # Expected operation - positive coloring
                if op.pct >= 50:
                    color_func = renderer.green  # Good - strategy is working!
                elif op.pct >= 20:
                    color_func = renderer.yellow
                else:
                    def color_func(x): return x
            else:
                # Infrastructure operation - warning at 15%+
                if op.pct >= 40:
                    color_func = renderer.red  # Critical - infrastructure too slow!
                elif op.pct >= 15:
                    color_func = renderer.yellow  # Optimize - should investigate
                else:
                    def color_func(x): return x

            op_name = color_func(f"{op.operation:<22}")
            total = f"{op.total_time_ms:>10.2f}ms"
            avg = f"{op.avg_time_ms:>10.3f}ms"
            calls = f"{op.call_count:>8,}"
            pct = color_func(f"{op.pct:>6.1f}%")

            print(f"{op_name} {total:<15} {avg:<15} {calls:<10} {pct:<10}")

        # Bottleneck
        if unit.bottleneck_operation:
            print()
            # Icon based on whether it's expected
            is_expected_bottleneck = unit.bottleneck_operation in _EXPECTED_OPERATIONS
            icon = renderer.green(
                '✅') if is_expected_bottleneck else renderer.red('🔥')
            print(f"  {icon} {renderer.bold(unit.bottleneck_operation)} "
                  f"({unit.bottleneck_pct:.1f}%)")

        # Inter-tick interval stats
        if unit.inter_tick:
            self._render_interval_stats(unit.inter_tick, renderer)

    def _render_interval_stats(
        self,
        stats: InterTickStatsRow,
        renderer: ConsoleRenderer
    ) -> None:
        """
        Render inter-tick interval distribution statistics.

        Args:
            stats: Interval statistics row
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
        gaps_info = f", gaps removed: {stats.gaps_removed}, threshold: {stats.threshold_s:.0f}s" \
            if stats.gaps_removed > 0 else ''
        print(f"  Intervals: {stats.interval_count:,}{gaps_info}")
        print(renderer.gray(
            '  Note: P5 = fastest 5% of tick arrivals. '
            'If avg processing > P5, the algorithm can\'t keep up with peak tick rate.'))

    def _render_aggregated_details(self, renderer, compact: bool = False, threshold: int = 9):
        """Render aggregated profiling statistics from the model aggregate."""
        agg = self._report.aggregate

        # Summary
        print(f"{renderer.bold('Scenarios:')} {agg.scenarios}  |  "
              f"{renderer.bold('Ticks:')} {agg.total_ticks:,}  |  "
              f"{renderer.bold('Time:')} {agg.total_time_s:.2f}s  |  "
              f"{renderer.bold('Avg/Tick:')} {agg.avg_per_tick_ms:.3f}ms")
        print()

        # Bottleneck
        if agg.most_common_bottleneck:
            mc = next((b for b in agg.bottlenecks
                       if b.operation == agg.most_common_bottleneck), None)
            if mc:
                print(f"{renderer.red('🔥')} {renderer.bold(agg.most_common_bottleneck)} "
                      f"({mc.scenario_count}/{mc.total_scenarios}, {mc.pct:.0f}%)")
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
        agg = self._report.aggregate
        has_budget_active = agg.budget_active

        warnings = [
            unit for unit in self._report.units
            if unit.inter_tick and unit.avg_per_tick_ms > unit.inter_tick.p5_ms
        ]
        has_p5 = any(unit.inter_tick for unit in self._report.units)

        # Only show per-scenario warnings when NO budget is configured.
        # When budget is active, clipping is already being simulated — warning is redundant.
        if warnings and not has_budget_active:
            warnings.sort(key=lambda u: u.avg_per_tick_ms, reverse=True)
            visible = warnings[:threshold] if compact and len(warnings) > threshold else warnings
            for unit in visible:
                print(renderer.red(
                    f"  ⚠️  BUDGET WARNING: avg tick processing ({unit.avg_per_tick_ms:.3f}ms) "
                    f"exceeds fastest 5% tick interval ({unit.inter_tick.p5_ms:.1f}ms) "
                    f"in {unit.name} — risk of clipping in live"))
            if compact and len(warnings) > threshold:
                remaining = len(warnings) - threshold
                print(renderer.red(
                    f"  ⚠️  +{remaining} more scenarios exceed budget — see log for full list"))
            print()

        if has_p5:
            print(f"  {renderer.bold('P5 range across scenarios:')} "
                  f"{agg.p5_min_ms:.1f}ms — {agg.p5_max_ms:.1f}ms")
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

    def _render_budget_recommendation(self, renderer: ConsoleRenderer) -> None:
        """
        Render tick processing budget recommendation based on measured P95 processing time.

        Args:
            renderer: ConsoleRenderer instance
        """
        agg = self._report.aggregate
        if agg.p95_processing_ms <= 0:
            return

        print(f"  {renderer.bold('💡 Tick Processing Budget Recommendation:')} ")
        print(f"     P95 processing time: {agg.p95_processing_ms:.3f}ms")
        print(f"     Suggested budget: {agg.suggested_budget_ms:.3f}ms (P95 + 10% safety margin)")
        if not agg.budget_active:
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
        agg = self._report.aggregate
        total_ticks = agg.clipping_total_ticks
        total_clipped = agg.clipping_total_clipped
        total_kept = agg.clipping_total_kept

        if total_ticks == 0:
            return

        budget_str = ', '.join(f'{b}ms' for b in agg.clipping_budgets)

        if total_clipped == 0:
            # Check if budget is below data granularity (< 1.0ms with integer-ms timestamps)
            max_budget = max(agg.clipping_budgets)
            if max_budget < 1.0:
                print(renderer.yellow(
                    f"  ⚠️  Tick Processing Budget Active ({budget_str}) — "
                    f"no ticks clipped, but budget < 1.0ms has no effect with integer-ms timestamps"))
                print(renderer.gray(
                    '     collected_msc has millisecond granularity — minimum effective budget is 1.0ms'))
            else:
                print(renderer.green(
                    f"  ✅ Tick Processing Budget Active ({budget_str}) — "
                    f"no ticks clipped ({total_ticks:,} ticks, {len(agg.clipping_budgets)} budget(s))"))
            print()
        else:
            overall_rate = total_clipped / total_ticks * 100
            print(f"  {renderer.bold('✂️  Tick Processing Budget Active:')}")
            print(f"     Budget: {budget_str}")
            print(f"     Total: {total_kept:,} / {total_ticks:,} ticks kept  |  "
                  f"Clipped: {total_clipped:,} ({overall_rate:.1f}%)")
            print()

        # Check if budget is too high (> 2× P95 processing time)
        if agg.p95_processing_ms > 0 and agg.clipping_budgets:
            max_budget = max(agg.clipping_budgets)
            if max_budget > agg.p95_processing_ms * 2:
                print(renderer.yellow(
                    f"  ⚠️  Budget ({max_budget}ms) exceeds 2× P95 processing time ({agg.p95_processing_ms:.3f}ms) "
                    f"— ticks may be clipped unnecessarily, reducing simulation accuracy"))
                print()

    def _render_cross_scenario_averages(self, renderer: ConsoleRenderer):
        """Render average operation times across all scenarios (from the model aggregate)."""
        print(renderer.bold("Avg Operation Times:"))
        print()

        header = f"{'Operation':<25} {'Avg/Call':<15}"
        print(renderer.bold(header))
        print("-" * 42)

        for op in self._report.aggregate.avg_operation_times:
            # Color
            if op.avg_time_ms >= 1.0:
                color_func = renderer.red
            elif op.avg_time_ms >= 0.5:
                color_func = renderer.yellow
            else:
                def color_func(x): return x

            op_name = color_func(f"{op.operation:<25}")
            time_str = color_func(f"{op.avg_time_ms:>12.3f}ms")
            print(f"{op_name} {time_str:<15}")

    def _render_bottleneck_details(self, renderer: ConsoleRenderer):
        """
        Render detailed bottleneck analysis from the model aggregate.

        Shows ALL operations with their bottleneck frequency + status.
        """
        bottlenecks = self._report.aggregate.bottlenecks
        if not bottlenecks:
            print("  No data")
            return

        header = f"{'Operation':<25} {'Scenarios':<15} {'%':<10} {'Status':<15}"
        print(renderer.bold(header))
        print("-" * 68)

        for row in bottlenecks:
            color_func, status = self._bottleneck_label(row.status, row.pct, renderer)

            op_name = color_func(f"{row.operation:<25}")
            count_str = f"{row.scenario_count}/{row.total_scenarios}"
            pct_str = color_func(
                f"{row.pct:>6.1f}%") if row.scenario_count > 0 else f"{row.pct:>6.1f}%"

            print(f"{op_name} {count_str:<15} {pct_str:<10} {status:<15}")

    @staticmethod
    def _bottleneck_label(status: str, pct: float, renderer: ConsoleRenderer):
        """Map a bottleneck status token to (color_func, status_label) — mirrors the console rules."""
        if status == 'none':
            return (lambda x: x), '-'
        if status == 'expected':
            color = renderer.green if pct >= 50 else renderer.yellow
            return color, '✅ Expected'
        if status == 'critical':
            return renderer.red, '⚠️  Critical'
        if status == 'optimize':
            return renderer.yellow, '⚠️  Optimize'
        return (lambda x: x), '⚠️  Review'
