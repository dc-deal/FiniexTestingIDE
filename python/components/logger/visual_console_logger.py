"""
VisualConsoleLogger - Colorful, compact logging output
Preparation for future TUI (Terminal User Interface)

PHASE 2 (V0.7): Enhanced Performance Visualization
- New performance structure with per-worker details
- Decision logic performance tracking
- Parameter override warnings
- Batch vs. Scenario parallelism clarity

HOTFIX: Use AppConfigLoader for correct batch mode detection
"""

import logging
import sys
from datetime import datetime
from typing import Optional
from python.config import AppConfigLoader


class ColorCodes:
    """ANSI Color Codes"""
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    GRAY = '\033[90m'
    BOLD = '\033[1m'
    RESET = '\033[0m'


class VisualConsoleLogger:
    """
    Custom Logger with:
    - Colored log levels (ERROR=Red, WARNING=Yellow, INFO=Blue, DEBUG=Gray)
    - Compact class names (instead of fully qualified)
    - Relative time display (ms since start)
    - Log section grouping
    - Terminal-optimized (~60 lines)
    """

    def __init__(self, name: str = "FiniexTestingIDE", terminal_height: int = 60):
        self.name = name
        self.terminal_height = terminal_height
        self.start_time = datetime.now()
        self.log_buffer = []

        # Logging Setup
        self._setup_custom_logger()

        # FIXED (V0.7): Get batch mode from self.app_config.json!
        self.app_config = AppConfigLoader()

    def _setup_custom_logger(self):
        """Configure Python logging with custom formatter"""
        # Custom Formatter
        formatter = VisualLogFormatter(self.start_time)

        # Console Handler
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)

        # Root Logger configuration
        root_logger = logging.getLogger()
        root_logger.handlers.clear()  # Remove old handlers
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.INFO)

    def info(self, message: str, logger_name: Optional[str] = None):
        """Log INFO message"""
        logger = logging.getLogger(logger_name or self.name)
        logger.info(message)

    def warning(self, message: str, logger_name: Optional[str] = None):
        """Log WARNING message"""
        logger = logging.getLogger(logger_name or self.name)
        logger.warning(message)

    def error(self, message: str, logger_name: Optional[str] = None):
        """Log ERROR message"""
        logger = logging.getLogger(logger_name or self.name)
        logger.error(message)

    def debug(self, message: str, logger_name: Optional[str] = None):
        """Log DEBUG message"""
        # Use AppConfigLoader instead of DEBUG_LOGGING
        if not self.app_config.get_debug_logging:
            return
        logger = logging.getLogger(logger_name or self.name)
        logger.debug(message)

    def section_header(self, title: str, width: int = 60, char: str = "="):
        """Output section header"""
        print(f"\n{ColorCodes.BOLD}{char * width}{ColorCodes.RESET}")
        print(f"{ColorCodes.BOLD}{title.center(width)}{ColorCodes.RESET}")
        print(f"{ColorCodes.BOLD}{char * width}{ColorCodes.RESET}")

    def section_separator(self, width: int = 60, char: str = "-"):
        """Output section separator"""
        print(f"{char * width}")

    def print_results_table(self, scenario_set_summary: dict, app_config: AppConfigLoader):
        """
        Output final results table with color formatting
        Compact with scenarios side-by-side (grid layout)

        PHASE 2 (V0.7): Enhanced with new performance structure
        FIXED: Show ALL scenario performance stats + aggregation
        HOTFIX: Use AppConfigLoader for correct batch mode detection
        """
        self.section_header("🎉 EXECUTION RESULTS")

        # Main statistics (compact in 2 columns)
        # FIXED: Correct keys from BatchOrchestrator
        success = scenario_set_summary.get('success', False)
        scenarios_count = scenario_set_summary.get('scenarios_count', 0)
        exec_time = scenario_set_summary.get('execution_time', 0)

        batch_parallel_mode = app_config.get_default_parallel_scenarios()
        batch_max_parallel_scenarios = app_config.get_default_max_parallel_scenarios()

        # Additional heuristic: Check if actual execution was parallel
        if scenarios_count > 1:
            individual_times = sum(r.get('execution_time', 0)
                                   for r in scenario_set_summary.get('scenario_results', []))
            if individual_times > 0 and exec_time < individual_times * 0.9:
                batch_parallel_mode = True

        print(f"{ColorCodes.GREEN}✅ Success: {success}{ColorCodes.RESET}  |  "
              f"{ColorCodes.BLUE}📊 Scenarios: {scenarios_count}{ColorCodes.RESET}  |  "
              f"{ColorCodes.BLUE}⏱️  Time: {exec_time:.2f}s{ColorCodes.RESET}")

        # NEW (V0.7): Show batch-level execution mode from app_config
        batch_mode_str = f"{ColorCodes.GREEN}Parallel{ColorCodes.RESET}" if batch_parallel_mode else f"{ColorCodes.YELLOW}Sequential{ColorCodes.RESET}"
        print(
            f"{ColorCodes.BOLD}⚙️  Batch Mode:{ColorCodes.RESET} {batch_mode_str}", end="")
        if batch_parallel_mode and scenarios_count > 1:
            print(
                f" ({min(batch_max_parallel_scenarios, scenarios_count)} scenarios concurrent)")
        else:
            print()

        # Scenario Details (as Grid)
        if "results" in scenario_set_summary and len(scenario_set_summary["results"]) > 0:
            self.section_separator()
            print(f"{ColorCodes.BOLD}SCENARIO DETAILS{ColorCodes.RESET}")
            self.section_separator()

            self._print_scenario_grid(scenario_set_summary["results"])

        # FIXED (V0.7): Show performance for ALL scenarios, not just last one
        if 'scenario_results' in scenario_set_summary and len(scenario_set_summary['scenario_results']) > 0:
            self.section_separator(width=120)
            print(
                f"{ColorCodes.BOLD}📊 PERFORMANCE DETAILS (PER SCENARIO){ColorCodes.RESET}")
            self.section_separator(width=120)

            for idx, scenario_result in enumerate(scenario_set_summary['scenario_results'], 1):
                if 'worker_statistics' in scenario_result:
                    scenario_name = scenario_result.get(
                        'scenario_set_name', f'Scenario_{idx}')

                    # Print separator between scenarios
                    if idx > 1:
                        print()
                        self.section_separator(width=120, char="·")
                        print()

                    self._print_worker_statistics(
                        scenario_result['worker_statistics'],
                        scenario_name=scenario_name
                    )

        # NEW (V0.7): Aggregated summary across all scenarios
        if 'scenario_results' in scenario_set_summary and len(scenario_set_summary['scenario_results']) > 1:
            self._print_aggregated_summary(
                scenario_set_summary['scenario_results'])

        # NEW (V0.7): Bottleneck analysis - worst performers
        if 'scenario_results' in scenario_set_summary and len(scenario_set_summary['scenario_results']) > 0:
            self._print_bottleneck_analysis(
                scenario_set_summary['scenario_results'])

        print("=" * 120)

    def _print_scenario_grid(self, scenarios: list, columns: int = 3):
        """
        Output scenarios as grid (side-by-side)
        FIXED: String lengths without ANSI codes for correct alignment
        """
        box_width = 38

        for i in range(0, len(scenarios), columns):
            row_scenarios = scenarios[i:i+columns]

            # Create lines for each box
            lines = [[] for _ in range(8)]

            for scenario in row_scenarios:
                scenario_set_name = scenario.get(
                    'scenario_set_name', 'Unknown')[:28]
                symbol = scenario.get('symbol', 'N/A')
                ticks = scenario.get('ticks_processed', 0)
                signals = scenario.get('signals_generated', 0)
                rate = scenario.get('signal_rate', 0)

                # Worker stats - NEW (V0.7): from new structure
                worker_calls = 0
                decisions = 0
                if 'worker_statistics' in scenario:
                    stats = scenario['worker_statistics']
                    worker_calls = stats.get('total_calls', 0)
                    decisions = stats.get('decision_logic_statistics', {}).get(
                        'decision_count', 0)

                # String formatting with exact length (without ANSI in calculation!)
                def pad_line(text: str, width: int = 36) -> str:
                    """Pad line to exact width, truncate if too long"""
                    if len(text) > width:
                        return text[:width]
                    return text + ' ' * (width - len(text))

                # Create lines (exact width)
                line1_text = f"📋 {scenario_set_name}"
                line2_text = f"Symbol: {symbol}"
                line3_text = f"Ticks: {ticks:,}"
                line4_text = f"Signals: {signals} ({rate:.1%})"
                line5_text = f"Calls: {worker_calls:,}"
                line6_text = f"Decisions: {decisions}"

                # Box with exact padding
                lines[0].append(f"┌{'─' * (box_width-2)}┐")
                lines[1].append(f"│ {pad_line(line1_text)} │")
                lines[2].append(f"│ {pad_line(line2_text)} │")
                lines[3].append(f"│ {pad_line(line3_text)} │")
                lines[4].append(f"│ {pad_line(line4_text)} │")
                lines[5].append(f"│ {pad_line(line5_text)} │")
                lines[6].append(f"│ {pad_line(line6_text)} │")
                lines[7].append(f"└{'─' * (box_width-2)}┘")

            # Output
            for line_parts in lines:
                print("  ".join(line_parts))

            print()  # Empty line between rows

    def _print_worker_statistics(self, stats: dict, scenario_name: str = "Unknown"):
        """
        Worker statistics compact side-by-side

        PHASE 2 (V0.7): Complete rewrite with new performance structure
        Shows:
        - Scenario-level overview
        - Per-worker performance (min/max/avg)
        - Decision logic performance
        - Parallel efficiency
        """
        self.section_separator(width=120)

        # Basic statistics
        ticks_processed = stats.get('ticks_processed', 0)
        parallel_mode = stats.get('parallel_mode', False)

        # Worker statistics
        worker_stats = stats.get('worker_statistics', {})
        total_workers = worker_stats.get('total_workers', 0)
        total_calls = worker_stats.get('total_calls', 0)

        # Decision logic statistics
        decision_stats = stats.get('decision_logic_statistics', {})
        decisions_made = decision_stats.get('decision_count', 0)

        # Header line
        mode_str = f"({ColorCodes.GREEN}Parallel{ColorCodes.RESET})" if parallel_mode else f"({ColorCodes.YELLOW}Sequential{ColorCodes.RESET})"
        print(
            f"{ColorCodes.BOLD}📊 SCENARIO PERFORMANCE:{ColorCodes.RESET} {scenario_name}")
        print(f"{ColorCodes.BOLD}   Workers:{ColorCodes.RESET} {total_workers} workers {mode_str}  |  "
              f"Ticks: {ticks_processed:,}  |  "
              f"Calls: {total_calls:,}  |  "
              f"Decisions: {decisions_made}")

        # Per-worker performance details
        if 'workers' in worker_stats:
            print(f"\n{ColorCodes.BOLD}   📊 WORKER DETAILS:{ColorCodes.RESET}")

            workers = worker_stats['workers']
            for worker_name, worker_perf in workers.items():
                call_count = worker_perf.get('call_count', 0)
                avg_time = worker_perf.get('avg_time_ms', 0)
                min_time = worker_perf.get('min_time_ms', 0)
                max_time = worker_perf.get('max_time_ms', 0)
                total_time = worker_perf.get('total_time_ms', 0)

                # Format output
                print(f"      {ColorCodes.BLUE}{worker_name:15}{ColorCodes.RESET}  "
                      f"Calls: {call_count:>5}  |  "
                      f"Avg: {avg_time:>6.3f}ms  |  "
                      f"Range: {min_time:>6.3f}-{max_time:>6.3f}ms  |  "
                      f"Total: {total_time:>8.2f}ms")

        # Parallel execution statistics
        if parallel_mode and 'parallel_stats' in worker_stats:
            pstats = worker_stats['parallel_stats']
            time_saved = pstats.get('total_time_saved_ms', 0)
            avg_saved = pstats.get('avg_saved_per_tick_ms', 0)
            status = pstats.get('status', 'N/A')

            print(f"\n{ColorCodes.BOLD}   ⚡ PARALLEL EFFICIENCY:{ColorCodes.RESET}")
            print(f"      Time saved: {time_saved:>8.2f}ms total  |  "
                  f"Avg/tick: {avg_saved:>6.3f}ms  |  "
                  f"Status: {status}")

        # Decision logic performance
        if decision_stats:
            logic_name = decision_stats.get('decision_logic_name', 'Unknown')
            logic_type = decision_stats.get('decision_logic_type', 'Unknown')
            avg_time = decision_stats.get('avg_time_ms', 0)
            min_time = decision_stats.get('min_time_ms', 0)
            max_time = decision_stats.get('max_time_ms', 0)
            total_time = decision_stats.get('total_time_ms', 0)

            print(
                f"\n{ColorCodes.BOLD}   🧠 DECISION LOGIC:{ColorCodes.RESET} {logic_name} ({logic_type})")
            print(f"      Decisions: {decisions_made}  |  "
                  f"Avg: {avg_time:>6.3f}ms  |  "
                  f"Range: {min_time:>6.3f}-{max_time:>6.3f}ms  |  "
                  f"Total: {total_time:>8.2f}ms")

        print()  # Empty line after stats

    def _print_aggregated_summary(self, scenario_results: list):
        """
        Print aggregated summary across all scenarios.

        NEW (V0.7): Shows combined performance metrics

        Args:
            scenario_results: List of scenario result dicts
        """
        print()
        self.section_separator(width=120)
        print(
            f"{ColorCodes.BOLD}📊 AGGREGATED SUMMARY (ALL SCENARIOS){ColorCodes.RESET}")
        self.section_separator(width=120)

        # Aggregate basic stats
        total_ticks = 0
        total_decisions = 0
        total_signals = 0
        total_worker_calls = 0

        # Aggregate worker performance
        worker_aggregates = {}  # {worker_name: {calls, total_time, times_list}}
        decision_aggregates = {'calls': 0, 'total_time': 0, 'times': []}

        for scenario in scenario_results:
            # Basic stats
            total_ticks += scenario.get('ticks_processed', 0)
            total_signals += scenario.get('signals_generated', 0)

            # Worker stats
            if 'worker_statistics' in scenario:
                stats = scenario['worker_statistics']
                total_decisions += stats.get('decision_logic_statistics',
                                             {}).get('decision_count', 0)

                # Per-worker aggregation
                workers = stats.get('worker_statistics', {}).get('workers', {})
                for worker_name, worker_perf in workers.items():
                    if worker_name not in worker_aggregates:
                        worker_aggregates[worker_name] = {
                            'calls': 0,
                            'total_time': 0,
                            'times': []
                        }

                    worker_aggregates[worker_name]['calls'] += worker_perf.get(
                        'call_count', 0)
                    worker_aggregates[worker_name]['total_time'] += worker_perf.get(
                        'total_time_ms', 0)
                    # Store individual call times if available
                    avg_time = worker_perf.get('avg_time_ms', 0)
                    if avg_time > 0:
                        worker_aggregates[worker_name]['times'].append(
                            avg_time)

                # Decision logic aggregation
                decision_stats = stats.get('decision_logic_statistics', {})
                decision_aggregates['calls'] += decision_stats.get(
                    'decision_count', 0)
                decision_aggregates['total_time'] += decision_stats.get(
                    'total_time_ms', 0)
                avg_time = decision_stats.get('avg_time_ms', 0)
                if avg_time > 0:
                    decision_aggregates['times'].append(avg_time)

        # Display aggregated stats
        print(f"\n{ColorCodes.BOLD}   📈 OVERALL:{ColorCodes.RESET}")
        print(f"      Total Ticks: {total_ticks:,}  |  "
              f"Total Signals: {total_signals:,}  |  "
              f"Total Decisions: {total_decisions:,}")

        # Worker aggregates
        if worker_aggregates:
            print(
                f"\n{ColorCodes.BOLD}   👷 WORKERS (AGGREGATED):{ColorCodes.RESET}")
            for worker_name, agg in worker_aggregates.items():
                total_calls = agg['calls']
                total_time = agg['total_time']
                avg_time = total_time / total_calls if total_calls > 0 else 0

                # Calculate avg of averages across scenarios
                scenario_avg = sum(agg['times']) / \
                    len(agg['times']) if agg['times'] else 0

                print(f"      {ColorCodes.BLUE}{worker_name:15}{ColorCodes.RESET}  "
                      f"Total Calls: {total_calls:>6}  |  "
                      f"Total Time: {total_time:>8.2f}ms  |  "
                      f"Avg: {avg_time:>6.3f}ms  |  "
                      f"Scenario Avg: {scenario_avg:>6.3f}ms")

        # Decision logic aggregate
        if decision_aggregates['calls'] > 0:
            total_calls = decision_aggregates['calls']
            total_time = decision_aggregates['total_time']
            avg_time = total_time / total_calls if total_calls > 0 else 0
            scenario_avg = sum(decision_aggregates['times']) / len(
                decision_aggregates['times']) if decision_aggregates['times'] else 0

            print(
                f"\n{ColorCodes.BOLD}   🧠 DECISION LOGIC (AGGREGATED):{ColorCodes.RESET}")
            print(f"      Total Decisions: {total_calls}  |  "
                  f"Total Time: {total_time:>8.2f}ms  |  "
                  f"Avg: {avg_time:>6.3f}ms  |  "
                  f"Scenario Avg: {scenario_avg:>6.3f}ms")

        print()

    def _print_bottleneck_analysis(self, scenario_results: list):
        """
        Print bottleneck analysis - worst performers.

        NEW (V0.7): Identifies performance bottlenecks automatically

        Args:
            scenario_results: List of scenario result dicts
        """
        print()
        self.section_separator(width=120)
        print(f"{ColorCodes.BOLD}{ColorCodes.RED}⚠️  BOTTLENECK ANALYSIS{ColorCodes.RESET} {ColorCodes.GRAY}(Worst Performers){ColorCodes.RESET}")
        self.section_separator(width=120)

        # Find worst scenario (slowest avg time per tick)
        slowest_scenario = None
        slowest_scenario_time = 0

        # Find worst worker (slowest avg time across all scenarios)
        worker_times = {}  # {worker_name: [(scenario_name, avg_time_ms)]}

        # Find worst decision logic
        # {logic_name: [(scenario_name, avg_time_ms)]}
        decision_logic_times = {}

        # Find worst parallel efficiency
        worst_parallel = None
        worst_parallel_saved = float('inf')

        for scenario in scenario_results:
            scenario_name = scenario.get('scenario_set_name', 'Unknown')
            ticks = scenario.get('ticks_processed', 0)

            if 'worker_statistics' in scenario:
                stats = scenario['worker_statistics']

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
                    slowest_scenario = {
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
                logic_name = decision_stats.get(
                    'decision_logic_name', 'Unknown')
                avg_time = decision_stats.get('avg_time_ms', 0)
                if logic_name not in decision_logic_times:
                    decision_logic_times[logic_name] = []
                decision_logic_times[logic_name].append(
                    (scenario_name, avg_time))

                # Check parallel efficiency
                if 'parallel_stats' in stats.get('worker_statistics', {}):
                    pstats = stats['worker_statistics']['parallel_stats']
                    time_saved = pstats.get('total_time_saved_ms', 0)
                    if time_saved < worst_parallel_saved:
                        worst_parallel_saved = time_saved
                        worst_parallel = {
                            'name': scenario_name,
                            'time_saved': time_saved,
                            'status': pstats.get('status', 'N/A')
                        }

        # Find worst worker (highest avg across all scenarios)
        worst_worker = None
        worst_worker_avg = 0
        for worker_name, times in worker_times.items():
            avg_time = sum(t[1] for t in times) / len(times) if times else 0
            if avg_time > worst_worker_avg:
                worst_worker_avg = avg_time
                worst_worker = {
                    'name': worker_name,
                    'avg_time': avg_time,
                    'scenarios': times
                }

        # Find worst decision logic
        worst_decision_logic = None
        worst_decision_avg = 0
        for logic_name, times in decision_logic_times.items():
            avg_time = sum(t[1] for t in times) / len(times) if times else 0
            if avg_time > worst_decision_avg:
                worst_decision_avg = avg_time
                worst_decision_logic = {
                    'name': logic_name,
                    'avg_time': avg_time,
                    'scenarios': times
                }

        # Print bottlenecks
        print()

        # Slowest Scenario
        if slowest_scenario:
            print(f"{ColorCodes.BOLD}   🐌 SLOWEST SCENARIO:{ColorCodes.RESET}")
            print(f"      {ColorCodes.RED}{slowest_scenario['name']}{ColorCodes.RESET}  |  "
                  f"Avg/tick: {ColorCodes.RED}{slowest_scenario['avg_time_per_tick']:.3f}ms{ColorCodes.RESET}  |  "
                  f"Total: {slowest_scenario['total_time']:.2f}ms")
            print(
                f"      {ColorCodes.YELLOW}→ This scenario took the longest time per tick{ColorCodes.RESET}")

        # Slowest Worker
        if worst_worker:
            print(f"\n{ColorCodes.BOLD}   🐌 SLOWEST WORKER:{ColorCodes.RESET}")
            print(f"      {ColorCodes.RED}{worst_worker['name']}{ColorCodes.RESET}  |  "
                  f"Avg: {ColorCodes.RED}{worst_worker['avg_time']:.3f}ms{ColorCodes.RESET} (across all scenarios)")

            # Show which scenario was worst for this worker
            worst_scenario_for_worker = max(
                worst_worker['scenarios'], key=lambda x: x[1])
            print(f"      {ColorCodes.YELLOW}→ Worst in scenario '{worst_scenario_for_worker[0]}': "
                  f"{worst_scenario_for_worker[1]:.3f}ms{ColorCodes.RESET}")

        # Slowest Decision Logic
        if worst_decision_logic and worst_decision_logic['avg_time'] > 0.5:
            print(
                f"\n{ColorCodes.BOLD}   🐌 SLOWEST DECISION LOGIC:{ColorCodes.RESET}")
            print(f"      {ColorCodes.RED}{worst_decision_logic['name']}{ColorCodes.RESET}  |  "
                  f"Avg: {ColorCodes.RED}{worst_decision_logic['avg_time']:.3f}ms{ColorCodes.RESET} (across all scenarios)")
            print(
                f"      {ColorCodes.YELLOW}→ Consider optimizing decision logic if > 1ms{ColorCodes.RESET}")

        # Worst Parallel Efficiency
        if worst_parallel and worst_parallel['time_saved'] < 0:
            print(
                f"\n{ColorCodes.BOLD}   🐌 WORST PARALLEL EFFICIENCY:{ColorCodes.RESET}")
            print(f"      {ColorCodes.RED}{worst_parallel['name']}{ColorCodes.RESET}  |  "
                  f"Time saved: {ColorCodes.RED}{worst_parallel['time_saved']:.2f}ms{ColorCodes.RESET}  |  "
                  f"Status: {worst_parallel['status']}")
            print(
                f"      {ColorCodes.YELLOW}→ Parallel execution slower than sequential! Consider disabling.{ColorCodes.RESET}")

        # Summary recommendation
        print(f"\n{ColorCodes.BOLD}   💡 RECOMMENDATIONS:{ColorCodes.RESET}")
        if worst_worker and worst_worker['avg_time'] > 1.0:
            print(
                f"      • Optimize {ColorCodes.YELLOW}{worst_worker['name']}{ColorCodes.RESET} worker (slowest component)")
        if worst_decision_logic and worst_decision_logic['avg_time'] > 1.0:
            print(
                f"      • Optimize {ColorCodes.YELLOW}{worst_decision_logic['name']}{ColorCodes.RESET} decision logic")
        if worst_parallel and worst_parallel['time_saved'] < 0:
            print(f"      • Disable parallel workers for better performance")

        # If nothing is slow, give positive feedback
        if not (worst_worker and worst_worker['avg_time'] > 1.0) and \
           not (worst_decision_logic and worst_decision_logic['avg_time'] > 1.0) and \
           not (worst_parallel and worst_parallel['time_saved'] < 0):
            print(
                f"      {ColorCodes.GREEN}✅ All components performing well! No major bottlenecks detected.{ColorCodes.RESET}")

        print()

    def _print_global_contract(self, contract: dict):
        """Output global contract (compact)"""
        self.section_separator(width=120)
        print(f"{ColorCodes.BOLD}GLOBAL CONTRACT{ColorCodes.RESET}  |  "
              f"Warmup: {contract.get('max_warmup_bars', 0)} bars  |  "
              f"Timeframes: {', '.join(contract.get('timeframes', []))}  |  "
              f"Workers: {contract.get('total_workers', 0)}")

    def print_parameter_overrides(self, scenario_name: str, overrides: dict):
        """
        Print parameter override warnings.

        NEW (V0.7): Shows when scenarios override global parameters

        Args:
            scenario_name: Name of the scenario
            overrides: Dict of parameter overrides
        """
        if not overrides:
            return

        print(
            f"\n{ColorCodes.RED}⚠️  PARAMETER OVERRIDES{ColorCodes.RESET} in {scenario_name}:")
        for param_path, value in overrides.items():
            print(
                f"   {ColorCodes.YELLOW}→{ColorCodes.RESET} {param_path}: {value}")
        print()


class VisualLogFormatter(logging.Formatter):
    """
    Custom Formatter:
    - Colored log levels
    - Compact class names (with C/ prefix if class detected)
    - Relative time (ms since start)
    """

    def __init__(self, start_time: datetime):
        super().__init__()
        self.start_time = start_time

        # Level -> Color mapping
        self.level_colors = {
            logging.ERROR: ColorCodes.RED,
            logging.WARNING: ColorCodes.YELLOW,
            logging.INFO: ColorCodes.BLUE,
            logging.DEBUG: ColorCodes.GRAY,
        }

    def format(self, record: logging.LogRecord) -> str:
        """Format log entry"""
        # Calculate relative time (ms since start)
        now = datetime.now()
        elapsed_ms = int((now - self.start_time).total_seconds() * 1000)

        # Time format: from 1000ms → "Xs XXXms" for better readability
        if elapsed_ms >= 1000:
            seconds = elapsed_ms // 1000
            millis = elapsed_ms % 1000
            time_display = f"{seconds:>3}s {millis:03d}ms"
        else:
            time_display = f"   {elapsed_ms:>3}ms   "

        # Extract class name and optionally prefix with C/
        logger_name = record.name
        if '.' in logger_name:
            class_name = logger_name.split('.')[-1]
            # Detection: Capital letter at start = class
            if class_name and class_name[0].isupper():
                display_name = f"C/{class_name}"
            else:
                display_name = class_name
        else:
            display_name = logger_name

        # Color for level
        level_color = self.level_colors.get(record.levelno, ColorCodes.RESET)
        level_name = record.levelname

        # Formatting
        formatted = (
            f"{ColorCodes.GRAY}{time_display}{ColorCodes.RESET} - "
            f"{ColorCodes.GRAY}{display_name:<25}{ColorCodes.RESET} - "
            f"{level_color}{level_name:<7}{ColorCodes.RESET} - "
            f"{record.getMessage()}"
        )

        return formatted
