"""
FiniexTestingIDE - Performance Summary
Worker and decision logic performance rendering

 
- Uses BatchExecutionSummary instead of batch_results dict
- Reads Scenario objects

FULLY TYPED: Uses BatchPerformanceStats with direct attribute access.

Renders:
- Per-scenario worker performance (call counts, timings, parallel efficiency)
- Per-scenario decision logic performance
- Aggregated performance across all scenarios
- Bottleneck analysis (worst performers)
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from python.framework.reporting.console_renderer import ConsoleRenderer
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.performance_summary_aggregation_types import AggregatedPerformanceStats, DecisionLogicBottleneckData, ParallelBottleneckData, PerformanceBottlenecks, ScenarioBottleneckData, WorkerAggregateData, WorkerBottleneckData
from python.framework.types.process_data_types import ProcessResult


class PerformanceSummary:
    """
    Worker and decision logic performance summary.


    - Uses BatchExecutionSummary for data access
    - FULLY TYPED: Direct attribute access instead of .get()
    """

    def __init__(self, batch_execution_summary: BatchExecutionSummary) -> None:
        """
        Initialize performance summary.

        Args:
            batch_execution_summary: Batch execution summary containing all scenario results
        """
        self.batch_execution_summary: BatchExecutionSummary = batch_execution_summary
        self.all_scenarios: List[ProcessResult] = batch_execution_summary.scenario_list

    def render_per_scenario(self, renderer: ConsoleRenderer) -> None:
        """
        Render performance stats per scenario.

        Args:
            renderer: ConsoleRenderer instance
        """
        if not self.all_scenarios:
            print("No performance data available")
            return

        for idx, scenario in enumerate(self.all_scenarios, 1):
            # Separator between scenarios
            if idx > 1:
                print()
                renderer.print_separator(width=120, char="·")
                print()

            self._render_scenario_performance(scenario, renderer)

    def render_aggregated(self, renderer: ConsoleRenderer) -> None:
        """
        Render aggregated performance across all scenarios.

        Args:
            renderer: ConsoleRenderer instance
        """

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

        # Analyze bottlenecks
        bottlenecks: PerformanceBottlenecks = self._analyze_bottlenecks()

        print()
        renderer.section_separator()
        print(f"{renderer.bold(renderer.red('⚠️ BOTTLENECK ANALYSIS'))} "
              f"{renderer.gray('(Worst Performers)')}")
        renderer.section_separator()

        self._render_bottleneck_details(bottlenecks, renderer)
        print()

    def _render_scenario_performance(self, scenario: ProcessResult, renderer: ConsoleRenderer) -> None:
        """
        Render performance for single scenario.

        Args:
            scenario: Scenario result to render
            renderer: ConsoleRenderer instance
        """
        if not scenario.tick_loop_results:
            return

        renderer.section_separator()

        # Access BatchPerformanceStats directly
        tick_loop_results = scenario.tick_loop_results
        batch_stats = tick_loop_results.performance_stats
        ticks_processed = batch_stats.ticks_processed
        parallel_mode = batch_stats.parallel_mode

        total_workers = batch_stats.total_workers
        total_calls = batch_stats.total_worker_calls

        # Decision logic stats
        decision_stats = batch_stats.decision_logic
        decisions_made = decision_stats.decision_count if decision_stats else 0

        # Header
        mode_str = renderer.green(
            "Parallel") if parallel_mode else renderer.yellow("Sequential")
        print(f"{renderer.bold('📊 SCENARIO PERFORMANCE:')} {scenario.scenario_name}")
        print(f"{renderer.bold('   Workers:')} {total_workers} workers ({mode_str})  |  "
              f"Ticks: {ticks_processed:,}  |  "
              f"Calls: {total_calls:,}  |  "
              f"Decisions: {decisions_made}")

        # Per-worker details
        if batch_stats.workers:
            print(f"\n{renderer.bold('   📊 WORKER DETAILS:')}")

            for worker_name, worker_perf in batch_stats.workers.items():
                call_count = worker_perf.worker_call_count
                avg_time = worker_perf.worker_avg_time_ms
                min_time = worker_perf.worker_min_time_ms
                max_time = worker_perf.worker_max_time_ms
                total_time = worker_perf.worker_total_time_ms

                print(f"      {renderer.blue(f'{worker_name:15}')}  "
                      f"Calls: {call_count:>5}  |  "
                      f"Avg: {avg_time:>6.3f}ms  |  "
                      f"Range: {min_time:>6.3f}-{max_time:>6.3f}ms  |  "
                      f"Total: {total_time:>8.2f}ms")

        # Parallel efficiency
        if parallel_mode:
            time_saved = batch_stats.parallel_time_saved_ms
            avg_saved = batch_stats.parallel_avg_saved_per_tick_ms
            status = batch_stats.parallel_status

            print(f"\n{renderer.bold('   ⚡ PARALLEL EFFICIENCY:')}")
            print(f"      Time saved: {time_saved:>8.2f}ms total  |  "
                  f"Avg/tick: {avg_saved:>6.3f}ms  |  "
                  f"Status: {status}")

        # Decision logic
        if decision_stats:
            logic_name = decision_stats.logic_name
            logic_type = decision_stats.logic_type
            avg_time = decision_stats.decision_avg_time_ms
            min_time = decision_stats.decision_min_time_ms
            max_time = decision_stats.decision_max_time_ms
            total_time = decision_stats.decision_total_time_ms

            print(
                f"\n{renderer.bold('   🧠 DECISION LOGIC:')} {logic_name} ({logic_type})")
            print(f"      Decisions: {decisions_made}  |  "
                  f"Avg: {avg_time:>6.3f}ms  |  "
                  f"Range: {min_time:>6.3f}-{max_time:>6.3f}ms  |  "
                  f"Total: {total_time:>8.2f}ms")

        print()

    def _aggregate_performance_stats(self) -> AggregatedPerformanceStats:
        """
        Aggregate performance statistics across all scenarios.

        Returns:
            Aggregated performance statistics
        """
        aggregated = AggregatedPerformanceStats()

        for scenario in self.all_scenarios:
            if not scenario.tick_loop_results:
                continue

            performance_stats = scenario.tick_loop_results.performance_stats
            # Basic stats
            aggregated.total_ticks += performance_stats.ticks_processed

            if performance_stats.decision_logic:
                aggregated.total_signals += performance_stats.decision_logic.decision_count
                aggregated.total_decisions += performance_stats.decision_logic.decision_count

            # Worker stats
            for worker_name, worker_perf in performance_stats.workers.items():
                if worker_name not in aggregated.worker_aggregates:
                    aggregated.worker_aggregates[worker_name] = WorkerAggregateData(
                    )

                worker_agg = aggregated.worker_aggregates[worker_name]
                worker_agg.calls += worker_perf.worker_call_count
                worker_agg.total_time += worker_perf.worker_total_time_ms
                worker_agg.times.append(worker_perf.worker_avg_time_ms)

            # Decision logic
            if performance_stats.decision_logic:
                decision_agg = aggregated.decision_aggregates
                decision_agg.calls += performance_stats.decision_logic.decision_count
                decision_agg.total_time += performance_stats.decision_logic.decision_total_time_ms
                decision_agg.times.append(
                    performance_stats.decision_logic.decision_avg_time_ms)

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
        Analyze performance bottlenecks across all scenarios.

        Returns:
            Performance bottleneck analysis
        """
        bottlenecks = PerformanceBottlenecks()

        slowest_scenario_time: float = 0.0
        worker_times: Dict[str, List[Tuple[str, float]]] = {}
        decision_logic_times: Dict[str, List[Tuple[str, float]]] = {}
        worst_parallel_saved: float = float('inf')

        for scenario in self.all_scenarios:
            if not scenario.tick_loop_results:
                continue

            performance_stats = scenario.tick_loop_results.performance_stats
            scenario_name = scenario.scenario_name
            ticks = performance_stats.ticks_processed

            # Calculate scenario avg time per tick
            total_worker_time = sum(
                w.worker_total_time_ms for w in performance_stats.workers.values())
            total_decision_time = performance_stats.decision_logic.decision_total_time_ms if performance_stats.decision_logic else 0.0

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
            for worker_name, worker_perf in performance_stats.workers.items():
                avg_time = worker_perf.worker_avg_time_ms
                if worker_name not in worker_times:
                    worker_times[worker_name] = []
                worker_times[worker_name].append((scenario_name, avg_time))

            # Collect decision logic times
            if performance_stats.decision_logic:
                logic_name = performance_stats.decision_logic.logic_name
                avg_time = performance_stats.decision_logic.decision_avg_time_ms
                if logic_name not in decision_logic_times:
                    decision_logic_times[logic_name] = []
                decision_logic_times[logic_name].append(
                    (scenario_name, avg_time))

            # Check parallel efficiency
            if performance_stats.parallel_mode:
                time_saved = performance_stats.parallel_time_saved_ms
                if time_saved < worst_parallel_saved:
                    worst_parallel_saved = time_saved
                    bottlenecks.worst_parallel = ParallelBottleneckData(
                        name=scenario_name,
                        time_saved=time_saved,
                        status=performance_stats.parallel_status
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
