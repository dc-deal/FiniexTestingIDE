"""
The run summary's fold tables (#390, #557) — the two figures the summary folds itself.

Most of the run summary is copied from the sections it summarises; what it folds on its own is the
market time the run's units processed, and the run's weakest signal channel.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from python.framework.reporting.builders.run_unit import RunUnit
from python.framework.types.aggregation_types import FigureDeclaration, FoldTable, Reduction
from python.framework.types.api.report_types import RunSummary, SignalUsageRow
from python.framework.utils.time_utils import covered_seconds


def _spans(rows: Sequence[Any]) -> List[Tuple[datetime, datetime]]:
    """
    The units' tick spans, where a unit has both ends.

    Args:
        rows: The run units

    Returns:
        (first, last) per unit that has both
    """
    return [(u.first_tick_time, u.last_tick_time) for u in rows
            if u.first_tick_time and u.last_tick_time]


def span_seconds(row: Any) -> Optional[float]:
    """
    The market time one unit processed.

    Args:
        row: A run unit

    Returns:
        Last tick less first tick, in seconds; None when the unit lacks either end
    """
    if row.first_tick_time and row.last_tick_time:
        return (row.last_tick_time - row.first_tick_time).total_seconds()
    return None


def covered_market_time(combined: Dict[str, Any], rows: Sequence[Any]) -> Optional[float]:
    """
    The market time the units covered together — overlapping spans counted once.

    Args:
        combined: The figures being built
        rows: The run units

    Returns:
        The covered seconds, or None when no unit has a span
    """
    spans = _spans(rows)
    return covered_seconds(spans) if spans else None


RUN_SPAN_TABLE = FoldTable(
    name='run_summary_spans',
    figures={
        'tick_timespan_seconds': FigureDeclaration(Reduction.DERIVE),
        # Each unit's own span added up — overlaps counted as often as they ran
        'tick_timespan_total_seconds': FigureDeclaration(Reduction.SUM, source=span_seconds),
    },
    derivations={'tick_timespan_seconds': covered_market_time},
    row_model=RunUnit,
    served_as=RunSummary,
    serves_whole=False,
)


def fresh_ratio_of_a_used_channel(row: Any) -> Optional[float]:
    """
    One channel's fresh ratio, where it resolved any tick.

    Args:
        row: A signal usage row

    Returns:
        Its fresh ratio; None for a usage that processed no tick — nothing was decided on it
    """
    if row.fresh_ticks + row.stale_ticks + row.blind_ticks > 0:
        return row.fresh_ratio
    return None


SIGNAL_CHANNEL_TABLE = FoldTable(
    name='run_summary_signal',
    figures={
        # The weakest channel, not the mean: a mean would hide a dead channel behind a healthy
        # one, and the number exists to say how far the run's decisions can be trusted. None when
        # no SIGNAL worker resolved anything — not 1.0, which would claim a perfect feed.
        'signal_fresh_ratio': FigureDeclaration(Reduction.MIN,
                                                source=fresh_ratio_of_a_used_channel),
    },
    row_model=SignalUsageRow,
    served_as=RunSummary,
    serves_whole=False,
)
