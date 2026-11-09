"""
The profiling aggregate's fold tables (#399, #557) — one run unit's profile per row.

The run's processing time over its ticks, the most common bottleneck, the inter-tick P5 range, the
budget the slowest units suggest, and the clipping a budget caused. The per-operation means are a
table of their own, one operation's calls per row.
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

from python.framework.types.aggregation_types import (
    Addition,
    FigureDeclaration,
    FoldTable,
    GroupOrder,
    Reduction,
)
from python.framework.types.api.report_types import (
    ProfilingAggregate,
    ProfilingBottleneckRow,
    ProfilingOperationRow,
    ProfilingUnitRow,
)
from python.framework.types.scenario_types.scenario_set_performance_types import EXPECTED_OPERATIONS


def p5_of(row: Any) -> Optional[float]:
    """
    A unit's inter-tick P5, where it measured one.

    Args:
        row: A profiling unit row

    Returns:
        The P5 in ms, or None
    """
    return row.inter_tick.p5_ms if row.inter_tick else None


def over_its_p5(row: Any) -> int:
    """
    Whether a unit's average tick took longer than its own P5 interval — one count each.

    Args:
        row: A profiling unit row

    Returns:
        1 when it did, else 0
    """
    return 1 if row.inter_tick and row.avg_per_tick_ms > row.inter_tick.p5_ms else 0


def clipped_ticks_total(row: Any) -> Optional[int]:
    """
    The ticks a unit's budget saw, where a budget was active.

    Args:
        row: A profiling unit row

    Returns:
        The count, or None
    """
    return row.clipping.ticks_total if row.clipping else None


def clipped_ticks_kept(row: Any) -> Optional[int]:
    """
    The ticks a unit's budget kept, where a budget was active.

    Args:
        row: A profiling unit row

    Returns:
        The count, or None
    """
    return row.clipping.ticks_kept if row.clipping else None


def clipped_ticks_clipped(row: Any) -> Optional[int]:
    """
    The ticks a unit's budget clipped, where a budget was active.

    Args:
        row: A profiling unit row

    Returns:
        The count, or None
    """
    return row.clipping.ticks_clipped if row.clipping else None


def clipping_budget(row: Any) -> Optional[float]:
    """
    A unit's tick budget, where one was active.

    Args:
        row: A profiling unit row

    Returns:
        The budget in ms, or None
    """
    return row.clipping.budget_ms if row.clipping else None


def _bottleneck_frequency(rows: Sequence[Any]) -> Dict[str, int]:
    """
    How often each operation was a unit's top operation.

    Args:
        rows: The units

    Returns:
        Operation → units it topped
    """
    freq: Dict[str, int] = {}
    for row in rows:
        if row.bottleneck_operation:
            freq[row.bottleneck_operation] = freq.get(row.bottleneck_operation, 0) + 1
    return freq


def _most_common(rows: Sequence[Any]) -> Tuple[str, int]:
    """
    The operation that topped the most units — alphabetically first among equals.

    Args:
        rows: The units

    Returns:
        The operation and its count; ('', 0) when no unit named one
    """
    for operation, count in sorted(_bottleneck_frequency(rows).items(),
                                   key=lambda kv: (-kv[1], kv[0])):
        return operation, count
    return '', 0


def most_common_bottleneck(combined: Dict[str, Any], rows: Sequence[Any]) -> str:
    """
    The operation that topped the most units.

    Args:
        combined: The figures being built
        rows: The units

    Returns:
        Its name, '' when no unit named one
    """
    return _most_common(rows)[0]


def most_common_bottleneck_pct(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    The share of the units the most common bottleneck topped.

    Args:
        combined: The figures being built, the unit count already folded
        rows: The units

    Returns:
        The share in percent; 0.0 without a unit
    """
    scenarios = combined['scenarios']
    return (_most_common(rows)[1] / scenarios * 100) if scenarios else 0.0


def _p95_processing(rows: Sequence[Any]) -> float:
    """
    The P95 of the units' average tick processing time, nearest rank at int(n · 0.95).

    Args:
        rows: The units

    Returns:
        The P95 in ms; 0.0 when no unit processed a tick
    """
    averages = sorted(r.avg_per_tick_ms for r in rows if r.avg_per_tick_ms > 0)
    if not averages:
        return 0.0
    return averages[min(int(len(averages) * 0.95), len(averages) - 1)]


def p95_processing(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    The P95 of the units' average tick processing time.

    Args:
        combined: The figures being built
        rows: The units

    Returns:
        The P95 in ms; 0.0 when no unit processed a tick
    """
    return _p95_processing(rows)


def suggested_budget(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    The tick budget the slowest units suggest: their P95 plus a 10 % margin.

    Args:
        combined: The figures being built, the P95 already derived
        rows: The units

    Returns:
        The budget in ms, rounded to the microsecond; 0.0 without a P95
    """
    p95 = combined['p95_processing_ms']
    return round(p95 * 1.1, 3) if p95 else 0.0


def total_time_seconds(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    The units' processing time, in seconds.

    Args:
        combined: The figures being built, the milliseconds already folded
        rows: The units

    Returns:
        The seconds
    """
    return combined['total_time_ms'] / 1000


def average_per_tick(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    The processing time per tick over every unit — from the summed time and ticks.

    Args:
        combined: The figures being built
        rows: The units

    Returns:
        Milliseconds per tick; 0.0 without a tick
    """
    total_ticks = combined['total_ticks']
    return combined['total_time_ms'] / total_ticks if total_ticks > 0 else 0.0


def _bottleneck_status(operation: str, count: int) -> str:
    """
    Display classification only: 'expected' (intended hot path) vs 'infra', or 'none' when this
    operation was never the bottleneck. NOT a verdict — whether an infra bottleneck is a problem
    is decided by the post-run validator (#395).
    """
    if count == 0:
        return 'none'
    return 'expected' if operation in EXPECTED_OPERATIONS else 'infra'


def bottleneck_listing(combined: Dict[str, Any], rows: Sequence[Any]) -> List[ProfilingBottleneckRow]:
    """
    How often each operation the units measured was their top one — every operation listed, the
    most frequent first. `status` is a display classification only; whether a bottleneck is a
    problem is a decision, and lives in the post-run validator (#395).

    Args:
        combined: The figures being built, the unit count already folded
        rows: The units

    Returns:
        One row per operation
    """
    freq = _bottleneck_frequency(rows)
    scenarios = combined['scenarios']
    listing = [
        ProfilingBottleneckRow(
            operation=op,
            scenario_count=freq.get(op, 0),
            total_scenarios=scenarios,
            pct=(freq.get(op, 0) / scenarios * 100) if scenarios else 0.0,
            status=_bottleneck_status(op, freq.get(op, 0)))
        for op in {op.operation for row in rows for op in row.operations}
    ]
    listing.sort(key=lambda b: (-b.scenario_count, b.operation))
    return listing


PROFILING_TABLE = FoldTable(
    name='profiling',
    figures={
        'scenarios': FigureDeclaration(Reduction.COUNT),
        # Over no unit zero, so the rates rebuild; the family serves no aggregate without a unit
        'total_ticks': FigureDeclaration(Reduction.SUM, empty_value=0),
        'total_time_ms': FigureDeclaration(Reduction.SUM, source='total_ms', empty_value=0.0),
        'total_time_s': FigureDeclaration(Reduction.DERIVE),
        # From the summed components, never an average of the units' averages
        'avg_per_tick_ms': FigureDeclaration(Reduction.DERIVE),
        'most_common_bottleneck': FigureDeclaration(Reduction.DERIVE),
        'most_common_bottleneck_pct': FigureDeclaration(Reduction.DERIVE),
        'p5_min_ms': FigureDeclaration(Reduction.MIN, source=p5_of, empty_value=0.0),
        'p5_max_ms': FigureDeclaration(Reduction.MAX, source=p5_of, empty_value=0.0),
        'p95_processing_ms': FigureDeclaration(Reduction.DERIVE),
        'suggested_budget_ms': FigureDeclaration(Reduction.DERIVE),
        # The units whose average tick exceeded their own P5 interval — the console used to count
        # this itself to decide what to print (#391: no measures in PRESENT)
        'scenarios_over_p5': FigureDeclaration(Reduction.SUM, source=over_its_p5, empty_value=0),
        'clipping_total_ticks': FigureDeclaration(Reduction.SUM, source=clipped_ticks_total,
                                                  empty_value=0),
        'clipping_total_kept': FigureDeclaration(Reduction.SUM, source=clipped_ticks_kept,
                                                 empty_value=0),
        'clipping_total_clipped': FigureDeclaration(Reduction.SUM, source=clipped_ticks_clipped,
                                                    empty_value=0),
        'clipping_budgets': FigureDeclaration(Reduction.DISTINCT, source=clipping_budget),
        'bottlenecks': FigureDeclaration(Reduction.DERIVE),
    },
    derivations={
        'total_time_s': total_time_seconds,
        'avg_per_tick_ms': average_per_tick,
        'most_common_bottleneck': most_common_bottleneck,
        'most_common_bottleneck_pct': most_common_bottleneck_pct,
        'p95_processing_ms': p95_processing,
        'suggested_budget_ms': suggested_budget,
        'bottlenecks': bottleneck_listing,
    },
    row_model=ProfilingUnitRow,
    served_as=ProfilingAggregate,
    outside_the_fold={
        'total_time_ms': 'folded, not served: the seconds and the time per tick come from it',
        'budget_active': 'an input: the run decides whether a tick budget was active',
        'avg_operation_times': 'a nested table: the operations folded by their table',
    },
)

PROFILING_OPERATION_TABLE = FoldTable(
    name='profiling_operations',
    figures={
        # The group's key, read as every row carries it
        'operation': FigureDeclaration(Reduction.FIRST_ROW),
        # A mean of the units' means, added in a loop — #657 weighs it by the calls
        'avg_time_ms': FigureDeclaration(Reduction.MEAN, addition=Addition.SEQUENTIAL),
    },
    order=GroupOrder.FIRST_SEEN,
    grain=('operation',),
    row_model=ProfilingOperationRow,
    served_as=ProfilingOperationRow,
    outside_the_fold={
        name: 'per unit only: the mean over the units serves its default'
        for name in ('call_count', 'total_time_ms', 'pct')},
)
