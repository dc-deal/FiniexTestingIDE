"""
FiniexTestingIDE - Worker Decision Breakdown Summary (Facts Only)
Pure data output, no recommendations or suggestions.

:
- Uses typed ProfilingData instead of Dict[str, Any]
- Clean direct property access: profiling_data.get_operation_time()
- No more nested dict navigation

FULLY TYPED: Uses BatchPerformanceStats with direct attribute access.
"""

from python.framework.reporting.console.abstract_batch_summary_section import (
    AbstractBatchSummarySection,
)
from python.framework.types.api.report_types import (
    ProfilingReport,
    WorkerDecisionBreakdownRow,
    WorkerDecisionReport,
)
from python.framework.utils.console_renderer import ConsoleRenderer


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
        # One breakdown per unit both layers measured — derived on the profiling model (#557)
        self.breakdowns = profiling_report.worker_decision_breakdowns or []

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
            print('No data')
            return

        for idx, breakdown in enumerate(self.breakdowns, 1):
            if idx > 1:
                print()
                renderer.print_separator(width=120, char='·')
                print()
            self._render_scenario_breakdown(breakdown, renderer)

    def render_aggregated(self):
        """Render aggregated breakdown."""
        if not self._both_layers_have_data():
            return

        if not self.breakdowns:
            print('No data')
            return

        print()

    def _render_scenario_breakdown(self, breakdown: WorkerDecisionBreakdownRow, renderer):
        """Render scenario breakdown."""
        print(f"{renderer.bold('Scenario:')} {renderer.blue(breakdown.name)}")
        print(f"{renderer.gray('Total:')} {breakdown.total_time_ms:.2f}ms  |  "
              f"{renderer.gray('Ticks:')} {breakdown.total_ticks:,}")
        print()

        # Component breakdown
        print(renderer.bold('Components:'))
        print('┌────────────────────────────────────────────────────┐')

        bar_workers = self._create_bar(breakdown.worker_execution_pct)
        color = renderer.green if breakdown.worker_execution_pct > 50 else renderer.yellow
        print(f"│ Worker Execution      {breakdown.worker_execution_ms:>7.2f}ms  {bar_workers}  "
              f"{color(f'{breakdown.worker_execution_pct:>5.1f}%')}      │")

        bar_decision = self._create_bar(breakdown.decision_logic_pct)
        print(f'│ Decision Logic        {breakdown.decision_logic_ms:>7.2f}ms  {bar_decision}  '
              f'{breakdown.decision_logic_pct:>5.1f}%      │')

        bar_overhead = self._create_bar(breakdown.coordination_overhead_pct)
        # Overhead % is a pure calculation; the "too high?" verdict lives in the post-run
        # validator (#395), so no ⚠️ / red flag is asserted here.
        print(f"│ Coordination Overhead {breakdown.coordination_overhead_ms:>7.2f}ms  {bar_overhead}  "
              f"{renderer.yellow(f'{breakdown.coordination_overhead_pct:>5.1f}%')}     │")

        print('└────────────────────────────────────────────────────┘')
        print()
        # Per-worker timing is rendered once, by the model-fed performance summary
        # (WORKER DETAILS) — not duplicated here (#399 3d). The Components box above
        # is the overhead split; the per-worker detail lives in PerformanceSummary.

    def _create_bar(self, percentage: float, width: int = 12) -> str:
        """Create ASCII bar."""
        filled = int((percentage / 100) * width)
        empty = width - filled
        return '█' * filled + '░' * empty
