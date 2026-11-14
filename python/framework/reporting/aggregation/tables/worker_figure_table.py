"""
The worker and decision tables (#398, #557).

A worker's totals over the units — one worker's timings in one run unit per row: its calls and its
time add up, its fastest and slowest call are the extremes over the units, and its mean call is
rebuilt from the totals; the mean of the units' own means stands beside it, every unit weighing
the same. The decision logic's totals over the units that tracked their decisions, one unit per
row. A unit's own sums over its workers are computed fields of its row.

The slowest performers are a table of derivations, one unit per row: each picks the first that
lies strictly above zero — the slowest unit by its algo time per tick, the slowest worker and
decision logic by the mean of their units' means — or the first that saved the least for the
parallel workers. No reduction expresses that pick, so it is written here once; the means it
compares are folded by the tables above.
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

from python.framework.reporting.aggregation.aggregation_fold import fold_rows
from python.framework.types.aggregation_types import (
    Addition,
    FigureDeclaration,
    FoldTable,
    GroupOrder,
    Reduction,
)
from python.framework.types.api.report_types import (
    DecisionTotals,
    WorkerDecisionBottlenecks,
    WorkerDecisionUnitRow,
    WorkerStatRow,
)


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
        # Every unit weighs the same — the mean of the units' own means, as the console's
        # "Scenario Avg" always read
        'unit_mean_time_ms': FigureDeclaration(Reduction.MEAN, source='avg_time_ms'),
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


def mean_decision_time(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    The mean decision time over every tracked unit, from the summed time and decisions.

    Args:
        combined: The figures being built, the time and the decisions already folded
        rows: The tracked units

    Returns:
        The summed time over the summed decisions; 0.0 without a decision
    """
    decisions = combined['total_decisions']
    return combined['decision_total_time_ms'] / decisions if decisions > 0 else 0.0


DECISION_TOTALS_TABLE = FoldTable(
    name='decision_totals',
    figures={
        'total_ticks': FigureDeclaration(Reduction.SUM, source='ticks_processed', empty_value=0),
        'total_decisions': FigureDeclaration(Reduction.SUM, source='decision_count',
                                             empty_value=0),
        'total_non_flat_signals': FigureDeclaration(Reduction.SUM, source='non_flat_signals',
                                                    empty_value=0),
        # Added in a loop, as the console's aggregate always did — #657
        'decision_total_time_ms': FigureDeclaration(Reduction.SUM, empty_value=0.0,
                                                    addition=Addition.SEQUENTIAL),
        'decision_avg_time_ms': FigureDeclaration(Reduction.DERIVE),
        'decision_unit_mean_time_ms': FigureDeclaration(Reduction.MEAN,
                                                        source='decision_avg_time_ms',
                                                        empty_value=0.0),
    },
    derivations={'decision_avg_time_ms': mean_decision_time},
    row_model=WorkerDecisionUnitRow,
    served_as=DecisionTotals,
)

# The units' mean decision time per decision logic — the comparison the slowest logic is picked by
DECISION_LOGIC_MEANS_TABLE = FoldTable(
    name='decision_logic_means',
    figures={
        'decision_logic_name': FigureDeclaration(Reduction.FIRST_ROW),
        'decision_unit_mean_time_ms': FigureDeclaration(Reduction.MEAN,
                                                        source='decision_avg_time_ms',
                                                        empty_value=0.0),
    },
    order=GroupOrder.FIRST_SEEN,
    grain=('decision_logic_name',),
    row_model=WorkerDecisionUnitRow,
)


def _first_above_zero(candidates: List[Tuple[Any, float]]) -> Optional[Tuple[Any, float]]:
    """
    The first candidate whose value lies strictly above zero and above every one before it.

    Args:
        candidates: (subject, value) in their order

    Returns:
        The winning pair; None when no value lies above zero
    """
    best: Optional[Tuple[Any, float]] = None
    for subject, value in candidates:
        if value > (best[1] if best else 0.0):
            best = (subject, value)
    return best


def _slowest_unit(rows: Sequence[Any]) -> Optional[Tuple[Any, float]]:
    """
    The tracked unit with the highest algo time per tick.

    Args:
        rows: The tracked units

    Returns:
        (unit row, its time per tick); None when no unit spent any time
    """
    return _first_above_zero([(row, row.algo_avg_per_tick_ms or 0.0) for row in rows])


def _slowest_worker(rows: Sequence[Any]) -> Optional[Tuple[Any, float]]:
    """
    The worker with the highest mean of its units' mean call times.

    Args:
        rows: The tracked units

    Returns:
        (worker name, its mean); None when no worker spent any time
    """
    totals = fold_rows([w for row in rows for w in row.workers], WORKER_TOTALS_TABLE)
    return _first_above_zero([(t['worker_name'], t['unit_mean_time_ms'] or 0.0) for t in totals])


def _slowest_worker_worst_unit(rows: Sequence[Any]) -> Optional[Tuple[str, float]]:
    """
    The unit in which the slowest worker's mean call took longest — the first among equals.

    Args:
        rows: The tracked units

    Returns:
        (unit name, the worker's mean call there); None without a slowest worker
    """
    slowest = _slowest_worker(rows)
    if slowest is None:
        return None
    best: Optional[Tuple[str, float]] = None
    for row in rows:
        for worker in row.workers:
            if worker.worker_name == slowest[0] and (best is None or worker.avg_time_ms > best[1]):
                best = (row.name, worker.avg_time_ms)
    return best


def _slowest_decision_logic(rows: Sequence[Any]) -> Optional[Tuple[Any, float]]:
    """
    The decision logic with the highest mean of its units' mean decision times.

    Args:
        rows: The tracked units — a unit that names no logic is not compared

    Returns:
        (logic name, its mean); None when no logic spent any time
    """
    means = fold_rows([row for row in rows if row.decision_logic_name],
                      DECISION_LOGIC_MEANS_TABLE)
    return _first_above_zero([(m['decision_logic_name'], m['decision_unit_mean_time_ms'])
                              for m in means])


def _worst_parallel(rows: Sequence[Any]) -> Optional[Any]:
    """
    The unit with parallel workers that saved the least time — the first among equals.

    Args:
        rows: The tracked units

    Returns:
        Its row; None without a unit that ran its workers in parallel
    """
    worst = None
    for row in rows:
        if row.parallel_workers and (worst is None
                                     or row.parallel_time_saved_ms < worst.parallel_time_saved_ms):
            worst = row
    return worst


def slowest_algo_unit_name(combined: Dict[str, Any], rows: Sequence[Any]) -> Optional[str]:
    """
    The slowest unit's name.

    Args:
        combined: The figures being built
        rows: The tracked units

    Returns:
        The slowest unit's name; None where nothing qualifies
    """
    slowest = _slowest_unit(rows)
    return slowest[0].name if slowest else None


def slowest_algo_unit_avg_per_tick(combined: Dict[str, Any], rows: Sequence[Any]) -> Optional[float]:
    """
    The slowest unit's algo time per tick.

    Args:
        combined: The figures being built
        rows: The tracked units

    Returns:
        The slowest unit's algo time per tick; None where nothing qualifies
    """
    slowest = _slowest_unit(rows)
    return slowest[1] if slowest else None


def slowest_algo_unit_time(combined: Dict[str, Any], rows: Sequence[Any]) -> Optional[float]:
    """
    The slowest unit's algo time.

    Args:
        combined: The figures being built
        rows: The tracked units

    Returns:
        The slowest unit's algo time; None where nothing qualifies
    """
    slowest = _slowest_unit(rows)
    return slowest[0].algo_time_ms if slowest else None


def slowest_worker_name(combined: Dict[str, Any], rows: Sequence[Any]) -> Optional[str]:
    """
    The slowest worker's name.

    Args:
        combined: The figures being built
        rows: The tracked units

    Returns:
        The slowest worker's name; None where nothing qualifies
    """
    slowest = _slowest_worker(rows)
    return slowest[0] if slowest else None


def slowest_worker_mean(combined: Dict[str, Any], rows: Sequence[Any]) -> Optional[float]:
    """
    The slowest worker's mean of its units' means.

    Args:
        combined: The figures being built
        rows: The tracked units

    Returns:
        The slowest worker's mean of its units' means; None where nothing qualifies
    """
    slowest = _slowest_worker(rows)
    return slowest[1] if slowest else None


def slowest_worker_worst_unit(combined: Dict[str, Any], rows: Sequence[Any]) -> Optional[str]:
    """
    The slowest worker's worst unit.

    Args:
        combined: The figures being built
        rows: The tracked units

    Returns:
        Its name; None where nothing qualifies
    """
    worst = _slowest_worker_worst_unit(rows)
    return worst[0] if worst else None


def slowest_worker_worst_unit_time(combined: Dict[str, Any], rows: Sequence[Any]) -> Optional[float]:
    """
    The slowest worker's mean call in its worst unit.

    Args:
        combined: The figures being built
        rows: The tracked units

    Returns:
        The slowest worker's mean call in its worst unit; None where nothing qualifies
    """
    worst = _slowest_worker_worst_unit(rows)
    return worst[1] if worst else None


def slowest_decision_logic_name(combined: Dict[str, Any], rows: Sequence[Any]) -> Optional[str]:
    """
    The slowest decision logic's name.

    Args:
        combined: The figures being built
        rows: The tracked units

    Returns:
        The slowest decision logic's name; None where nothing qualifies
    """
    slowest = _slowest_decision_logic(rows)
    return slowest[0] if slowest else None


def slowest_decision_logic_mean(combined: Dict[str, Any], rows: Sequence[Any]) -> Optional[float]:
    """
    The slowest decision logic's mean of its units' means.

    Args:
        combined: The figures being built
        rows: The tracked units

    Returns:
        The slowest decision logic's mean of its units' means; None where nothing qualifies
    """
    slowest = _slowest_decision_logic(rows)
    return slowest[1] if slowest else None


def worst_parallel_unit(combined: Dict[str, Any], rows: Sequence[Any]) -> Optional[str]:
    """
    The worst parallel unit's name.

    Args:
        combined: The figures being built
        rows: The tracked units

    Returns:
        The worst parallel unit's name; None where nothing qualifies
    """
    worst = _worst_parallel(rows)
    return worst.name if worst else None


def worst_parallel_time_saved(combined: Dict[str, Any], rows: Sequence[Any]) -> Optional[float]:
    """
    The time the worst parallel unit's workers saved.

    Args:
        combined: The figures being built
        rows: The tracked units

    Returns:
        The time the worst parallel unit's workers saved; None where nothing qualifies
    """
    worst = _worst_parallel(rows)
    return worst.parallel_time_saved_ms if worst else None


_BOTTLENECK_DERIVATIONS = {
    'slowest_algo_unit': slowest_algo_unit_name,
    'slowest_algo_unit_avg_per_tick_ms': slowest_algo_unit_avg_per_tick,
    'slowest_algo_unit_time_ms': slowest_algo_unit_time,
    'slowest_worker': slowest_worker_name,
    'slowest_worker_mean_ms': slowest_worker_mean,
    'slowest_worker_worst_unit': slowest_worker_worst_unit,
    'slowest_worker_worst_unit_ms': slowest_worker_worst_unit_time,
    'slowest_decision_logic': slowest_decision_logic_name,
    'slowest_decision_logic_mean_ms': slowest_decision_logic_mean,
    'worst_parallel_unit': worst_parallel_unit,
    'worst_parallel_time_saved_ms': worst_parallel_time_saved,
}

WORKER_DECISION_BOTTLENECKS_TABLE = FoldTable(
    name='worker_decision_bottlenecks',
    figures={name: FigureDeclaration(Reduction.DERIVE) for name in _BOTTLENECK_DERIVATIONS},
    derivations=_BOTTLENECK_DERIVATIONS,
    row_model=WorkerDecisionUnitRow,
    served_as=WorkerDecisionBottlenecks,
)
