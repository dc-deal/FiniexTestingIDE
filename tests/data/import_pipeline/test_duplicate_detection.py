"""
Test Duplicate Detection.

Tests that the importer correctly detects and handles duplicate imports, and that a file whose
ticks fall inside time the archive already covers is refused before anything is written.
"""

from pathlib import Path

import pytest

from python.data_management.importers.tick_data_importer import TickDataImporter
from python.framework.exceptions.data_quality_errors import TickFileValidationException
from tests.data.import_pipeline.conftest import (
    build_minimal_tick_json,
    find_tick_parquets,
    write_json_fixture,
)


class TestDuplicateDetection:
    """Verify duplicate import prevention and override behavior."""

    def test_first_import_no_duplicate(self, tmp_path):
        """First import of a file should succeed without duplicate error."""
        source = tmp_path / 'source'
        target = tmp_path / 'target'
        data = build_minimal_tick_json(
            symbol='BTCUSD',
            broker_type='kraken_spot',
            tick_count=5,
        )
        write_json_fixture(source, 'BTCUSD_20260115_ticks.json', data)

        importer = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            auto_render_bars=False,
        )
        importer.process_all_exports()

        assert importer.processed_files == 1
        assert len(importer.errors) == 0

    def test_second_import_detects_duplicate(self, tmp_path):
        """Importing the same JSON file again should detect duplicate."""
        source = tmp_path / 'source'
        target = tmp_path / 'target'
        data = build_minimal_tick_json(
            symbol='ETHUSD',
            broker_type='kraken_spot',
            tick_count=5,
        )

        # First import
        write_json_fixture(source, 'ETHUSD_20260115_ticks.json', data)
        importer1 = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            auto_render_bars=False,
        )
        importer1.process_all_exports()
        assert importer1.processed_files == 1

        # Second import — same source file name
        write_json_fixture(source, 'ETHUSD_20260115_ticks.json', data)
        importer2 = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            auto_render_bars=False,
        )
        importer2.process_all_exports()

        # Should have duplicate warning (skipped, not imported)
        assert len(importer2.warnings) > 0
        assert 'DUPLICATE' in importer2.warnings[0]

    def test_override_allows_reimport(self, tmp_path):
        """With override=True, duplicate file should be replaced."""
        source = tmp_path / 'source'
        target = tmp_path / 'target'
        data = build_minimal_tick_json(
            symbol='ADAUSD',
            broker_type='kraken_spot',
            tick_count=5,
        )

        # First import
        write_json_fixture(source, 'ADAUSD_20260115_ticks.json', data)
        importer1 = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            auto_render_bars=False,
        )
        importer1.process_all_exports()

        # Second import with override
        write_json_fixture(source, 'ADAUSD_20260115_ticks.json', data)
        importer2 = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            override=True,
            auto_render_bars=False,
        )
        importer2.process_all_exports()

        assert importer2.processed_files == 1
        assert len(importer2.errors) == 0

    def test_different_source_no_duplicate(self, tmp_path):
        """Different source filenames should not trigger duplicate detection."""
        source = tmp_path / 'source'
        target = tmp_path / 'target'

        # First file
        data1 = build_minimal_tick_json(
            symbol='XRPUSD',
            broker_type='kraken_spot',
            tick_count=3,
            start_time='2026.01.15 10:00:00',
        )
        write_json_fixture(source, 'XRPUSD_20260115_ticks.json', data1)
        importer1 = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            auto_render_bars=False,
        )
        importer1.process_all_exports()

        # Different file (different name = different source)
        data2 = build_minimal_tick_json(
            symbol='XRPUSD',
            broker_type='kraken_spot',
            tick_count=3,
            start_time='2026.01.16 10:00:00',
        )
        # Remove old file from source
        for f in source.glob('*'):
            f.unlink()
        write_json_fixture(source, 'XRPUSD_20260116_ticks.json', data2)
        importer2 = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            auto_render_bars=False,
        )
        importer2.process_all_exports()

        assert importer2.processed_files == 1
        assert len(importer2.errors) == 0

        # Should have 2 parquet files now
        parquet_files = find_tick_parquets(target)
        assert len(parquet_files) == 2


def _write(source: Path, name: str, start_time: str, tick_count: int = 5) -> Path:
    """
    Write one DOTUSD export whose ticks are one second apart from `start_time` on.

    Args:
        source: Inbox directory
        name: File name
        start_time: First tick, 'YYYY.MM.DD HH:MM:SS' in UTC
        tick_count: Number of ticks

    Returns:
        The written file
    """
    return write_json_fixture(source, name, build_minimal_tick_json(
        symbol='DOTUSD', broker_type='kraken_spot', tick_count=tick_count,
        start_time=start_time))


def _importer(source: Path, target: Path, override: bool = False) -> TickDataImporter:
    """
    An importer over the given inbox and archive, without bar rendering.

    Args:
        source: Inbox directory
        target: Archive directory
        override: Whether a re-import replaces the parquet of the same raw file

    Returns:
        The importer
    """
    return TickDataImporter(source_dir=str(source), target_dir=str(target),
                            override=override, auto_render_bars=False)


class TestTheArchiveIsCheckedBeforeTheWrite:
    """
    The archive holds no overlap today (6,427 files, measured 2026-10-13), so a file reaching
    into time it covers is a defect of that file — and a backtest over the overlap would read
    the same trades twice. It is refused before the write, where the batch check after the
    import could only warn.
    """

    def test_an_overlapping_file_is_refused_and_nothing_is_written(self, tmp_path):
        source, target = tmp_path / 'source', tmp_path / 'target'
        _importer(source, target).import_file(
            _write(source, 'DOTUSD_20260115_100000_ticks.json', '2026.01.15 10:00:00'))

        overlapping = _write(source, 'DOTUSD_20260115_100002_ticks.json', '2026.01.15 10:00:02')
        with pytest.raises(TickFileValidationException, match='Overlaps the archive'):
            _importer(source, target).convert_json_to_parquet(overlapping)
        assert len(find_tick_parquets(target)) == 1

    def test_a_refused_override_keeps_the_parquet_it_would_replace(self, tmp_path):
        # Checked before the override deletes anything — otherwise the refusal would cost the
        # archive the very file the re-import was meant to replace.
        source, target = tmp_path / 'source', tmp_path / 'target'
        first = _importer(source, target)
        first.import_file(_write(source, 'DOTUSD_20260115_100000_ticks.json', '2026.01.15 10:00:00'))
        first.import_file(_write(source, 'DOTUSD_20260115_110000_ticks.json', '2026.01.15 11:00:00'))
        before = sorted(path.name for path in find_tick_parquets(target))

        moved = _write(source, 'DOTUSD_20260115_100000_ticks.json', '2026.01.15 11:00:02')
        with pytest.raises(TickFileValidationException, match='Overlaps the archive'):
            _importer(source, target, override=True).convert_json_to_parquet(moved)
        assert sorted(path.name for path in find_tick_parquets(target)) == before

    def test_touching_edges_are_not_an_overlap(self, tmp_path):
        # The same rule as the archive ordering check: a file may open on the instant the
        # previous one closed.
        source, target = tmp_path / 'source', tmp_path / 'target'
        importer = _importer(source, target)
        importer.import_file(_write(source, 'DOTUSD_20260115_100000_ticks.json', '2026.01.15 10:00:00'))
        assert importer.import_file(
            _write(source, 'DOTUSD_20260115_100004_ticks.json', '2026.01.15 10:00:04'))
        assert len(find_tick_parquets(target)) == 2

    def test_a_batch_is_checked_against_itself(self, tmp_path):
        # Neither file is in the archive when the batch starts; the second must still see the
        # first, because the coverage grows with every file the import writes.
        source, target = tmp_path / 'source', tmp_path / 'target'
        _write(source, 'DOTUSD_20260115_100000_ticks.json', '2026.01.15 10:00:00')
        _write(source, 'DOTUSD_20260115_100002_ticks.json', '2026.01.15 10:00:02')
        importer = _importer(source, target)
        importer.process_all_exports()
        assert importer.processed_files == 1
        assert importer.errors == ['VALIDATION FAILED in DOTUSD_20260115_100002_ticks.json']
