"""
Trade-id gap summary - the venue's trades a symbol's tick files do not hold.

The tick index measures each file's missing trade ids where every trade carries its id (collector
format 1.7.0 on, and a venue's published history). This sums them per symbol and origin, and names
the stretches that carry no ids, so the coverage report can say how complete our own collection is
and where that cannot be measured.

Pure functions over index entries - no IO, the same contract as the version spans.
"""

from typing import Dict, List

from python.framework.discoveries.data_coverage.data_format_version_spans import build_version_spans
from python.framework.types.config_types.data_origin_config_types import OriginClass
from python.framework.types.coverage_report_types import TradeIdGapReport, TradeIdGapSummary


def build_trade_id_gap_report(entries: List[Dict]) -> TradeIdGapReport:
    """
    Sum the measured trade-id gaps of a symbol's tick files per origin, and name what is unmeasured.

    Args:
        entries: Tick index entries for one symbol, chronologically sorted

    Returns:
        The summaries in the origin classes' declared order, and the unmeasured stretches
    """
    by_origin: Dict[str, List] = {}
    for entry in entries:
        if entry.get('trade_id_gaps') is not None:
            by_origin.setdefault(entry.get('origin_class', OriginClass.UNKNOWN.value), []).append(
                entry['trade_id_gaps'])
    declared = [origin.value for origin in OriginClass]
    summaries = []
    for origin_class in sorted(by_origin, key=lambda origin: (
            declared.index(origin) if origin in declared else len(declared), origin)):
        measured = by_origin[origin_class]
        longest = max(measured, key=lambda gaps: gaps.longest_run)
        summaries.append(TradeIdGapSummary(
            origin_class=origin_class,
            measured_files=len(measured),
            span=sum(gaps.span for gaps in measured),
            missing=sum(gaps.missing for gaps in measured),
            longest_run=longest.longest_run,
            longest_run_seconds=longest.longest_run_seconds,
            files_over_limit=sum(1 for gaps in measured if gaps.is_a_hole())))
    return TradeIdGapReport(
        summaries=summaries,
        unmeasured_spans=build_version_spans(
            [entry for entry in entries if entry.get('trade_id_gaps') is None]))
