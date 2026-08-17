"""
Data format version spans - which schema version covers which window of the archive.

The tick index records a data_format_version per file. Grouping consecutive files by that
version turns the per-file field into archive structure: which collector schema produced
which period.

The version states what the collector was CONFIGURED to declare, not how the tick timing
was obtained - it is an operator-set input of the collector, so it carries no claim about
whether collected_msc was recorded or reconstructed.

Pure functions over index entries - no IO, so the caller decides when (and whether) to pay
for the index.
"""

from typing import Dict, List

import pandas as pd

from python.framework.types.coverage_report_types import DataFormatVersionSpan


def build_version_spans(entries: List[Dict]) -> List[DataFormatVersionSpan]:
    """
    Group consecutive tick index entries into spans of one data_format_version.

    Args:
        entries: Tick index entries for one symbol, chronologically sorted (as the
            index stores them)

    Returns:
        List of spans in chronological order, empty when there are no entries
    """
    spans: List[DataFormatVersionSpan] = []

    for entry in entries:
        version = entry.get('data_format_version', 'unknown')
        start_time = pd.to_datetime(entry['start_time'], utc=True)
        end_time = pd.to_datetime(entry['end_time'], utc=True)
        tick_count = int(entry['tick_count'])

        # Extend the open span while the version holds
        if spans and spans[-1].version == version:
            current = spans[-1]
            current.end_time = end_time
            current.file_count += 1
            current.tick_count += tick_count
            continue

        spans.append(DataFormatVersionSpan(
            version=version,
            start_time=start_time,
            end_time=end_time,
            file_count=1,
            tick_count=tick_count
        ))

    return spans
