"""
FiniexTestingIDE - Worker Decision Breakdown Summary (Facts Only)
Pure data output, no recommendations or suggestions.

:
- Uses typed ProfilingData instead of Dict[str, Any]
- Clean direct property access: profiling_data.get_operation_time()
- No more nested dict navigation

FULLY TYPED: Uses BatchPerformanceStats with direct attribute access.
"""

from typing import List, Optional
from python.framework.reporting.console.abstract_batch_summary_section import AbstractBatchSummarySection
from python.framework.utils.console_renderer import ConsoleRenderer
from python.framework.types.api.report_types import ProfilingReport, ProfilingUnitRow, WorkerDecisionReport
from python.framework.types.performance_types.performance_metrics_types import (
    WorkerDecisionBreakdown,
)


class WorkerDecisionBreakdownSummary(AbstractBatchSummarySection):
    """
    Worker decision breakdown - facts only.

    FULLY TYPED: Uses BatchPerformanceStats instead of dicts.
    Uses typed ProfilingData for clean access.
    """

    _section_title = '🔍 WORKER DECISION BREAKDOWN'

    def __init__(
        self,
        profiling_report: ProfilingReport,
        worker_decision_report: WorkerDecisionReport,
    ):
        # Both inputs are model-fed now, matched per unit by name: the worker/decision facts
        # (Layer A, #398) and the operation Total that drives the coordination-overhead
        # (Layer B, from the profiling model — #399, closing the #398 residual). No
        # profiling_data_map dependency anymore.
        self._wd_rows_by_name = {row.name: row for row in worker_decision_report.units}
        self._profiling_by_name = {unit.name: unit for unit in profiling_report.units}
        self.breakdowns = self._build_breakdowns()

    def _both_layers_have_data(self) -> bool:
        """
        Returns True only when at least one unit has BOTH Layer A (worker facts, the
        worker/decision model) AND Layer B (operation timing, the profiling model). The
        breakdown's components (Worker Execution / Decision Logic / Coordination Overhead)
        need both — Layer A the per-component split, Layer B the operation top-line. If
        either is missing the section is suppressed (#137).
        """
        for name, profiling in self._profiling_by_name.items():
            row = self._wd_rows_by_name.get(name)
            if profiling.operations and row is not None and row.workers:
                return True
        return False

    def render_per_scenario(self, renderer: ConsoleRenderer):
        """Render per scenario breakdown."""
        if not self._both_layers_have_data():
            return

        self._render_section_header(renderer)

        if not self.breakdowns:
            print("No data")
            return

        for idx, breakdown in enumerate(self.breakdowns, 1):
            if idx > 1:
                print()
                renderer.print_separator(width=120, char="·")
                print()
            self._render_scenario_breakdown(breakdown, renderer)

    def render_aggregated(self):
        """Render aggregated breakdown."""
        if not self._both_layers_have_data():
            return

        if not self.breakdowns:
            print("No data")
            return

        print()

    def _build_breakdowns(self) -> List[WorkerDecisionBreakdown]:
        """Build breakdowns from the profiling units (model-fed, ordered per run unit)."""
        breakdowns = []
        for profiling in self._profiling_by_name.values():
            breakdown = self._build_breakdown_for_unit(profiling)
            if breakdown:
                breakdowns.append(breakdown)
        return breakdowns

    def _build_breakdown_for_unit(
        self, profiling: ProfilingUnitRow
    ) -> Optional[WorkerDecisionBreakdown]:
        """
        Build breakdown for a single run unit from the model rows (matched by name).

        Args:
            profiling: The unit's profiling row (operation timing)

        Returns:
            WorkerDecisionBreakdown or None if the worker_decision op / worker facts are missing
        """
        # Total worker_decision operation time — from the profiling model (#399,
        # closing the #398 residual; was the profiling_data_map top-line).
        total_worker_decision_ms = next(
            (op.total_time_ms for op in profiling.operations
             if op.operation == 'worker_decision'), 0.0)
        if total_worker_decision_ms == 0:
            return None

        # Facts from the unified worker/decision model (#398), matched by unit name.
        row = self._wd_rows_by_name.get(profiling.name)
        if row is None:
            return None

        # Calculate worker execution time (sum from the model's per-worker rows)
        worker_execution_ms = sum(w.total_time_ms for w in row.workers)

        # Build worker breakdown dict (worker_name -> time_ms)
        worker_breakdown = {
            w.worker_name: w.total_time_ms
            for w in row.workers
        }

        # Get decision logic time
        decision_logic_ms = row.decision_total_time_ms

        # Calculate coordination overhead (profiling total minus the model components)
        coordination_overhead_ms = max(
            0.0,
            total_worker_decision_ms - worker_execution_ms - decision_logic_ms
        )

        return WorkerDecisionBreakdown(
            scenario_name=profiling.name,
            total_time_ms=total_worker_decision_ms,
            total_ticks=row.ticks_processed,
            worker_execution_ms=worker_execution_ms,
            decision_logic_ms=decision_logic_ms,
            coordination_overhead_ms=coordination_overhead_ms,
            worker_breakdown=worker_breakdown
        )

    def _render_scenario_breakdown(self, breakdown: WorkerDecisionBreakdown, renderer):
        """Render scenario breakdown."""
        print(f"{renderer.bold('Scenario:')} {renderer.blue(breakdown.scenario_name)}")
        print(f"{renderer.gray('Total:')} {breakdown.total_time_ms:.2f}ms  |  "
              f"{renderer.gray('Ticks:')} {breakdown.total_ticks:,}")
        print()

        # Component breakdown
        print(renderer.bold("Components:"))
        print("┌────────────────────────────────────────────────────┐")

        bar_workers = self._create_bar(breakdown.worker_execution_pct)
        color = renderer.green if breakdown.worker_execution_pct > 50 else renderer.yellow
        print(f"│ Worker Execution      {breakdown.worker_execution_ms:>7.2f}ms  {bar_workers}  "
              f"{color(f'{breakdown.worker_execution_pct:>5.1f}%')}      │")

        bar_decision = self._create_bar(breakdown.decision_logic_pct)
        print(f"│ Decision Logic        {breakdown.decision_logic_ms:>7.2f}ms  {bar_decision}  "
              f"{breakdown.decision_logic_pct:>5.1f}%      │")

        bar_overhead = self._create_bar(breakdown.coordination_overhead_pct)
        # Overhead % is a pure calculation; the "too high?" verdict lives in the post-run
        # validator (#395), so no ⚠️ / red flag is asserted here.
        print(f"│ Coordination Overhead {breakdown.coordination_overhead_ms:>7.2f}ms  {bar_overhead}  "
              f"{renderer.yellow(f'{breakdown.coordination_overhead_pct:>5.1f}%')}     │")

        print("└────────────────────────────────────────────────────┘")
        print()
        # Per-worker timing is rendered once, by the model-fed performance summary
        # (WORKER DETAILS) — not duplicated here (#399 3d). The Components box above
        # is the overhead split; the per-worker detail lives in PerformanceSummary.

    def _create_bar(self, percentage: float, width: int = 12) -> str:
        """Create ASCII bar."""
        filled = int((percentage / 100) * width)
        empty = width - filled
        return '█' * filled + '░' * empty
