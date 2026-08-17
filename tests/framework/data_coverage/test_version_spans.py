"""
Data Format Version Span Tests.

Spans group consecutive tick index entries by their data_format_version, so the coverage
report can state which collector schema produced which window of the archive. Pure function
over index entries — no index, no IO.
"""

from python.framework.discoveries.data_coverage.data_format_version_spans import (
    build_version_spans)


def _entry(version, start, end, ticks=100):
    """Build a minimal tick index entry.

    Args:
        version: data_format_version string
        start: Start timestamp (ISO date, 'YYYY-MM-DD')
        end: End timestamp (ISO date, 'YYYY-MM-DD')
        ticks: Tick count for the file

    Returns:
        Index entry dict as the tick index stores it
    """
    return {
        'start_time': f"{start}T00:00:00+00:00",
        'end_time': f"{end}T00:00:00+00:00",
        'tick_count': ticks,
        'data_format_version': version,
    }


class TestSpanGrouping:
    """Verify consecutive entries collapse into spans correctly."""

    def test_empty_entries(self):
        """No entries → no spans."""
        assert build_version_spans([]) == []

    def test_single_file(self):
        """One file → one span carrying its own boundaries."""
        spans = build_version_spans(
            [_entry('1.3.0', '2026-01-01', '2026-01-02', ticks=500)])

        assert len(spans) == 1
        assert spans[0].version == '1.3.0'
        assert spans[0].file_count == 1
        assert spans[0].tick_count == 500

    def test_contiguous_run_collapses(self):
        """Three files of one version → one span spanning first start to last end."""
        spans = build_version_spans([
            _entry('1.2.0', '2026-01-01', '2026-01-02', ticks=10),
            _entry('1.2.0', '2026-01-02', '2026-01-03', ticks=20),
            _entry('1.2.0', '2026-01-03', '2026-01-04', ticks=30),
        ])

        assert len(spans) == 1
        assert spans[0].file_count == 3
        assert spans[0].tick_count == 60
        assert spans[0].start_time.strftime('%Y-%m-%d') == '2026-01-01'
        assert spans[0].end_time.strftime('%Y-%m-%d') == '2026-01-04'

    def test_version_change_opens_new_span(self):
        """A version change ends the open span."""
        spans = build_version_spans([
            _entry('1.2.0', '2026-01-01', '2026-01-02'),
            _entry('1.3.0', '2026-01-02', '2026-01-03'),
        ])

        assert [s.version for s in spans] == ['1.2.0', '1.3.0']

    def test_interleaved_versions_are_not_merged(self):
        """Re-imports producing A-B-A must stay three spans, not two."""
        spans = build_version_spans([
            _entry('1.2.0', '2026-01-01', '2026-01-02'),
            _entry('1.3.0', '2026-01-02', '2026-01-03'),
            _entry('1.2.0', '2026-01-03', '2026-01-04'),
        ])

        assert [s.version for s in spans] == ['1.2.0', '1.3.0', '1.2.0']

    def test_missing_version_key_reads_unknown(self):
        """An entry without the field must not raise."""
        entry = _entry('1.3.0', '2026-01-01', '2026-01-02')
        del entry['data_format_version']

        assert build_version_spans([entry])[0].version == 'unknown'
