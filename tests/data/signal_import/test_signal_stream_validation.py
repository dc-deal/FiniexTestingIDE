"""
FiniexTestingIDE - Signal Stream Identity Validation (#141 Part 2a)

The importer's guard on the producer's stream identity. A seq HOLE is reported and imported
(the file is incomplete, not wrong); a REWOUND series is refused, because seq is unique only
within an epoch and two series would otherwise merge under one key.
"""

import json

import pytest

from python.data_management.importers.signal_importer import SignalDataImporter
from python.framework.exceptions.signal_data_errors import SignalSchemaError

PIPELINE_ID = 'test_sentiment'


def envelope(offset_s: int, seq=None, stream_epoch=None) -> dict:
    """Build one archive line, optionally carrying stream identity."""
    base = 1783970240000
    line = {
        'schema_version': '1.0',
        'pipeline_id': PIPELINE_ID,
        'collected_msc': base + offset_s * 1000,
        'status': 'success',
        'result': [{'symbol': 'BTCUSD', 'signal': 'BUY'}],
    }
    if seq is not None:
        line.update(seq=seq, stream_epoch=stream_epoch,
                    available_msc=base + offset_s * 1000 - 100)
    return line


def run_import(tmp_path, lines):
    """Import one JSONL file built from the given lines; return the importer."""
    raw = tmp_path / 'raw' / PIPELINE_ID
    raw.mkdir(parents=True)
    jsonl = raw / '2026-08-20.jsonl'
    jsonl.write_text('\n'.join(json.dumps(line) for line in lines) + '\n')
    importer = SignalDataImporter(
        source_dir=str(tmp_path / 'raw'), target_dir=str(tmp_path / 'processed'),
        override=True)
    importer.convert_jsonl_to_parquet(jsonl)
    return importer


class TestAccepted:
    """What must import without complaint."""

    def test_contiguous_sequence_is_clean(self, tmp_path):
        importer = run_import(tmp_path, [
            envelope(1, seq=1, stream_epoch=1),
            envelope(2, seq=2, stream_epoch=1),
            envelope(3, seq=3, stream_epoch=1),
        ])
        assert importer.errors == []

    def test_clean_epoch_bump_restarts_the_numbering(self, tmp_path):
        """A reset legitimately starts seq over; that is not a hole."""
        importer = run_import(tmp_path, [
            envelope(1, seq=1, stream_epoch=1),
            envelope(2, seq=2, stream_epoch=1),
            envelope(3, seq=1, stream_epoch=2),
        ])
        assert importer.errors == []

    def test_pre_stream_lines_are_unverifiable_not_invalid(self, tmp_path):
        """The archive before the stream contract carries no identity at all."""
        importer = run_import(tmp_path, [envelope(1), envelope(2)])
        assert importer.errors == []


class TestReported:
    """A hole costs envelopes but not the file."""

    def test_missing_seq_is_reported_and_imported(self, tmp_path):
        importer = run_import(tmp_path, [
            envelope(1, seq=1, stream_epoch=1),
            envelope(2, seq=5, stream_epoch=1),
        ])
        assert len(importer.errors) == 1
        assert '3 missing seq' in importer.errors[0]

    def test_holes_are_counted_per_epoch(self, tmp_path):
        importer = run_import(tmp_path, [
            envelope(1, seq=1, stream_epoch=1),
            envelope(2, seq=3, stream_epoch=1),
            envelope(3, seq=1, stream_epoch=2),
            envelope(4, seq=4, stream_epoch=2),
        ])
        assert len(importer.errors) == 2


class TestRefused:
    """A rewound series must never merge into an existing one."""

    def test_epoch_going_backwards_is_refused(self, tmp_path):
        with pytest.raises(SignalSchemaError, match='went backwards'):
            run_import(tmp_path, [
                envelope(1, seq=1, stream_epoch=2),
                envelope(2, seq=5, stream_epoch=1),
            ])

    def test_reissued_epoch_is_refused(self, tmp_path):
        """A restore reissues an epoch; seq then steps backwards inside it."""
        with pytest.raises(SignalSchemaError, match='seq went backwards'):
            run_import(tmp_path, [
                envelope(1, seq=1200, stream_epoch=1),
                envelope(2, seq=1051, stream_epoch=1),
            ])


class TestTriggerReasonAcrossTheBoundary:
    """
    The producer promoted trigger_reason out of metadata; both eras must land in one column.

    Getting this wrong is silent: an unread trigger renders as '' = unknown, which looks like
    the pre-contract era rather than like a bug.
    """

    def _import_one(self, tmp_path, line):
        """Import a single line and return its trigger_reason column value."""
        import pandas as pd
        raw = tmp_path / 'raw' / PIPELINE_ID
        raw.mkdir(parents=True)
        jsonl = raw / '2026-08-20.jsonl'
        jsonl.write_text(json.dumps(line) + '\n')
        importer = SignalDataImporter(
            source_dir=str(tmp_path / 'raw'),
            target_dir=str(tmp_path / 'processed'), override=True)
        return pd.read_parquet(
            importer.convert_jsonl_to_parquet(jsonl))['trigger_reason'].iloc[0]

    def test_legacy_metadata_location_is_read(self, tmp_path):
        line = envelope(1)
        line['metadata'] = {'trigger_reason': 'breaking', 'model': 'gpt-4o-mini'}
        assert self._import_one(tmp_path, line) == 'breaking'

    def test_promoted_top_level_is_read(self, tmp_path):
        line = envelope(1)
        line['trigger_reason'] = 'boot'
        assert self._import_one(tmp_path, line) == 'boot'

    def test_absent_stays_unknown(self, tmp_path):
        """Never 'scheduled' — that would mislabel a boot pass as a grid point."""
        assert self._import_one(tmp_path, envelope(1)) == ''
