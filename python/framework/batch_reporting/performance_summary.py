"""
FiniexTestingIDE - Performance Summary
Worker and decision logic performance rendering.

Thin presenter over the unified WorkerDecisionReport model (#398/#399 3d): per-worker timing,
decision-logic timing, parallel efficiency, the cross-scenario aggregate, and the worst-performer
bottleneck analysis are all read from the model — the single worker/decision source (the
per-scenario worker list was removed from the worker-decision breakdown to avoid the duplicate).

Renders:
- Per-scenario worker performance (call counts, timings, parallel efficiency)
- Per-scenario decision logic performance
- Aggregated performance across all scenarios
- Bottleneck analysis (worst performers)
"""

from typing import Dict, List, Tuple

from python.framework.batch_reporting.abstract_batch_summary_section import AbstractBatchSummarySection
from python.framework.utils.console_renderer import ConsoleRenderer
from python.framework.types.api.report_types import WorkerDecisionReport, WorkerDecisionUnitRow
from python.framework.types.performance_types.performance_summary_aggregation_types import AggregatedPerformanceStats, DecisionLogicBottleneckData, ParallelBottleneckData, PerformanceBottlenecks, ScenarioBottleneckData, WorkerAggregateData, WorkerBottleneckData


class PerformanceSummary(AbstractBatchSummarySection):
    """
    Worker and decision logic performance summary — model-fed from `WorkerDecisionReport`.
    """

    _section_title = '📊 PERFORMANCE DETAILS (PER SCENARIO)'

    def __init__(self, worker_decision_report: WorkerDecisionReport) -> None:
        """
        Initialize performance summary.

        Args:
            worker_decision_report: The unified worker/decision report (#398)
        """
        self._units: List[WorkerDecisionUnitRow] = worker_decision_report.units

    def _layer_a_has_data(self) -> bool:
        """
        Returns True if at least one unit produced per-worker statistics.
        When Layer A (worker_decision_tracking) is off, all units have empty
        worker lists and this summary's sections are suppressed (#137).
        """
        return any(unit.workers for unit in self._units)

    def render_per_scenario(self, renderer: ConsoleRenderer):
        """
        Render per-scenario worker / decision performance.

        Args:
            renderer: ConsoleRenderer instance
        """
        if not self._layer_a_has_data():
            return

        self._render_section_header(renderer)

        if not self._units:
            print("No performance data available")
            return

        for idx, unit in enumerate(self._units, 1):
            # Separator between scenarios
            if idx > 1:
                print()
                renderer.print_separator(width=120, char="·")
                print()

            self._render_scenario_performance(unit, renderer)

    def render_aggregated(self, renderer: ConsoleRenderer) -> None:
        """
        Render aggregated performance across all scenarios.

        Args:
            renderer: ConsoleRenderer instance
        """
        if not self._layer_a_has_data():
            return

        # Aggregate statistics
        aggregated: AggregatedPerformanceStats = self._aggregate_performance_stats()

        print()
        renderer.section_separator()
        renderer.print_bold("📊 AGGREGATED SUMMARY (ALL SCENARIOS)")
        renderer.section_separator()

        self._render_aggregated_details(aggregated, renderer)
        print()

    def render_bottleneck_analysis(self, renderer: ConsoleRenderer) -> None:
        """
        Render bottleneck analysis - worst performers.

        Args:
            renderer: ConsoleRenderer instance
        """
        if not self._layer_a_has_data():
            return

        # Analyze bottlenecks
        bottlenecks: PerformanceBottlenecks = self._analyze_bottlenecks()

        print()
        renderer.section_separator()
        print(f"{renderer.bold(renderer.red('⚠️ BOTTLENECK ANALYSIS'))} "
              f"{renderer.gray('(Worst Performers)')}")
        renderer.section_separator()

        self._render_bottleneck_details(bottlenecks, renderer)
        print()

    def _render_scenario_performance(self, unit: WorkerDecisionUnitRow, renderer: ConsoleRenderer) -> None:
        """
        Render performance for single scenario (unit).

        Args:
            unit: The worker/decision unit row
            renderer: ConsoleRenderer instance
        """
        renderer.section_separator()

        # Header
        parallel_workers = unit.parallel_workers
        ticks_processed = unit.ticks_processed
        total_workers = len(unit.workers)
        decision_count = unit.decision_count
        total_calls = sum(w.call_count for w in unit.workers)

        mode_str = renderer.green(
            "Parallel") if parallel_workers else renderer.yellow("Sequential")
        print(f"{renderer.bold('📊 SCENARIO PERFORMANCE:')} {unit.name}")
        print(f"{renderer.bold('   Workers:')} {total_workers} workers ({mode_str})  |  "
              f"Ticks: {ticks_processed:,}  |  "
              f"Calls: {total_calls:,}  |  "
              f"Decisions: {decision_count}")

        # Per-worker details
        if unit.workers:
            print(f"\n{renderer.bold('   📊 WORKER DETAILS:')}")

            for w in unit.workers:
                print(f"      {renderer.blue(f'{w.worker_name:15}->{w.worker_type:15}')}  "
                      f"Calls: {w.call_count:>5}  |  "
                      f"Avg: {w.avg_time_ms:>6.3f}ms  |  "
                      f"Range: {w.min_time_ms:>6.3f}-{w.max_time_ms:>6.3f}ms  |  "
                      f"Total: {w.total_time_ms:>8.2f}ms")

        # Parallel efficiency
        if parallel_workers and ticks_processed > 0:
            parallel_time_saved_ms = unit.parallel_time_saved_ms
            parallel_avg_saved_per_tick_ms = parallel_time_saved_ms / ticks_processed
            status = self._get_parallel_status(parallel_time_saved_ms)

            print(f"\n{renderer.bold('   ⚡ PARALLEL EFFICIENCY:')}")
            print(f"      Time saved: {parallel_time_saved_ms:>8.2f}ms total  |  "
                  f"Avg/tick: {parallel_avg_saved_per_tick_ms:>6.3f}ms  |  "
                  f"Status: {status}")

        # Decision logic
        if unit.decision_logic_name or unit.decision_count:
            print(
                f"\n{renderer.bold('   🧠 DECISION LOGIC:')} {unit.decision_logic_name} ({unit.decision_logic_type})")
            print(f"      Decisions: {unit.decision_count}  |  "
                  f"Avg: {unit.decision_avg_time_ms:>6.3f}ms  |  "
                  f"Range: {unit.decision_min_time_ms:>6.3f}-{unit.decision_max_time_ms:>6.3f}ms  |  "
                  f"Total: {unit.decision_total_time_ms:>8.2f}ms")

        print()

    def _aggregate_performance_stats(self) -> AggregatedPerformanceStats:
        """
        Aggregate performance statistics across all units.

        Returns:
            Aggregated performance statistics
        """
        aggregated = AggregatedPerformanceStats()

        for unit in self._units:
            # Basic stats
            aggregated.total_ticks += unit.ticks_processed
            aggregated.total_signals += unit.decision_count
            aggregated.total_decisions += unit.decision_count

            # Worker stats
            for w in unit.workers:
                if w.worker_name not in aggregated.worker_aggregates:
                    aggregated.worker_aggregates[w.worker_name] = WorkerAggregateData()

                worker_agg = aggregated.worker_aggregates[w.worker_name]
                worker_agg.calls += w.call_count
                worker_agg.total_time += w.total_time_ms
                worker_agg.times.append(w.avg_time_ms)

            # Decision logic
            decision_agg = aggregated.decision_aggregates
            decision_agg.calls += unit.decision_count
            decision_agg.total_time += unit.decision_total_time_ms
            decision_agg.times.append(unit.decision_avg_time_ms)

        return aggregated

    def _render_aggregated_details(
        self,
        aggregated: AggregatedPerformanceStats,
        renderer: ConsoleRenderer
    ) -> None:
        """
        Render detailed aggregated performance stats.

        Args:
            aggregated: Aggregated performance statistics
            renderer: ConsoleRenderer instance
        """
        print()
        print(f"{renderer.bold('   📊 AGGREGATED STATS:')}")
        print(f"      Total Ticks: {aggregated.total_ticks:,}  |  "
              f"Total Signals: {aggregated.total_signals:,}  |  "
              f"Total Decisions: {aggregated.total_decisions:,}")

        # Worker aggregates
        if aggregated.worker_aggregates:
            print(f"\n{renderer.bold('   👷 WORKERS (AGGREGATED):')}")

            for worker_name, agg in aggregated.worker_aggregates.items():
                total_calls = agg.calls
                total_time = agg.total_time
                avg_time = total_time / total_calls if total_calls > 0 else 0.0
                scenario_avg = sum(agg.times) / \
                    len(agg.times) if agg.times else 0.0

                print(f"      {renderer.blue(f'{worker_name:15}')}  "
                      f"Total Calls: {total_calls:>6}  |  "
                      f"Total Time: {total_time:>8.2f}ms  |  "
                      f"Avg: {avg_time:>6.3f}ms  |  "
                      f"Scenario Avg: {scenario_avg:>6.3f}ms")

        # Decision logic aggregate
        decision_agg = aggregated.decision_aggregates
        if decision_agg.calls > 0:
            total_calls = decision_agg.calls
            total_time = decision_agg.total_time
            avg_time = total_time / total_calls if total_calls > 0 else 0.0
            scenario_avg = sum(decision_agg.times) / \
                len(decision_agg.times) if decision_agg.times else 0.0

            print(f"\n{renderer.bold('   🧠 DECISION LOGIC (AGGREGATED):')}")
            print(f"      Total Decisions: {total_calls}  |  "
                  f"Total Time: {total_time:>8.2f}ms  |  "
                  f"Avg: {avg_time:>6.3f}ms  |  "
                  f"Scenario Avg: {scenario_avg:>6.3f}ms")

    def _analyze_bottlenecks(self) -> PerformanceBottlenecks:
        """
        Analyze performance bottlenecks across all units.

        Returns:
            Performance bottleneck analysis
        """
        bottlenecks = PerformanceBottlenecks()

        slowest_scenario_time: float = 0.0
        worker_times: Dict[str, List[Tuple[str, float]]] = {}
        decision_logic_times: Dict[str, List[Tuple[str, float]]] = {}
        worst_parallel_saved: float = float('inf')

        for unit in self._units:
            scenario_name = unit.name
            ticks = unit.ticks_processed

            # Calculate scenario avg time per tick
            total_worker_time = sum(w.total_time_ms for w in unit.workers)
            total_decision_time = unit.decision_total_time_ms

            total_time = total_worker_time + total_decision_time
            avg_time_per_tick = total_time / ticks if ticks > 0 else 0.0

            if avg_time_per_tick > slowest_scenario_time:
                slowest_scenario_time = avg_time_per_tick
                bottlenecks.slowest_scenario = ScenarioBottleneckData(
                    name=scenario_name,
                    avg_time_per_tick=avg_time_per_tick,
                    total_time=total_time
                )

            # Collect worker times
            for w in unit.workers:
                if w.worker_name not in worker_times:
                    worker_times[w.worker_name] = []
                worker_times[w.worker_name].append((scenario_name, w.avg_time_ms))

            # Collect decision logic times
            if unit.decision_logic_name:
                if unit.decision_logic_name not in decision_logic_times:
                    decision_logic_times[unit.decision_logic_name] = []
                decision_logic_times[unit.decision_logic_name].append(
                    (scenario_name, unit.decision_avg_time_ms))

            # Check parallel efficiency
            if unit.parallel_workers:
                time_saved = unit.parallel_time_saved_ms
                if time_saved < worst_parallel_saved:
                    worst_parallel_saved = time_saved
                    bottlenecks.worst_parallel = ParallelBottleneckData(
                        name=scenario_name,
                        time_saved=time_saved,
                        status=self._get_parallel_status(time_saved)
                    )

        # Find worst worker
        worst_worker_avg: float = 0.0
        for worker_name, times in worker_times.items():
            avg_time = sum(t[1] for t in times) / len(times) if times else 0.0
            if avg_time > worst_worker_avg:
                worst_worker_avg = avg_time
                bottlenecks.slowest_worker = WorkerBottleneckData(
                    name=worker_name,
                    avg_time=avg_time,
                    scenarios=times
                )

        # Find worst decision logic
        worst_decision_avg: float = 0.0
        for logic_name, times in decision_logic_times.items():
            avg_time = sum(t[1] for t in times) / len(times) if times else 0.0
            if avg_time > worst_decision_avg:
                worst_decision_avg = avg_time
                bottlenecks.slowest_decision_logic = DecisionLogicBottleneckData(
                    name=logic_name,
                    avg_time=avg_time,
                    scenarios=times
                )

        return bottlenecks

    def _render_bottleneck_details(
        self,
        bottlenecks: PerformanceBottlenecks,
        renderer: ConsoleRenderer
    ) -> None:
        """
        Render bottleneck analysis details.

        Args:
            bottlenecks: Bottleneck analysis data
            renderer: ConsoleRenderer instance
        """
        print()

        # Slowest scenario
        if bottlenecks.slowest_scenario:
            scenario = bottlenecks.slowest_scenario
            print(f"{renderer.bold('   🌶 SLOWEST SCENARIO:')}")
            avg_str = renderer.red(f"{scenario.avg_time_per_tick:.3f}ms")
            print(f"      {renderer.red(scenario.name)}  |  "
                  f"Avg/tick: {avg_str}  |  "
                  f"Total: {scenario.total_time:.2f}ms")
            print(
                f"      {renderer.yellow('→ This scenario took the longest time per tick')}")

        # Slowest worker
        if bottlenecks.slowest_worker:
            worker = bottlenecks.slowest_worker
            print(f"\n{renderer.bold('   🌶 SLOWEST WORKER:')}")
            avg_str = renderer.red(f"{worker.avg_time:.3f}ms")
            print(f"      {renderer.red(worker.name)}  |  "
                  f"Avg: {avg_str} (across all scenarios)")

            worst_scenario = max(worker.scenarios, key=lambda x: x[1])
            worst_msg = f"→ Worst in scenario '{worst_scenario[0]}': {worst_scenario[1]:.3f}ms"
            print(f"      {renderer.yellow(worst_msg)}")

        # Slowest decision logic
        if bottlenecks.slowest_decision_logic and bottlenecks.slowest_decision_logic.avg_time > 0.5:
            logic = bottlenecks.slowest_decision_logic
            print(f"\n{renderer.bold('   🌶 SLOWEST DECISION LOGIC:')}")
            avg_str = renderer.red(f"{logic.avg_time:.3f}ms")
            print(f"      {renderer.red(logic.name)}  |  "
                  f"Avg: {avg_str} (across all scenarios)")
            print(
                f"      {renderer.yellow('→ Consider optimizing decision logic if > 1ms')}")

        # Worst parallel efficiency
        if bottlenecks.worst_parallel and bottlenecks.worst_parallel.time_saved < 0:
            parallel = bottlenecks.worst_parallel
            print(f"\n{renderer.bold('   🌶 WORST PARALLEL EFFICIENCY:')}")
            time_saved_str = renderer.red(f"{parallel.time_saved:.2f}ms")
            print(f"      {renderer.red(parallel.name)}  |  "
                  f"Time saved: {time_saved_str}  |  "
                  f"Status: {parallel.status}")
            print(
                f"      {renderer.yellow('→ Parallel execution slower than sequential! Consider disabling.')}")

        # Recommendations
        print(f"\n{renderer.bold('   💡 RECOMMENDATIONS:')}")

        has_issues: bool = False
        if bottlenecks.slowest_worker and bottlenecks.slowest_worker.avg_time > 1.0:
            worker_name = renderer.yellow(bottlenecks.slowest_worker.name)
            print(f"      • Optimize {worker_name} worker (slowest component)")
            has_issues = True

        if bottlenecks.slowest_decision_logic and bottlenecks.slowest_decision_logic.avg_time > 1.0:
            logic_name = renderer.yellow(
                bottlenecks.slowest_decision_logic.name)
            print(f"      • Optimize {logic_name} decision logic")
            has_issues = True

        if bottlenecks.worst_parallel and bottlenecks.worst_parallel.time_saved < 0:
            print(f"      • Disable parallel workers for better performance")
            has_issues = True

        if not has_issues:
            print(
                f"      {renderer.green('✅ All components performing well! No major bottlenecks detected.')}")

        print()

    def _get_parallel_status(self, time_saved_ms: float) -> str:
        """
        Determine parallel execution status.

        Args:
            time_saved_ms: Total time saved

        Returns:
            Status string
        """
        if time_saved_ms > 0.01:
            return "✅ Faster"
        elif time_saved_ms < -0.01:
            return "⚠️ Slower"
        else:
            return "≈ Equal"
