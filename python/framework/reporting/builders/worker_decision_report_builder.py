"""
Worker/decision report builder (#398) — the per-unit worker + decision performance postprocessor.

Maps each `RunUnit`'s `worker_statistics` + `decision_statistics` + `coordination_statistics` to a
`WorkerDecisionUnitRow`; the per-worker timing totals (summed across units) come from the shared
aggregator. Unified — both pipelines (sim scenario / live session); coordination is sim-only and
stays at its defaults on live. The coordination-overhead % breakdown is profiling-derived and stays
with the Profiling section, not here.
"""

from typing import List

from python.framework.reporting.builders.report_aggregators import aggregate_worker_totals
from python.framework.reporting.builders.run_unit import RunUnit
from python.framework.types.api.report_types import (
    WorkerDecisionReport, WorkerDecisionUnitRow, WorkerStatRow)


def build_worker_decision_report(units: List[RunUnit]) -> WorkerDecisionReport:
    """
    Build the worker/decision report from the run units.

    Args:
        units: The run's units (sim: N scenarios; live: 1 session)

    Returns:
        WorkerDecisionReport — one row per unit + the per-worker timing totals
    """
    rows = [_to_unit_row(unit) for unit in units]
    return WorkerDecisionReport(units=rows, worker_totals=aggregate_worker_totals(rows))


def _to_unit_row(unit: RunUnit) -> WorkerDecisionUnitRow:
    """Map one RunUnit's worker + decision stats to a renderable row."""
    decision = unit.decision_statistics
    coordination = unit.coordination_statistics
    workers = [
        WorkerStatRow(
            worker_type=w.worker_type, worker_name=w.worker_name,
            call_count=w.worker_call_count, total_time_ms=w.worker_total_time_ms,
            avg_time_ms=w.worker_avg_time_ms, min_time_ms=w.worker_min_time_ms,
            max_time_ms=w.worker_max_time_ms,
            compute_basis=w.worker_compute_basis, last_compute_tick=w.worker_last_compute_tick)
        for w in unit.worker_statistics
    ]
    return WorkerDecisionUnitRow(
        name=unit.name,
        symbol=unit.symbol,
        decision_logic_type=decision.decision_logic_type if decision else '',
        decision_logic_name=decision.decision_logic_name if decision else '',
        decision_count=decision.decision_count if decision else 0,
        buy_signals=decision.buy_signals if decision else 0,
        sell_signals=decision.sell_signals if decision else 0,
        flat_signals=decision.flat_signals if decision else 0,
        trades_requested=decision.trades_requested if decision else 0,
        decision_total_time_ms=decision.decision_total_time_ms if decision else 0.0,
        decision_avg_time_ms=decision.decision_avg_time_ms if decision else 0.0,
        decision_min_time_ms=decision.decision_min_time_ms if decision else 0.0,
        decision_max_time_ms=decision.decision_max_time_ms if decision else 0.0,
        ticks_processed=coordination.ticks_processed if coordination else 0,
        parallel_workers=coordination.parallel_workers if coordination else False,
        parallel_time_saved_ms=coordination.parallel_time_saved_ms if coordination else 0.0,
        workers=workers,
    )
