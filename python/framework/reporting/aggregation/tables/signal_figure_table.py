"""
The signal report's fold tables (#433, #557).

The SIGNAL workers' counters per run unit and symbol, a source's archive facts over its symbols'
coverage reports, and — on the feed path — the provenance values a live series carried.
"""

from datetime import datetime
from typing import Any, Dict, Optional, Sequence, Tuple

from python.framework.discoveries.signal_coverage.signal_coverage_report import SignalCoverageReport
from python.framework.types.aggregation_types import (
    FigureDeclaration,
    FoldTable,
    GroupOrder,
    Reduction,
)
from python.framework.types.api.report_types import SignalSourceRow
from python.framework.types.signal_data_types import SignalResolutionStats, SignalStatsOfUnit
from python.framework.utils.time_utils import format_iso_or_empty

# A unit may run several SIGNAL workers on its one source (e.g. different staleness thresholds);
# their TICK counters are summed, because the report's granularity is the scenario's use of a
# source, not the worker instance. The clamp counters merge by MAX: they describe the SERIES, and
# those workers share ONE provider — summing would multiply a single producer clock correction by
# the number of workers that happened to read it.
SIGNAL_RESOLUTION_TABLE = FoldTable(
    name='signal_resolution',
    figures={
        'unit_name': FigureDeclaration(Reduction.FIRST_ROW),
        'symbol': FigureDeclaration(Reduction.FIRST_ROW),
        'worker_name': FigureDeclaration(Reduction.FIRST_ROW),
        'signal_kind': FigureDeclaration(Reduction.FIRST_ROW),
        'fresh_ticks': FigureDeclaration(Reduction.SUM),
        'stale_ticks': FigureDeclaration(Reduction.SUM),
        'blind_ticks': FigureDeclaration(Reduction.SUM),
        'availability_clamps': FigureDeclaration(Reduction.MAX),
        'max_clamp_correction_ms': FigureDeclaration(Reduction.MAX),
    },
    order=GroupOrder.FIRST_SEEN,
    grain=('unit_name', 'symbol'),
    row_model=SignalStatsOfUnit,
    served_as=SignalResolutionStats,
    outside_the_fold={
        'unit_name': 'folded, not served: the key the counters are looked up by',
        'off_tick_arrivals': 'per worker only: the merged counters serve its default',
    },
)


def sequence_description(row: SignalCoverageReport) -> str:
    """
    How a coverage report describes its series.

    Args:
        row: A symbol's coverage report

    Returns:
        Its sequence description
    """
    return row.get_sequence_description()


def data_origin(row: SignalCoverageReport) -> str:
    """
    The origin a coverage report's envelopes stated.

    Args:
        row: A symbol's coverage report

    Returns:
        The origin, '' when unknown
    """
    return row.get_data_origin()


def config_fingerprint(row: SignalCoverageReport) -> str:
    """
    The producer configuration a coverage report's envelopes stated.

    Args:
        row: A symbol's coverage report

    Returns:
        The fingerprint, '' when unknown
    """
    return row.get_config_fingerprint()


def cadence(row: SignalCoverageReport) -> float:
    """
    A coverage report's snapshot cadence.

    Args:
        row: A symbol's coverage report

    Returns:
        Seconds between snapshots
    """
    return row.cadence_seconds


def snapshots(row: SignalCoverageReport) -> int:
    """
    A coverage report's snapshot count.

    Args:
        row: A symbol's coverage report

    Returns:
        The count
    """
    return row.snapshot_count


def archive_begin(row: SignalCoverageReport) -> Optional[datetime]:
    """
    Where a coverage report's archive begins.

    Args:
        row: A symbol's coverage report

    Returns:
        Its first instant, or None
    """
    return row.start_time


def archive_finish(row: SignalCoverageReport) -> Optional[datetime]:
    """
    Where a coverage report's archive ends.

    Args:
        row: A symbol's coverage report

    Returns:
        Its last instant, or None
    """
    return row.end_time


def archive_start(combined: Dict[str, Any], rows: Sequence[Any]) -> str:
    """
    The source's earliest archive instant, as served.

    Args:
        combined: The figures being built, the earliest instant already folded
        rows: The coverage reports

    Returns:
        ISO-8601, '' when no symbol has one
    """
    return format_iso_or_empty(combined['earliest_start'])


def archive_end(combined: Dict[str, Any], rows: Sequence[Any]) -> str:
    """
    The source's latest archive instant, as served.

    Args:
        combined: The figures being built, the latest instant already folded
        rows: The coverage reports

    Returns:
        ISO-8601, '' when no symbol has one
    """
    return format_iso_or_empty(combined['latest_end'])


def gap_counts(combined: Dict[str, Any], rows: Sequence[Any]) -> Dict[str, int]:
    """
    The gap counts summed across a source's symbols, empty categories dropped.

    Args:
        combined: The figures being built
        rows: The coverage reports

    Returns:
        Category → count, in first-seen order
    """
    merged: Dict[str, int] = {}
    for coverage in rows:
        for category, count in coverage.gap_counts.items():
            if count:
                merged[category] = merged.get(category, 0) + count
    return merged


def _first_trigger_composition(rows: Sequence[Any]) -> Tuple[Dict[str, int], int]:
    """
    A source's trigger composition — envelope-level, so taken from ONE coverage.

    Summing across symbols would multiply every envelope by its symbol count. The unknown count
    travels with the composition: both come from the same coverage, so the pair always describes
    the same envelope set.

    Args:
        rows: The coverage reports

    Returns:
        (composition, count of envelopes carrying no reason) of the first coverage that has either
    """
    for coverage in rows:
        if coverage.trigger_reasons or coverage.trigger_unknown:
            return dict(coverage.trigger_reasons), coverage.trigger_unknown
    return {}, 0


def trigger_reasons(combined: Dict[str, Any], rows: Sequence[Any]) -> Dict[str, int]:
    """
    A source's trigger composition.

    Args:
        combined: The figures being built
        rows: The coverage reports

    Returns:
        Reason → envelopes
    """
    return _first_trigger_composition(rows)[0]


def trigger_unknown(combined: Dict[str, Any], rows: Sequence[Any]) -> int:
    """
    A source's envelopes that carried no trigger reason.

    Args:
        combined: The figures being built
        rows: The coverage reports

    Returns:
        Their count, from the same coverage as the composition
    """
    return _first_trigger_composition(rows)[1]


# The provenance scalars are envelope-level, so they agree across a source's symbols; the first
# coverage carrying values is representative.
SIGNAL_COVERAGE_TABLE = FoldTable(
    name='signal_coverage',
    figures={
        'sequence': FigureDeclaration(Reduction.FIRST_ROW, source=sequence_description),
        'data_origin': FigureDeclaration(Reduction.IDENTITY, source=data_origin, empty_value=''),
        'config_fingerprint': FigureDeclaration(Reduction.IDENTITY, source=config_fingerprint,
                                                empty_value=''),
        'cadence_seconds': FigureDeclaration(Reduction.MAX, source=cadence, empty_value=0.0),
        'snapshot_count': FigureDeclaration(Reduction.MAX, source=snapshots, empty_value=0),
        'earliest_start': FigureDeclaration(Reduction.MIN, source=archive_begin),
        'latest_end': FigureDeclaration(Reduction.MAX, source=archive_finish),
        'archive_start': FigureDeclaration(Reduction.DERIVE),
        'archive_end': FigureDeclaration(Reduction.DERIVE),
        'gap_counts': FigureDeclaration(Reduction.DERIVE),
        'trigger_reasons': FigureDeclaration(Reduction.DERIVE),
        'trigger_unknown': FigureDeclaration(Reduction.DERIVE),
    },
    derivations={
        'archive_start': archive_start,
        'archive_end': archive_end,
        'gap_counts': gap_counts,
        'trigger_reasons': trigger_reasons,
        'trigger_unknown': trigger_unknown,
    },
    row_model=SignalCoverageReport,
    served_as=SignalSourceRow,
    serves_whole=False,
    outside_the_fold={
        'earliest_start': 'folded, not served: the archive start is served as ISO-8601',
        'latest_end': 'folded, not served: the archive end is served as ISO-8601',
    },
)


def one_or_mixed(combined: Dict[str, Any], rows: Sequence[Any]) -> str:
    """
    A live series' provenance value: the one it carried, 'mixed' when it carried several.

    Args:
        combined: The figures being built
        rows: The distinct values the series carried

    Returns:
        The single value, 'mixed', or '' for unknown
    """
    ordered = sorted(value for value in rows if value)
    if not ordered:
        return ''
    return ordered[0] if len(ordered) == 1 else 'mixed'


FEED_PROVENANCE_TABLE = FoldTable(
    name='signal_feed_provenance',
    figures={'value': FigureDeclaration(Reduction.DERIVE)},
    derivations={'value': one_or_mixed},
)
