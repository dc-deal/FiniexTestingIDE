"""
Worker/decision breakdown builder (#399, #557) — where each unit's `worker_decision` time went.

Joins the two tracking layers per unit, by name: the operation's total from the tick-loop
profiling, the workers' and the decision logic's time from the worker/decision report. What is
left of the total once both are taken out is the coordination between them, never below zero.
Sim-only, like the profiling it extends; built once both reports exist.
"""

from typing import List

from python.framework.types.api.report_types import (
    ProfilingReport,
    WorkerDecisionBreakdownRow,
    WorkerDecisionReport,
)


def build_worker_decision_breakdowns(
        profiling_report: ProfilingReport,
        worker_decision_report: WorkerDecisionReport) -> List[WorkerDecisionBreakdownRow]:
    """
    One breakdown per unit both layers measured, in the profiling's unit order.

    Args:
        profiling_report: The profiling report — each unit's `worker_decision` operation total
        worker_decision_report: The worker/decision report — each unit's workers and decisions

    Returns:
        The breakdowns; a unit without the operation's time or without a worker/decision row
        has none
    """
    rows_by_name = {row.name: row for row in worker_decision_report.units}
    breakdowns = []
    for profiling in profiling_report.units:
        total = next((op.total_time_ms for op in profiling.operations
                      if op.operation == 'worker_decision'), 0.0)
        row = rows_by_name.get(profiling.name)
        if total == 0 or row is None:
            continue
        workers = row.worker_time_ms
        # Null where the unit did not track its decisions: nothing was timed, nothing taken out
        decision = row.decision_total_time_ms or 0.0
        overhead = max(0.0, total - workers - decision)
        breakdowns.append(WorkerDecisionBreakdownRow(
            name=profiling.name, total_time_ms=total, total_ticks=row.ticks_processed,
            worker_execution_ms=workers, decision_logic_ms=decision,
            coordination_overhead_ms=overhead,
            worker_execution_pct=(workers / total * 100) if total > 0 else 0.0,
            decision_logic_pct=(decision / total * 100) if total > 0 else 0.0,
            coordination_overhead_pct=(overhead / total * 100) if total > 0 else 0.0))
    return breakdowns
