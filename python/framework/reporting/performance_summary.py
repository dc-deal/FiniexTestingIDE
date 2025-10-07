"""
FiniexTestingIDE - Performance Summary
Worker and decision logic performance rendering

REFACTORED (C#003):
- Uses ScenarioSetPerformanceManager instead of batch_results dict
- Reads ScenarioPerformanceStats objects

Renders:
- Per-scenario worker performance (call counts, timings, parallel efficiency)
- Per-scenario decision logic performance
- Aggregated performance across all scenarios
- Bottleneck analysis (worst performers)
"""

from python.framework.reporting.scenario_set_performance_manager import ScenarioSetPerformanceManager
from typing import Any, Dict, List


class PerformanceSummary:
    """
    Worker and decision logic performance summary.

    REFACTORED (C#003):
    - Uses ScenarioSetPerformanceManager for data access
    """

    def __init__(self, performance_log: ScenarioSetPerformanceManager):
        """
        Initialize performance summary.

        Args:
            performance_log: Performance statistics container
        """
        self.performance_log = performance_log
        self.all_scenarios = performance_log.get_all_scenarios()

    def render_per_scenario(self, renderer):
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

    def render_aggregated(self, renderer):
        """
        Render aggregated performance across all scenarios.

        Args:
            renderer: ConsoleRenderer instance
        """

        # Aggregate statistics
        aggregated = self._aggregate_performance_stats()

        print()
        renderer.section_separator(width=120)
        renderer.print_bold("📊 AGGREGATED SUMMARY (ALL SCENARIOS)")
        renderer.section_separator(width=120)

        self._render_aggregated_details(aggregated, renderer)
        print()

    def render_bottleneck_analysis(self, renderer):
        """
        Render bottleneck analysis - worst performers.

        Args:
            renderer: ConsoleRenderer instance
        """

        # Analyze bottlenecks
        bottlenecks = self._analyze_bottlenecks()

        print()
        renderer.section_separator(width=120)
        print(f"{renderer.bold(renderer.red('⚠️  BOTTLENECK ANALYSIS'))} "
              f"{renderer.gray('(Worst Performers)')}")
        renderer.section_separator(width=120)

        self._render_bottleneck_details(bottlenecks, renderer)
        print()

    def _render_scenario_performance(self, scenario, renderer):
        """Render performance for single scenario."""
        renderer.section_separator(width=120)

        stats = scenario.worker_statistics
        ticks_processed = scenario.ticks_processed
        parallel_mode = stats.get('parallel_mode', False)

        worker_stats = stats.get('worker_statistics', {})
        total_workers = worker_stats.get('total_workers', 0)
        total_calls = worker_stats.get('total_calls', 0)

        decision_stats = stats.get('decision_logic_statistics', {})
        decisions_made = decision_stats.get('decision_count', 0)

        # Header
        mode_str = renderer.green(
            "Parallel") if parallel_mode else renderer.yellow("Sequential")
        print(f"{renderer.bold('📊 SCENARIO PERFORMANCE:')} {scenario.scenario_name}")
        print(f"{renderer.bold('   Workers:')} {total_workers} workers ({mode_str})  |  "
              f"Ticks: {ticks_processed:,}  |  "
              f"Calls: {total_calls:,}  |  "
              f"Decisions: {decisions_made}")

        # Per-worker details
        if 'workers' in worker_stats:
            print(f"\n{renderer.bold('   📊 WORKER DETAILS:')}")

            workers = worker_stats['workers']
            for worker_name, worker_perf in workers.items():
                call_count = worker_perf.get('call_count', 0)
                avg_time = worker_perf.get('avg_time_ms', 0)
                min_time = worker_perf.get('min_time_ms', 0)
                max_time = worker_perf.get('max_time_ms', 0)
                total_time = worker_perf.get('total_time_ms', 0)

                print(f"      {renderer.blue(f'{worker_name:15}')}  "
                      f"Calls: {call_count:>5}  |  "
                      f"Avg: {avg_time:>6.3f}ms  |  "
                      f"Range: {min_time:>6.3f}-{max_time:>6.3f}ms  |  "
                      f"Total: {total_time:>8.2f}ms")

        # Parallel efficiency
        if parallel_mode and 'parallel_stats' in worker_stats:
            pstats = worker_stats['parallel_stats']
            time_saved = pstats.get('total_time_saved_ms', 0)
            avg_saved = pstats.get('avg_saved_per_tick_ms', 0)
            status = pstats.get('status', 'N/A')

            print(f"\n{renderer.bold('   ⚡ PARALLEL EFFICIENCY:')}")
            print(f"      Time saved: {time_saved:>8.2f}ms total  |  "
                  f"Avg/tick: {avg_saved:>6.3f}ms  |  "
                  f"Status: {status}")

        # Decision logic
        if decision_stats:
            logic_name = decision_stats.get('decision_logic_name', 'Unknown')
            logic_type = decision_stats.get('decision_logic_type', 'Unknown')
            avg_time = decision_stats.get('avg_time_ms', 0)
            min_time = decision_stats.get('min_time_ms', 0)
            max_time = decision_stats.get('max_time_ms', 0)
            total_time = decision_stats.get('total_time_ms', 0)

            print(
                f"\n{renderer.bold('   🧠 DECISION LOGIC:')} {logic_name} ({logic_type})")
            print(f"      Decisions: {decisions_made}  |  "
                  f"Avg: {avg_time:>6.3f}ms  |  "
                  f"Range: {min_time:>6.3f}-{max_time:>6.3f}ms  |  "
                  f"Total: {total_time:>8.2f}ms")

        print()

    def _aggregate_performance_stats(self):
        """Aggregate performance statistics across all scenarios."""
        aggregated = {
            'total_ticks': 0,
            'total_decisions': 0,
            'total_signals': 0,
            'worker_aggregates': {},
            'decision_aggregates': {'calls': 0, 'total_time': 0, 'times': []},
        }

        for scenario in self.all_scenarios:
            # Basic stats
            aggregated['total_ticks'] += scenario.ticks_processed
            aggregated['total_signals'] += scenario.signals_generated

            # Worker stats
            stats = scenario.worker_statistics
            aggregated['total_decisions'] += stats.get(
                'decision_logic_statistics', {}).get('decision_count', 0)

            # Per-worker aggregation
            workers = stats.get('worker_statistics', {}).get('workers', {})
            for worker_name, worker_perf in workers.items():
                if worker_name not in aggregated['worker_aggregates']:
                    aggregated['worker_aggregates'][worker_name] = {
                        'calls': 0,
                        'total_time': 0,
                        'times': []
                    }

                aggregated['worker_aggregates'][worker_name]['calls'] += worker_perf.get(
                    'call_count', 0)
                aggregated['worker_aggregates'][worker_name]['total_time'] += worker_perf.get(
                    'total_time_ms', 0)

                avg_time = worker_perf.get('avg_time_ms', 0)
                if avg_time > 0:
                    aggregated['worker_aggregates'][worker_name]['times'].append(
                        avg_time)

            # Decision logic aggregation
            decision_stats = stats.get('decision_logic_statistics', {})
            aggregated['decision_aggregates']['calls'] += decision_stats.get(
                'decision_count', 0)
            aggregated['decision_aggregates']['total_time'] += decision_stats.get(
                'total_time_ms', 0)

            avg_time = decision_stats.get('avg_time_ms', 0)
            if avg_time > 0:
                aggregated['decision_aggregates']['times'].append(avg_time)

        return aggregated

    def _render_aggregated_details(self, aggregated: Dict[str, Any], renderer):
        """Render aggregated performance details."""
        # Overall summary
        print(f"\n{renderer.bold('   📈 OVERALL:')}")
        print(f"      Total Ticks: {aggregated['total_ticks']:,}  |  "
              f"Total Signals: {aggregated['total_signals']:,}  |  "
              f"Total Decisions: {aggregated['total_decisions']:,}")

        # Worker aggregates
        if aggregated['worker_aggregates']:
            print(f"\n{renderer.bold('   👷 WORKERS (AGGREGATED):')}")

            for worker_name, agg in aggregated['worker_aggregates'].items():
                total_calls = agg['calls']
                total_time = agg['total_time']
                avg_time = total_time / total_calls if total_calls > 0 else 0
                scenario_avg = sum(agg['times']) / \
                    len(agg['times']) if agg['times'] else 0

                print(f"      {renderer.blue(f'{worker_name:15}')}  "
                      f"Total Calls: {total_calls:>6}  |  "
                      f"Total Time: {total_time:>8.2f}ms  |  "
                      f"Avg: {avg_time:>6.3f}ms  |  "
                      f"Scenario Avg: {scenario_avg:>6.3f}ms")

        # Decision logic aggregate
        decision_agg = aggregated['decision_aggregates']
        if decision_agg['calls'] > 0:
            total_calls = decision_agg['calls']
            total_time = decision_agg['total_time']
            avg_time = total_time / total_calls if total_calls > 0 else 0
            scenario_avg = sum(
                decision_agg['times']) / len(decision_agg['times']) if decision_agg['times'] else 0

            print(f"\n{renderer.bold('   🧠 DECISION LOGIC (AGGREGATED):')}")
            print(f"      Total Decisions: {total_calls}  |  "
                  f"Total Time: {total_time:>8.2f}ms  |  "
                  f"Avg: {avg_time:>6.3f}ms  |  "
                  f"Scenario Avg: {scenario_avg:>6.3f}ms")

    def _analyze_bottlenecks(self):
        """Analyze performance bottlenecks across all scenarios."""
        bottlenecks = {
            'slowest_scenario': None,
            'slowest_worker': None,
            'slowest_decision_logic': None,
            'worst_parallel': None
        }

        slowest_scenario_time = 0
        worker_times = {}
        decision_logic_times = {}
        worst_parallel_saved = float('inf')

        for scenario in self.all_scenarios:
            scenario_name = scenario.scenario_name
            ticks = scenario.ticks_processed
            stats = scenario.worker_statistics

            # Calculate scenario avg time per tick
            total_worker_time = 0
            workers = stats.get('worker_statistics', {}).get('workers', {})
            for worker_perf in workers.values():
                total_worker_time += worker_perf.get('total_time_ms', 0)

            decision_stats = stats.get('decision_logic_statistics', {})
            total_decision_time = decision_stats.get('total_time_ms', 0)

            total_time = total_worker_time + total_decision_time
            avg_time_per_tick = total_time / ticks if ticks > 0 else 0

            if avg_time_per_tick > slowest_scenario_time:
                slowest_scenario_time = avg_time_per_tick
                bottlenecks['slowest_scenario'] = {
                    'name': scenario_name,
                    'avg_time_per_tick': avg_time_per_tick,
                    'total_time': total_time
                }

            # Collect worker times
            for worker_name, worker_perf in workers.items():
                avg_time = worker_perf.get('avg_time_ms', 0)
                if worker_name not in worker_times:
                    worker_times[worker_name] = []
                worker_times[worker_name].append((scenario_name, avg_time))

            # Collect decision logic times
            logic_name = decision_stats.get('decision_logic_name', 'Unknown')
            avg_time = decision_stats.get('avg_time_ms', 0)
            if logic_name not in decision_logic_times:
                decision_logic_times[logic_name] = []
            decision_logic_times[logic_name].append((scenario_name, avg_time))

            # Check parallel efficiency
            if 'parallel_stats' in stats.get('worker_statistics', {}):
                pstats = stats['worker_statistics']['parallel_stats']
                time_saved = pstats.get('total_time_saved_ms', 0)
                if time_saved < worst_parallel_saved:
                    worst_parallel_saved = time_saved
                    bottlenecks['worst_parallel'] = {
                        'name': scenario_name,
                        'time_saved': time_saved,
                        'status': pstats.get('status', 'N/A')
                    }

        # Find worst worker
        worst_worker_avg = 0
        for worker_name, times in worker_times.items():
            avg_time = sum(t[1] for t in times) / len(times) if times else 0
            if avg_time > worst_worker_avg:
                worst_worker_avg = avg_time
                bottlenecks['slowest_worker'] = {
                    'name': worker_name,
                    'avg_time': avg_time,
                    'scenarios': times
                }

        # Find worst decision logic
        worst_decision_avg = 0
        for logic_name, times in decision_logic_times.items():
            avg_time = sum(t[1] for t in times) / len(times) if times else 0
            if avg_time > worst_decision_avg:
                worst_decision_avg = avg_time
                bottlenecks['slowest_decision_logic'] = {
                    'name': logic_name,
                    'avg_time': avg_time,
                    'scenarios': times
                }

        return bottlenecks

    def _render_bottleneck_details(self, bottlenecks: Dict[str, Any], renderer):
        """Render bottleneck analysis details."""
        print()

        # Slowest scenario
        if bottlenecks['slowest_scenario']:
            scenario = bottlenecks['slowest_scenario']
            print(f"{renderer.bold('   🐌 SLOWEST SCENARIO:')}")
            avg_str = renderer.red(f"{scenario['avg_time_per_tick']:.3f}ms")
            print(f"      {renderer.red(scenario['name'])}  |  "
                  f"Avg/tick: {avg_str}  |  "
                  f"Total: {scenario['total_time']:.2f}ms")
            print(
                f"      {renderer.yellow('→ This scenario took the longest time per tick')}")

        # Slowest worker
        if bottlenecks['slowest_worker']:
            worker = bottlenecks['slowest_worker']
            print(f"\n{renderer.bold('   🐌 SLOWEST WORKER:')}")
            avg_str = renderer.red(f"{worker['avg_time']:.3f}ms")
            print(f"      {renderer.red(worker['name'])}  |  "
                  f"Avg: {avg_str} (across all scenarios)")

            worst_scenario = max(worker['scenarios'], key=lambda x: x[1])
            worst_msg = f"→ Worst in scenario '{worst_scenario[0]}': {worst_scenario[1]:.3f}ms"
            print(f"      {renderer.yellow(worst_msg)}")

        # Slowest decision logic
        if bottlenecks['slowest_decision_logic'] and bottlenecks['slowest_decision_logic']['avg_time'] > 0.5:
            logic = bottlenecks['slowest_decision_logic']
            print(f"\n{renderer.bold('   🐌 SLOWEST DECISION LOGIC:')}")
            avg_str = renderer.red(f"{logic['avg_time']:.3f}ms")
            print(f"      {renderer.red(logic['name'])}  |  "
                  f"Avg: {avg_str} (across all scenarios)")
            print(
                f"      {renderer.yellow('→ Consider optimizing decision logic if > 1ms')}")

        # Worst parallel efficiency
        if bottlenecks['worst_parallel'] and bottlenecks['worst_parallel']['time_saved'] < 0:
            parallel = bottlenecks['worst_parallel']
            print(f"\n{renderer.bold('   🐌 WORST PARALLEL EFFICIENCY:')}")
            time_saved_str = renderer.red(f"{parallel['time_saved']:.2f}ms")
            print(f"      {renderer.red(parallel['name'])}  |  "
                  f"Time saved: {time_saved_str}  |  "
                  f"Status: {parallel['status']}")
            print(
                f"      {renderer.yellow('→ Parallel execution slower than sequential! Consider disabling.')}")

        # Recommendations
        print(f"\n{renderer.bold('   💡 RECOMMENDATIONS:')}")

        has_issues = False
        if bottlenecks['slowest_worker'] and bottlenecks['slowest_worker']['avg_time'] > 1.0:
            worker_name = renderer.yellow(
                bottlenecks['slowest_worker']['name'])
            print(f"      • Optimize {worker_name} worker (slowest component)")
            has_issues = True

        if bottlenecks['slowest_decision_logic'] and bottlenecks['slowest_decision_logic']['avg_time'] > 1.0:
            logic_name = renderer.yellow(
                bottlenecks['slowest_decision_logic']['name'])
            print(f"      • Optimize {logic_name} decision logic")
            has_issues = True

        if bottlenecks['worst_parallel'] and bottlenecks['worst_parallel']['time_saved'] < 0:
            print(f"      • Disable parallel workers for better performance")
            has_issues = True

        if not has_issues:
            print(
                f"      {renderer.green('✅ All components performing well! No major bottlenecks detected.')}")

        print()
