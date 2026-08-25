"""
Gap File Attribution Tests.

Whether a gap sits inside one collector file or across a boundary answers the operator
question behind every outage: was the collector running through it? The boundary alone
does not answer it — files roll at max_ticks_per_file, so a boundary can fall inside a
running session by coincidence (measured: 97 of 178 archive cases). The file's open time
separates the two. Pure function over index entries — no index, no IO.
"""

import pandas as pd

from python.framework.discoveries.data_coverage.gap_file_attribution import (
    attribute_gaps_to_files,
    parse_file_open_time,
)
from python.framework.types.coverage_report_types import Gap
from python.framework.utils.market_calendar import GapCategory


def _entry(file_name, start, end):
    """Build a minimal tick index entry.

    Args:
        file_name: Parquet file name (carries the collection start)
        start: First tick timestamp (ISO)
        end: Last tick timestamp (ISO)

    Returns:
        Index entry dict as the tick index stores it
    """
    return {'file': file_name, 'start_time': start, 'end_time': end}


def _gap(start, end):
    """Build a gap between two ISO timestamps.

    Args:
        start: Gap start (last tick before)
        end: Gap end (first tick after)

    Returns:
        Gap with the file fields still unattributed
    """
    gap_start = pd.Timestamp(start, tz='UTC')
    gap_end = pd.Timestamp(end, tz='UTC')
    return Gap(
        gap_seconds=(gap_end - gap_start).total_seconds(),
        category=GapCategory.LARGE,
        reason='test',
        gap_start=gap_start,
        gap_end=gap_end,
    )


class TestOpenTimeParsing:
    """The file name carries the collection start in the collector's own clock."""

    def test_utc_collector_name(self):
        """Kraken writes UTC, so the name needs no correction."""
        opened = parse_file_open_time('ADAUSD_20260728_233104.parquet', 0)
        assert opened == pd.Timestamp('2026-07-28 23:31:04', tz='UTC')

    def test_server_time_name_is_shifted(self):
        """MT5 writes broker server time — the offset registry brings it to UTC."""
        opened = parse_file_open_time('EURUSD_20260819_173456.parquet', -3)
        assert opened == pd.Timestamp('2026-08-19 14:34:56', tz='UTC')

    def test_name_without_timestamp(self):
        """A name that carries no stamp yields nothing, never a guess."""
        assert parse_file_open_time('EURUSD.parquet', 0) is None


class TestAttribution:
    """Where a gap sits, and what that says about the collector."""

    def test_gap_inside_one_file(self):
        """Both ends in the same file — the collector demonstrably ran through it."""
        entries = [_entry('BTC_20260728_233104.parquet',
                          '2026-07-28T23:31:04+00:00', '2026-08-02T09:30:53+00:00')]
        gaps = [_gap('2026-07-29T15:33:54+00:00', '2026-07-29T20:43:12+00:00')]

        attribute_gaps_to_files(gaps, entries, 0)

        assert gaps[0].file_before == 'BTC_20260728_233104.parquet'
        assert gaps[0].file_after == 'BTC_20260728_233104.parquet'
        assert gaps[0].next_file_opened_after_s is None

    def test_boundary_with_rollover_keeps_the_session(self):
        """The next file opened at the last tick — the file rolled, the collector stayed."""
        entries = [
            _entry('BTC_20260728_000000.parquet',
                   '2026-07-28T00:00:00+00:00', '2026-07-29T15:33:54+00:00'),
            _entry('BTC_20260729_153354.parquet',
                   '2026-07-29T20:43:12+00:00', '2026-07-30T00:00:00+00:00'),
        ]
        gaps = [_gap('2026-07-29T15:33:54+00:00', '2026-07-29T20:43:12+00:00')]

        attribute_gaps_to_files(gaps, entries, 0)

        assert gaps[0].file_before == 'BTC_20260728_000000.parquet'
        assert gaps[0].file_after == 'BTC_20260729_153354.parquet'
        assert gaps[0].next_file_opened_after_s == 0

    def test_boundary_with_late_reopen_shows_the_downtime(self):
        """The next file opened deep inside the gap — collection had stopped."""
        entries = [
            _entry('EUR_20250926_194306.parquet',
                   '2025-09-26T00:00:00+00:00', '2025-09-26T20:56:41+00:00'),
            _entry('EUR_20251010_235659.parquet',
                   '2025-10-12T21:01:00+00:00', '2025-10-13T00:00:00+00:00'),
        ]
        gaps = [_gap('2025-09-26T20:56:41+00:00', '2025-10-12T21:01:00+00:00')]

        attribute_gaps_to_files(gaps, entries, -3)

        # Opened 2025-10-10 20:56:59 UTC — 14 days after the last tick, inside the gap
        assert gaps[0].next_file_opened_after_s == 14 * 24 * 3600 + 18

    def test_segment_edges_inside_the_hole_still_find_the_boundary(self):
        """
        A gap split at a market boundary carries synthetic edges that lie in the
        hole between two files. Locating by start time alone put both edges in the
        preceding file and reported "collector was running" for a two-week outage —
        the report lying in the most damaging direction.
        """
        entries = [
            _entry('EUR_20250926_194306.parquet',
                   '2025-09-26T00:00:00+00:00', '2025-09-26T20:56:41+00:00'),
            _entry('EUR_20251010_235659.parquet',
                   '2025-10-12T21:01:00+00:00', '2025-10-17T00:00:00+00:00'),
        ]
        # Sub-segment produced by split_gap_at_market_boundaries: both edges sit
        # after the first file ended and before the second one started.
        gaps = [_gap('2025-09-28T22:00:00+00:00', '2025-10-03T20:00:00+00:00')]

        attribute_gaps_to_files(gaps, entries, -3)

        assert gaps[0].file_before == 'EUR_20250926_194306.parquet'
        assert gaps[0].file_after == 'EUR_20251010_235659.parquet'
        assert gaps[0].next_file_opened_after_s == 14 * 24 * 3600 + 18

    def test_gap_before_the_first_file_stays_unattributed(self):
        """Outside the indexed range nothing is claimed."""
        entries = [_entry('BTC_20260728_000000.parquet',
                          '2026-07-28T00:00:00+00:00', '2026-07-29T00:00:00+00:00')]
        gaps = [_gap('2026-07-01T00:00:00+00:00', '2026-07-02T00:00:00+00:00')]

        attribute_gaps_to_files(gaps, entries, 0)

        assert gaps[0].file_before is None
        assert gaps[0].file_after is None

    def test_no_entries_leaves_gaps_untouched(self):
        """An unindexed symbol must not crash the report."""
        gaps = [_gap('2026-07-29T15:33:54+00:00', '2026-07-29T20:43:12+00:00')]

        attribute_gaps_to_files(gaps, [], 0)

        assert gaps[0].file_before is None

    def test_entries_are_sorted_before_use(self):
        """The index is not guaranteed ordered — the attribution must order it."""
        entries = [
            _entry('BTC_20260729_153354.parquet',
                   '2026-07-29T20:43:12+00:00', '2026-07-30T00:00:00+00:00'),
            _entry('BTC_20260728_000000.parquet',
                   '2026-07-28T00:00:00+00:00', '2026-07-29T15:33:54+00:00'),
        ]
        gaps = [_gap('2026-07-29T15:33:54+00:00', '2026-07-29T20:43:12+00:00')]

        attribute_gaps_to_files(gaps, entries, 0)

        assert gaps[0].file_before == 'BTC_20260728_000000.parquet'
        assert gaps[0].file_after == 'BTC_20260729_153354.parquet'
