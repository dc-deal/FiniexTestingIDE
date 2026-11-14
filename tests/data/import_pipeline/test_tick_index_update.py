"""
Test the tick index update.

An import used to rebuild the tick index by reading every tick file in full. An update reads only
what changed — a file the index does not hold, or one whose size or modification time moved — and
keeps the rest. These tests pin what it reads, what it keeps and what it drops, that it falls back
to a full rebuild where it must, that its result equals a full rebuild's, and that an import shows
which of the two ran and estimates the step by it.
"""

import os
from pathlib import Path
from typing import Dict, List

import pandas as pd
import pyarrow.parquet as pq

from python.data_management.importers.import_coordinator import (
    _INDEX_REBUILD_REASON_TEXTS,
    ImportCoordinator,
)
from python.data_management.importers.tick_data_importer import TickDataImporter
from python.data_management.index.tick_index_manager import TickIndexManager
from python.framework.types.import_types.import_progress_types import (
    ImportPaths,
    ImportRateUnit,
    ImportStepId,
)
from python.framework.types.import_types.tick_index_types import (
    TickIndexMethod,
    TickIndexRebuildReason,
)
from tests.data.import_pipeline.conftest import build_minimal_tick_json, write_json_fixture


def _import(tmp_path: Path, batch: str, *symbols: str) -> Path:
    """Import one export per symbol from a source folder of its own; returns the archive."""
    source = tmp_path / batch
    target = tmp_path / 'target'
    for symbol in symbols:
        write_json_fixture(source, f'{symbol}_ticks.json',
                           build_minimal_tick_json(symbol=symbol, broker_type='kraken_spot'))
    TickDataImporter(source_dir=str(source), target_dir=str(target),
                     auto_render_bars=False).process_all_exports()
    return target


def _tick_files(target: Path) -> List[Path]:
    """The archive's tick files."""
    return sorted(target.glob('*/ticks/**/*.parquet'))


def _update(target: Path) -> TickIndexManager:
    """One update of the archive's index, by a manager of its own."""
    manager = TickIndexManager(data_dir=str(target))
    manager.update_index()
    return manager


def _rows(target: Path) -> List[Dict]:
    """The index as written, one row per file, in path order."""
    rows = pq.read_table(target / TickIndexManager.INDEX_FILE_PARQUET).to_pylist()
    return sorted(rows, key=lambda row: row['path'])


class TestAnUpdateReadsOnlyWhatChanged:

    def test_a_second_import_reads_only_its_own_file(self, tmp_path, monkeypatch):
        target = _import(tmp_path, 'first', 'AAAUSD', 'BBBUSD')
        read: List[str] = []
        scan_file = TickIndexManager._scan_file

        def counting_scan(self, parquet_file):
            read.append(parquet_file.name)
            return scan_file(self, parquet_file)

        monkeypatch.setattr(TickIndexManager, '_scan_file', counting_scan)
        _import(tmp_path, 'second', 'CCCUSD')

        assert len(read) == 1 and read[0].startswith('CCCUSD')
        assert [row['symbol'] for row in _rows(target)] == ['AAAUSD', 'BBBUSD', 'CCCUSD']

    def test_nothing_changed_reads_nothing(self, tmp_path):
        target = _import(tmp_path, 'first', 'AAAUSD', 'BBBUSD')

        run = _update(target).get_last_run()

        assert run.method is TickIndexMethod.UPDATE
        assert run.rebuild_reason is None
        assert (run.scanned, run.unchanged, run.gone) == (0, 2, 0)

    def test_a_rewritten_file_is_read_again(self, tmp_path):
        target = _import(tmp_path, 'first', 'AAAUSD', 'BBBUSD')
        rewritten = _tick_files(target)[0]
        stat = os.stat(rewritten)
        os.utime(rewritten, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

        run = _update(target).get_last_run()

        assert (run.scanned, run.unchanged, run.gone) == (1, 1, 0)

    def test_a_removed_file_leaves_the_index(self, tmp_path):
        target = _import(tmp_path, 'first', 'AAAUSD', 'BBBUSD')
        removed = _tick_files(target)[0]
        removed.unlink()

        run = _update(target).get_last_run()

        assert (run.scanned, run.unchanged, run.gone) == (0, 1, 1)
        assert [row['file'] for row in _rows(target)] == [_tick_files(target)[0].name]


class TestAnUpdateFallsBackToAFullRebuild:

    def test_no_index_means_a_full_rebuild(self, tmp_path):
        target = _import(tmp_path, 'first', 'AAAUSD', 'BBBUSD')
        (target / TickIndexManager.INDEX_FILE_PARQUET).unlink()
        manager = TickIndexManager(data_dir=str(target))

        assert manager.get_full_rebuild_reason() is TickIndexRebuildReason.NO_INDEX
        manager.update_index()
        run = manager.get_last_run()

        assert run.method is TickIndexMethod.FULL_REBUILD
        assert run.rebuild_reason is TickIndexRebuildReason.NO_INDEX
        assert run.scanned == 2
        # A full rebuild compares nothing, so these two were not measured
        assert run.unchanged is None and run.gone is None

    def test_an_index_under_an_older_schema_is_rebuilt_in_full(self, tmp_path):
        target = _import(tmp_path, 'first', 'AAAUSD')
        index_file = target / TickIndexManager.INDEX_FILE_PARQUET
        # Rewriting through pandas drops the version stamp, as an older index lacks it
        pd.read_parquet(index_file).to_parquet(index_file)

        manager = TickIndexManager(data_dir=str(target))
        assert manager.get_full_rebuild_reason() is TickIndexRebuildReason.OLDER_SCHEMA
        manager.update_index()

        assert manager.get_last_run().method is TickIndexMethod.FULL_REBUILD
        assert manager.get_last_run().rebuild_reason is TickIndexRebuildReason.OLDER_SCHEMA

    def test_an_index_without_the_file_identities_is_rebuilt_once_and_then_updated(self, tmp_path):
        # The identities came without a schema change: an index written before them is still
        # current for every reader, but an update could recognise none of its files. It is
        # rebuilt in full once, so that it is shown and estimated as what it is
        target = _import(tmp_path, 'first', 'AAAUSD', 'BBBUSD')
        index_file = target / TickIndexManager.INDEX_FILE_PARQUET
        table = pq.read_table(index_file).drop_columns(['file_size_bytes', 'file_mtime_ns'])
        pq.write_table(table, index_file)
        manager = TickIndexManager(data_dir=str(target))
        assert manager.get_full_rebuild_reason() is TickIndexRebuildReason.NO_FILE_IDENTITIES

        manager.update_index()
        first = manager.get_last_run()
        second = _update(target).get_last_run()

        assert (first.method, first.rebuild_reason, first.scanned) == (
            TickIndexMethod.FULL_REBUILD, TickIndexRebuildReason.NO_FILE_IDENTITIES, 2)
        assert (second.method, second.scanned, second.unchanged) == (TickIndexMethod.UPDATE, 0, 2)


class TestAnUpdateEqualsAFullRebuild:

    def test_the_updated_index_matches_a_full_rebuild(self, tmp_path):
        target = _import(tmp_path, 'first', 'AAAUSD', 'BBBUSD', 'CCCUSD')
        _import(tmp_path, 'second', 'DDDUSD')
        files = _tick_files(target)
        stat = os.stat(files[0])
        os.utime(files[0], ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        files[1].unlink()

        _update(target)
        updated = _rows(target)
        TickIndexManager(data_dir=str(target)).build_index(force_rebuild=True)

        assert updated == _rows(target)

    def test_the_file_identities_are_kept_as_exact_integers(self, tmp_path):
        # A nanosecond modification time does not survive a float; a rounded one would make
        # every update read the file again
        target = _import(tmp_path, 'first', 'AAAUSD', 'BBBUSD')

        for row in _rows(target):
            stat = os.stat(row['path'])
            assert row['file_size_bytes'] == stat.st_size
            assert row['file_mtime_ns'] == stat.st_mtime_ns


def _paths(root: Path) -> ImportPaths:
    """An import tree under a temporary root."""
    return ImportPaths(
        tick_source=root / 'raw',
        tick_target=root / 'processed',
        tick_finished=root / 'finished',
        tick_unpacking=root / 'unpacking',
        tick_refused=root / 'refused',
        signal_source=root / 'raw' / 'signals',
        signal_target=root / 'processed' / 'signals',
        signal_finished=root / 'finished' / 'signals',
        log_dir=root / 'logs' / 'imports',
        timings_file=root / 'runtime' / 'import_timings.json',
    )


def _run_import(paths: ImportPaths, symbol: str) -> ImportCoordinator:
    """One import of one loose export, without the screen."""
    write_json_fixture(paths.tick_source, f'{symbol}_20260115_100000_ticks.json',
                       build_minimal_tick_json(symbol=symbol))
    coordinator = ImportCoordinator(paths=paths, override=False, show_display=False)
    coordinator.run()
    return coordinator


class TestTheImportShowsWhichMethodRan:

    def test_the_first_import_rebuilds_in_full_and_says_why(self, tmp_path):
        paths = _paths(tmp_path)

        coordinator = _run_import(paths, 'AAAUSD')

        log = coordinator.get_log_path().read_text(encoding='utf-8')
        assert '📚 Tick index: full rebuild (no index yet) — 1 scanned' in log

    def test_the_next_import_updates_and_is_estimated_by_its_own_files(self, tmp_path):
        paths = _paths(tmp_path)
        _run_import(paths, 'AAAUSD')

        coordinator = _run_import(paths, 'BBBUSD')

        log = coordinator.get_log_path().read_text(encoding='utf-8')
        assert '📚 Tick index: update — 1 scanned · 1 unchanged · 0 gone' in log
        rate = coordinator._tracker.measured_rates()[ImportStepId.TICKS_INDEX]
        assert rate.unit is ImportRateUnit.IMPORTED_FILE

    def test_every_rebuild_reason_has_its_words(self):
        assert set(_INDEX_REBUILD_REASON_TEXTS) == set(TickIndexRebuildReason)
