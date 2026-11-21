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

from typing import Dict, List

from python.framework.reporting.console.abstract_batch_summary_section import (
    AbstractBatchSummarySection,
)
from python.framework.types.api.report_types import WorkerDecisionReport, WorkerDecisionUnitRow
from python.framework.utils.console_renderer import ConsoleRenderer


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
        self._report = worker_decision_report
        # Only a tracked unit has figures to show; an untracked one carries null counters.
        self._units: List[WorkerDecisionUnitRow] = [
            unit for unit in worker_decision_report.units if unit.worker_decision_tracked]

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
            print('No performance data available')
            return

        for idx, unit in enumerate(self._units, 1):
            # Separator between scenarios
            if idx > 1:
                print()
                renderer.print_separator(width=120, char='·')
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

        print()
        renderer.section_separator()
        renderer.print_bold('📊 AGGREGATED SUMMARY (ALL SCENARIOS)')
        renderer.section_separator()

        self._render_aggregated_details(renderer)
        print()

    def render_bottleneck_analysis(self, renderer: ConsoleRenderer) -> None:
        """
        Render bottleneck analysis - worst performers.

        Args:
            renderer: ConsoleRenderer instance
        """
        if not self._layer_a_has_data():
            return

        print()
        renderer.section_separator()
        print(f"{renderer.bold(renderer.red('⚠️ BOTTLENECK ANALYSIS'))} "
              f"{renderer.gray('(Worst Performers)')}")
        renderer.section_separator()

        self._render_bottleneck_details(renderer)
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
        total_workers = unit.timed_worker_count
        decision_count = unit.decision_count
        total_calls = unit.worker_call_count

        mode_str = renderer.green(
            'Parallel') if parallel_workers else renderer.yellow('Sequential')
        print(f"{renderer.bold('📊 SCENARIO PERFORMANCE:')} {unit.name}")
        print(f"{renderer.bold('   Workers:')} {total_workers} workers ({mode_str})  |  "
              f"Ticks: {ticks_processed:,}  |  "
              f"Calls: {total_calls:,}  |  "
              f"Decisions: {decision_count}")

        # Per-worker details
        if unit.workers:
            print(f"\n{renderer.bold('   📊 WORKER DETAILS:')}")

            for w in unit.workers:
                # Cadence telemetry (#420): actual computes vs ticks + ticks idle
                # since the last compute (BAR_CLOSE serves a cached value in between).
                total_ticks = unit.ticks_processed
                if total_ticks > 0 and w.compute_ratio_pct is not None:
                    # `basis=` in front: bare, the compute basis `live` read as a live session.
                    cadence = (f'basis={w.compute_basis:9} {w.call_count:>5}/{total_ticks} '
                               f'computes ({w.compute_ratio_pct:4.0f}%, '
                               f"{w.ticks_idle if w.ticks_idle is not None else '—'} idle)")
                else:
                    cadence = f'basis={w.compute_basis:9} {w.call_count:>5} computes'
                print(f"      {renderer.blue(f'{w.worker_name:15}->{w.worker_type:15}')}  "
                      f"{cadence}  |  "
                      f"Avg: {w.avg_time_ms:>6.3f}ms  |  "
                      f"Range: {w.min_time_ms:>6.3f}-{w.max_time_ms:>6.3f}ms  |  "
                      f"Total: {w.total_time_ms:>8.2f}ms")

        # Parallel efficiency
        if parallel_workers and ticks_processed > 0:
            parallel_time_saved_ms = unit.parallel_time_saved_ms
            parallel_avg_saved_per_tick_ms = unit.parallel_avg_saved_per_tick_ms
            status = self._get_parallel_status(parallel_time_saved_ms)

            print(f"\n{renderer.bold('   ⚡ PARALLEL EFFICIENCY:')}")
            print(f'      Time saved: {parallel_time_saved_ms:>8.2f}ms total  |  '
                  f'Avg/tick: {parallel_avg_saved_per_tick_ms:>6.3f}ms  |  '
                  f'Status: {status}')

        # Decision logic
        if unit.worker_decision_tracked:
            print(
                f"\n{renderer.bold('   🧠 DECISION LOGIC:')} {unit.decision_logic_name} ({unit.decision_logic_type})")
            print(f'      Decisions: {unit.decision_count}  |  '
                  f'Avg: {unit.decision_avg_time_ms:>6.3f}ms  |  '
                  f'Range: {unit.decision_min_time_ms:>6.3f}-{unit.decision_max_time_ms:>6.3f}ms  |  '
                  f'Total: {unit.decision_total_time_ms:>8.2f}ms')

        print()

    def _render_aggregated_details(self, renderer: ConsoleRenderer) -> None:
        """
        Render detailed aggregated performance stats — the report's totals.

        Args:
            renderer: ConsoleRenderer instance
        """
        totals = self._report.decision_totals
        if totals is None:
            # No unit tracked its decisions: there are no totals to show
            return
        print()
        print(f"{renderer.bold('   📊 AGGREGATED STATS:')}")
        print(f'      Total Ticks: {totals.total_ticks:,}  |  '
              f'Total Signals: {totals.total_non_flat_signals:,}  |  '
              f'Total Decisions: {totals.total_decisions:,}')

        # Worker totals, in the order the workers first appear across the scenarios
        if self._report.worker_totals:
            print(f"\n{renderer.bold('   👷 WORKERS (AGGREGATED):')}")

            first_seen: Dict[str, int] = {}
            for unit in self._units:
                for w in unit.workers:
                    first_seen.setdefault(w.worker_name, len(first_seen))
            for worker in sorted(self._report.worker_totals,
                                 key=lambda w: first_seen.get(w.worker_name, len(first_seen))):
                print(f"      {renderer.blue(f'{worker.worker_name:15}')}  "
                      f"Total Calls: {worker.call_count:>6}  |  "
                      f"Total Time: {worker.total_time_ms:>8.2f}ms  |  "
                      f"Avg: {worker.avg_time_ms:>6.3f}ms  |  "
                      f"Scenario Avg: {worker.unit_mean_time_ms:>6.3f}ms")

        # Decision logic totals
        if totals.total_decisions > 0:
            print(f"\n{renderer.bold('   🧠 DECISION LOGIC (AGGREGATED):')}")
            print(f'      Total Decisions: {totals.total_decisions}  |  '
                  f'Total Time: {totals.decision_total_time_ms:>8.2f}ms  |  '
                  f'Avg: {totals.decision_avg_time_ms:>6.3f}ms  |  '
                  f'Scenario Avg: {totals.decision_unit_mean_time_ms:>6.3f}ms')

    def _render_bottleneck_details(self, renderer: ConsoleRenderer) -> None:
        """
        Render bottleneck analysis details — the report's slowest performers.

        Args:
            renderer: ConsoleRenderer instance
        """
        b = self._report.bottlenecks
        print()
        if b is None:
            # No unit tracked its decisions: there is nothing to rank
            return

        # Slowest scenario
        if b.slowest_algo_unit is not None:
            print(f"{renderer.bold('   🌶 SLOWEST SCENARIO:')}")
            avg_str = renderer.red(f'{b.slowest_algo_unit_avg_per_tick_ms:.3f}ms')
            print(f'      {renderer.red(b.slowest_algo_unit)}  |  '
                  f'Avg/tick: {avg_str}  |  '
                  f'Total: {b.slowest_algo_unit_time_ms:.2f}ms')
            print(
                f"      {renderer.yellow('→ This scenario took the longest time per tick')}")

        # Slowest worker
        if b.slowest_worker is not None:
            print(f"\n{renderer.bold('   🌶 SLOWEST WORKER:')}")
            avg_str = renderer.red(f'{b.slowest_worker_mean_ms:.3f}ms')
            print(f'      {renderer.red(b.slowest_worker)}  |  '
                  f'Avg: {avg_str} (across all scenarios)')

            worst_msg = (f"→ Worst in scenario '{b.slowest_worker_worst_unit}': "
                         f'{b.slowest_worker_worst_unit_ms:.3f}ms')
            print(f'      {renderer.yellow(worst_msg)}')

        # Slowest decision logic — a measurement, shown like the slowest worker; there is
        # deliberately no fixed "too slow" threshold (validators/shared_advisory_checks.py)
        if b.slowest_decision_logic is not None:
            print(f"\n{renderer.bold('   🌶 SLOWEST DECISION LOGIC:')}")
            avg_str = renderer.red(f'{b.slowest_decision_logic_mean_ms:.3f}ms')
            print(f'      {renderer.red(b.slowest_decision_logic)}  |  '
                  f'Avg: {avg_str} (across all scenarios)')

        # Worst parallel efficiency — shown whenever a unit ran its workers in parallel; whether
        # it cost time is PostRunValidator's verdict (_check_parallel_penalty)
        if b.worst_parallel_unit is not None:
            print(f"\n{renderer.bold('   🌶 WORST PARALLEL EFFICIENCY:')}")
            time_saved_str = renderer.red(f'{b.worst_parallel_time_saved_ms:.2f}ms')
            print(f'      {renderer.red(b.worst_parallel_unit)}  |  '
                  f'Time saved: {time_saved_str}  |  '
                  f'Status: {self._get_parallel_status(b.worst_parallel_time_saved_ms)}')

        # No recommendations here. Whether parallel execution is costing time is a verdict —
        # PostRunValidator decides it (_check_parallel_penalty) and the WARNINGS & ERRORS
        # section renders it. This section shows the measurements, and the clean-run line
        # belongs to the section that owns the verdicts, so it is not repeated here.
        # There is deliberately NO per-component "too slow" verdict: see the note in
        # validators/shared_advisory_checks.py — "slow" needs a relative reference, and the
        # grounded form of that question is PostRunValidator._check_budget.
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
            return '✅ Faster'
        elif time_saved_ms < -0.01:
            return '⚠️ Slower'
        else:
            return '≈ Equal'
