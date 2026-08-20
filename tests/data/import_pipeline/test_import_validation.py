"""
Test Tick Import Validator.

One case per rejection reason plus the cross-file ordering plane. The validator
encodes invariants that were measured against the real archive before being
written down — the comments name the measurement each one rests on.
"""

import numpy as np
import pandas as pd
import pytest

from python.framework.validators.tick_import_validator import (
    PLAUSIBLE_LAG_WINDOW_MS,
    SEGMENT_SPLIT_FORWARD_MS,
    TickImportValidator,
)

BASE_MSC = 1768471200000
HOUR_MS = 3_600_000


def build_frame(
    tick_count: int = 50,
    lag_ms: int = -100,
    backwards_at: int = -1,
    anchor_jump: int = 0,
    jump_at: int = 25,
) -> pd.DataFrame:
    """
    Build a tick frame in the shape the importer hands to the validator.

    Args:
        tick_count: Number of ticks
        lag_ms: Offset of collected_msc against time_msc
        backwards_at: Index where collected_msc steps backwards (-1 = never)
        anchor_jump: Offset added from jump_at onwards, simulating an anchor break
        jump_at: Index where the anchor jump starts

    Returns:
        DataFrame with timestamp, time_msc, collected_msc, bid, ask
    """
    time_msc = BASE_MSC + np.arange(tick_count, dtype='int64') * 400
    collected = time_msc + lag_ms

    if anchor_jump:
        collected[jump_at:] += anchor_jump
    if backwards_at >= 0:
        collected[backwards_at] = collected[backwards_at - 1] - 500

    return pd.DataFrame({
        'timestamp': pd.to_datetime(time_msc, unit='ms'),
        'time_msc': time_msc,
        'collected_msc': collected,
        'bid': np.full(tick_count, 1.16000),
        'ask': np.full(tick_count, 1.16020),
    })


@pytest.fixture
def validator() -> TickImportValidator:
    """Provide a validator instance."""
    return TickImportValidator()


class TestHealthyFile:
    """A file satisfying every invariant must pass untouched."""

    def test_healthy_file_passes(self, validator):
        """Measured on live 1.5.0 data: lag around -100 ms, sigma 4.5 ms."""
        result = validator.validate_file(
            build_frame(), 'healthy.json',
            declared_tick_count=50, collected_msc_is_utc=True)

        assert result.is_valid
        assert result.errors == []
        assert result.metrics['segments'] == 1.0
        assert result.metrics['min_lag_ms'] == -100.0

    def test_simultaneous_arrivals_are_allowed(self, validator):
        """Equal stamps are legitimate — Kraken bursts several ticks per ms."""
        df = build_frame(tick_count=6)
        df['collected_msc'] = df['collected_msc'].iloc[0]

        result = validator.validate_file(df, 'burst.json')

        assert result.is_valid
        assert result.metrics['simultaneous_arrivals'] == 5.0


class TestRejectionReasons:
    """Each defect class the archive scan turned up gets its own case."""

    def test_backwards_collected_msc_rejected(self, validator):
        """Measured: zero backwards steps in 5148 archive files."""
        result = validator.validate_file(
            build_frame(backwards_at=30), 'backwards.json')

        assert not result.is_valid
        assert any('collected_msc steps backwards' in e for e in result.errors)

    def test_timezone_offset_rejected_with_migration_hint(self, validator):
        """Class A: device-local collected_msc, never converted to UTC."""
        result = validator.validate_file(
            build_frame(lag_ms=HOUR_MS), 'class_a.json',
            collected_msc_is_utc=False)

        assert not result.is_valid
        assert any('restore_collected_msc_v3' in e for e in result.errors)

    def test_utc_declaring_file_gets_collector_verdict(self, validator):
        """A file declaring the UTC timebase has no legacy excuse."""
        result = validator.validate_file(
            build_frame(lag_ms=HOUR_MS), 'class_a_utc.json',
            collected_msc_is_utc=True)

        assert not result.is_valid
        assert any('collector defect' in e for e in result.errors)

    def test_anchor_overflow_is_split_into_segments(self, validator):
        """Class C: the 2^64-scale jump measured in 96 archive files."""
        result = validator.validate_file(
            build_frame(anchor_jump=18_446_742_229_035_831), 'class_c.json')

        assert not result.is_valid
        assert result.metrics['segments'] == 2.0

    def test_tick_count_mismatch_rejected(self, validator):
        """The file's own summary must agree with what it delivers."""
        result = validator.validate_file(
            build_frame(tick_count=50), 'count.json', declared_tick_count=99)

        assert not result.is_valid
        assert any('Tick count mismatch' in e for e in result.errors)

    def test_inverted_spread_rejected(self, validator):
        """Ask below bid is structurally impossible, not a market condition."""
        df = build_frame()
        df.loc[10, 'ask'] = 1.15000

        result = validator.validate_file(df, 'inverted.json')

        assert not result.is_valid
        assert any('inverted spread' in e for e in result.errors)

    def test_timestamp_time_msc_disagreement_rejected(self, validator):
        """Both come from one MqlTick; measured deviation never exceeds 999 ms."""
        df = build_frame()
        df['timestamp'] = df['timestamp'] + pd.Timedelta(days=6)

        result = validator.validate_file(df, 'stamp_drift.json')

        assert not result.is_valid
        assert any('timestamp and time_msc disagree' in e for e in result.errors)

    def test_empty_file_rejected(self, validator):
        """An empty tick array carries no data to import."""
        result = validator.validate_file(
            build_frame().iloc[0:0], 'empty.json')

        assert not result.is_valid


class TestTolerances:
    """The window boundaries are measured values, not round numbers."""

    def test_lag_at_window_edge_accepted(self, validator):
        """30 s sits far above any real receive lag and far below 1 h."""
        result = validator.validate_file(
            build_frame(lag_ms=PLAUSIBLE_LAG_WINDOW_MS), 'edge.json')

        assert result.is_valid

    def test_lag_beyond_window_rejected(self, validator):
        """One millisecond past the window is already a defect."""
        result = validator.validate_file(
            build_frame(lag_ms=PLAUSIBLE_LAG_WINDOW_MS + 1), 'past_edge.json')

        assert not result.is_valid

    def test_weekend_sized_gap_is_not_a_segment_break(self, validator):
        """Largest legitimate intra-file gap measured: 48.17 h."""
        df = build_frame(tick_count=10)
        weekend = 48 * HOUR_MS
        df.loc[5:, 'time_msc'] += weekend
        df.loc[5:, 'collected_msc'] += weekend
        df['timestamp'] = pd.to_datetime(df['time_msc'], unit='ms')

        result = validator.validate_file(df, 'weekend.json')

        assert result.is_valid
        assert result.metrics['segments'] == 1.0

    def test_gap_beyond_split_threshold_is_a_segment_break(self, validator):
        """Smallest measured anchor jump: 21.35 d, well past the 7 d threshold."""
        df = build_frame(tick_count=10)
        df.loc[5:, 'collected_msc'] += SEGMENT_SPLIT_FORWARD_MS + 1

        result = validator.validate_file(df, 'anchor_break.json')

        assert result.metrics['segments'] == 2.0


class TestMissingColumns:
    """Pre-V1.3.0 exports lack timing columns — that is not a defect."""

    def test_missing_collected_msc_warns_only(self, validator):
        """Old data has no arrival clock; nothing to assert, nothing to reject."""
        df = build_frame().drop(columns=['collected_msc'])

        result = validator.validate_file(df, 'legacy.json')

        assert result.is_valid

    def test_zero_collected_msc_warns_only(self, validator):
        """The importer defaults the column to 0 when the source lacks it."""
        df = build_frame()
        df['collected_msc'] = 0

        result = validator.validate_file(df, 'zeroed.json')

        assert result.is_valid
        assert any('pre-V1.3.0' in w for w in result.warnings)


class TestArchiveOrdering:
    """Cross-file plane — runs off the index, opens no data file."""

    def test_ordered_archive_has_no_findings(self, validator):
        """Measured: zero overlaps across all 5222 archive file pairs."""
        entries = {'mt5': {'EURUSD': [
            {'file': 'a.parquet', 'start_time': '2026-01-15T10:00:00+00:00',
             'end_time': '2026-01-15T11:00:00+00:00',
             'collected_start': 1768471200000, 'collected_end': 1768474800000},
            {'file': 'b.parquet', 'start_time': '2026-01-15T11:00:00+00:00',
             'end_time': '2026-01-15T12:00:00+00:00',
             'collected_start': 1768474800000, 'collected_end': 1768478400000},
        ]}}

        assert validator.validate_archive_ordering(entries) == []

    def test_overlap_is_reported(self, validator):
        """Two collectors on one symbol would produce exactly this."""
        entries = {'mt5': {'EURUSD': [
            {'file': 'a.parquet', 'start_time': '2026-01-15T10:00:00+00:00',
             'end_time': '2026-01-15T11:30:00+00:00',
             'collected_start': 1768471200000, 'collected_end': 1768476600000},
            {'file': 'b.parquet', 'start_time': '2026-01-15T11:00:00+00:00',
             'end_time': '2026-01-15T12:00:00+00:00',
             'collected_start': 1768476600000, 'collected_end': 1768478400000},
        ]}}

        findings = validator.validate_archive_ordering(entries)

        assert len(findings) == 1
        assert 'overlaps' in findings[0]

    def test_backwards_arrival_across_files_is_reported(self, validator):
        """Arrival is a physical sequence — it cannot run backwards between files."""
        entries = {'mt5': {'EURUSD': [
            {'file': 'a.parquet', 'start_time': '2026-01-15T10:00:00+00:00',
             'end_time': '2026-01-15T11:00:00+00:00',
             'collected_start': 1768471200000, 'collected_end': 1768474800000},
            {'file': 'b.parquet', 'start_time': '2026-01-15T11:00:00+00:00',
             'end_time': '2026-01-15T12:00:00+00:00',
             'collected_start': 1768474795000, 'collected_end': 1768478400000},
        ]}}

        findings = validator.validate_archive_ordering(entries)

        assert len(findings) == 1
        assert 'collected_msc steps back 5000 ms' in findings[0]

    def test_continuous_arrival_across_files_passes(self, validator):
        """Event times and arrival times both continuous — nothing to report."""
        entries = {'mt5': {'EURUSD': [
            {'file': 'a.parquet', 'start_time': '2026-01-15T10:00:00+00:00',
             'end_time': '2026-01-15T11:00:00+00:00',
             'collected_start': 1768471200000, 'collected_end': 1768474800000},
            {'file': 'b.parquet', 'start_time': '2026-01-15T11:00:00+00:00',
             'end_time': '2026-01-15T12:00:00+00:00',
             'collected_start': 1768474800000, 'collected_end': 1768478400000},
        ]}}

        assert validator.validate_archive_ordering(entries) == []

    def test_missing_arrival_bounds_are_reported_as_unverified(self, validator):
        """An index without arrival bounds must say so — a skipped plane that
        logs like a passed one is how the check silently did nothing."""
        entries = {'mt5': {'EURUSD': [
            {'file': 'a.parquet', 'start_time': '2026-01-15T10:00:00+00:00',
             'end_time': '2026-01-15T11:00:00+00:00'},
            {'file': 'b.parquet', 'start_time': '2026-01-15T11:00:00+00:00',
             'end_time': '2026-01-15T12:00:00+00:00'},
        ]}}

        findings = validator.validate_archive_ordering(entries)

        assert len(findings) == 1
        assert 'NOT verified' in findings[0]

    def test_unverified_transitions_are_reported_once(self, validator):
        """One aggregate line for the whole run, not one per symbol."""
        entries = {'mt5': {
            'EURUSD': [
                {'file': 'a.parquet', 'start_time': '2026-01-15T10:00:00+00:00',
                 'end_time': '2026-01-15T11:00:00+00:00'},
                {'file': 'b.parquet', 'start_time': '2026-01-15T11:00:00+00:00',
                 'end_time': '2026-01-15T12:00:00+00:00'},
            ],
            'GBPUSD': [
                {'file': 'c.parquet', 'start_time': '2026-01-15T10:00:00+00:00',
                 'end_time': '2026-01-15T11:00:00+00:00'},
                {'file': 'd.parquet', 'start_time': '2026-01-15T11:00:00+00:00',
                 'end_time': '2026-01-15T12:00:00+00:00'},
            ],
        }}

        findings = validator.validate_archive_ordering(entries)

        assert len(findings) == 1
        assert '2 of 2 file transitions' in findings[0]

    def test_touching_files_are_not_an_overlap(self, validator):
        """Measured: 227 archive pairs touch at exactly 0 ms distance."""
        entries = {'mt5': {'EURUSD': [
            {'file': 'a.parquet', 'start_time': '2026-01-15T10:00:00+00:00',
             'end_time': '2026-01-15T11:00:00+00:00',
             'collected_start': 1768471200000, 'collected_end': 1768474800000},
            {'file': 'b.parquet', 'start_time': '2026-01-15T11:00:00+00:00',
             'end_time': '2026-01-15T12:00:00+00:00',
             'collected_start': 1768474800000, 'collected_end': 1768478400000},
        ]}}

        assert validator.validate_archive_ordering(entries) == []
