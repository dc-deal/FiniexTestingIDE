"""
The worker totals' fold table (#398, #557) — one worker's timings in one run unit per row.

A worker's totals over the units: its calls and its time add up, its fastest and slowest call are
the extremes over the units, and its mean call is rebuilt from the totals — never a mean of the
units' means.
"""

from typing import Any, Dict, Sequence

from python.framework.types.aggregation_types import (
    Addition,
    FigureDeclaration,
    FoldTable,
    GroupOrder,
    Reduction,
)
from python.framework.types.api.report_types import WorkerStatRow


def mean_call_time(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    A worker's mean call time over every unit, from its summed time and calls.

    Args:
        combined: The figures being built, the time and the calls already folded
        rows: The worker's rows

    Returns:
        The summed time over the summed calls; 0.0 without a call
    """
    return (combined['total_time_ms'] / combined['call_count']) if combined['call_count'] else 0.0


_PER_UNIT_ONLY = 'per unit only: the total over the units serves its default'

WORKER_TOTALS_TABLE = FoldTable(
    name='worker_totals',
    figures={
        'worker_type': FigureDeclaration(Reduction.FIRST_ROW),
        'worker_name': FigureDeclaration(Reduction.FIRST_ROW),
        'call_count': FigureDeclaration(Reduction.SUM),
        # Added in a loop, as the totals always were — #657
        'total_time_ms': FigureDeclaration(Reduction.SUM, addition=Addition.SEQUENTIAL),
        'avg_time_ms': FigureDeclaration(Reduction.DERIVE),
        'min_time_ms': FigureDeclaration(Reduction.MIN, empty_value=0.0),
        'max_time_ms': FigureDeclaration(Reduction.MAX, empty_value=0.0),
    },
    derivations={'avg_time_ms': mean_call_time},
    order=GroupOrder.FIRST_SEEN,
    grain=('worker_name',),
    row_model=WorkerStatRow,
    served_as=WorkerStatRow,
    outside_the_fold={
        'compute_basis': _PER_UNIT_ONLY,
        'last_compute_tick': _PER_UNIT_ONLY,
        'compute_ratio_pct': _PER_UNIT_ONLY,
        'ticks_idle': _PER_UNIT_ONLY,
    },
)
