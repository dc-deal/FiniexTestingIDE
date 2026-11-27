"""
The feed stability's fold tables (#451, #557).

A source row folds the episodes of one disturbed source and domain; its per-tick counters fold from
the units' counters on that source and are joined onto the rows the episodes created. The report's
totals fold over the source rows — two levels, as the report was always added up: a total over all
episodes at once would add the same seconds in another order.
"""

from typing import Any, Dict, List, Sequence

from python.framework.types.aggregation_types import (
    Addition,
    FigureDeclaration,
    FoldTable,
    GroupOrder,
    Reduction,
)
from python.framework.types.api.report_types import (
    FeedStabilityEpisodeRow,
    FeedStabilityReport,
    FeedStabilitySourceRow,
)
from python.framework.types.disturbance_episode_types import (
    DisturbanceOrigin,
    FeedCountersOfSource,
    FeedEpisodeInSource,
)


def episodes_as_served(combined: Dict[str, Any], rows: Sequence[Any]) -> List[FeedStabilityEpisodeRow]:
    """
    The source's episodes, as the report lists them — in the order they occurred across the units.

    Args:
        combined: The figures being built
        rows: The source's episodes

    Returns:
        One served row per episode
    """
    return [FeedStabilityEpisodeRow(unit=r.unit, symbol=r.symbol, stale_from=r.stale_from,
                                    stale_to=r.stale_to, duration_seconds=r.duration_seconds,
                                    origin=r.origin, label=r.label) for r in rows]


def origins_in_order(combined: Dict[str, Any], rows: Sequence[Any]) -> List[str]:
    """
    Where the source's episodes came from, each origin once, in the order first seen.

    Args:
        combined: The figures being built
        rows: The source's episodes

    Returns:
        The distinct origins
    """
    origins: List[str] = []
    for row in rows:
        if row.origin not in origins:
            origins.append(row.origin)
    return origins


FEED_COUNTER_TABLE = FoldTable(
    name='feed_stability_counters',
    figures={
        'fresh_ticks': FigureDeclaration(Reduction.SUM),
        'stale_ticks': FigureDeclaration(Reduction.SUM),
        'blind_ticks': FigureDeclaration(Reduction.SUM),
    },
    order=GroupOrder.FIRST_SEEN,
    grain=('source', 'domain'),
    row_model=FeedCountersOfSource,
    served_as=FeedStabilitySourceRow,
    serves_whole=False,
)


FEED_SOURCE_TABLE = FoldTable(
    name='feed_stability_source',
    figures={
        'source': FigureDeclaration(Reduction.FIRST_ROW),
        'domain': FigureDeclaration(Reduction.FIRST_ROW),
        # Added in a loop, as the row always added its episodes — #657
        'stale_seconds': FigureDeclaration(Reduction.SUM, source='duration_seconds',
                                           addition=Addition.SEQUENTIAL),
        'episode_count': FigureDeclaration(Reduction.COUNT),
        'origins': FigureDeclaration(Reduction.DERIVE),
        'episodes': FigureDeclaration(Reduction.DERIVE),
    },
    derivations={'origins': origins_in_order, 'episodes': episodes_as_served},
    order=GroupOrder.SORTED,
    grain=('source', 'domain'),
    row_model=FeedEpisodeInSource,
    served_as=FeedStabilitySourceRow,
    outside_the_fold={
        name: "joined from the units' counters, folded by their table"
        for name in FEED_COUNTER_TABLE.figures},
)

def stress_episodes(row: Any) -> int:
    """
    A source row's episodes that a stress configuration injected.

    Args:
        row: A served source row

    Returns:
        Their count
    """
    return sum(1 for episode in row.episodes
               if episode.origin == DisturbanceOrigin.STRESS_INJECTED.value)


FEED_TOTALS_TABLE = FoldTable(
    name='feed_stability_totals',
    figures={
        'episode_count': FigureDeclaration(Reduction.SUM),
        'stale_seconds': FigureDeclaration(Reduction.SUM),
        'stress_injected_count': FigureDeclaration(Reduction.SUM, source=stress_episodes),
        # A source in two domains is two rows, and counts twice
        'source_count': FigureDeclaration(Reduction.COUNT),
    },
    row_model=FeedStabilitySourceRow,
    served_as=FeedStabilityReport,
    serves_whole=False,
)
