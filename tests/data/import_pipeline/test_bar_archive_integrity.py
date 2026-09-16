"""
Test Bar Archive Integrity.

Two defects on the bar import path, both of the family "the pipeline noticed something
and said so where nobody looks" (#519).

An import directed at a scratch directory used to rebuild the PRODUCTION bar index and
the production discovery caches, because `BarsIndexManager` could not be scoped at all.
An import that looks isolated was only half isolated, and a test moved real state.

And a bar file whose column is corrupt reads cleanly everywhere except in that one
column: the footer still reports the right row count. Such a file survived a day and two
green suites, then removed itself from the index and surfaced as forty-one failures about
a missing timeframe.
"""

import hashlib
from pathlib import Path
from typing import Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.import_config_manager import ImportConfigManager
from python.data_management.importers.bar_importer import BarImporter, _verify_bar_file
from python.data_management.importers.tick_data_importer import TickDataImporter
from python.data_management.index.bars_index_manager import BarsIndexManager
from python.framework.discoveries.discovery_cache_index import DISCOVERY_INDEX_FILE
from python.framework.exceptions.data_quality_errors import BarFileVerificationException
from tests.data.import_pipeline.conftest import (
    build_minimal_tick_json,
    write_json_fixture,
)
from tests.shared.recording_logger import RecordingLogger


def _fingerprint(path: Path) -> Optional[str]:
    """Content hash of a file, or None when it does not exist.

    Args:
        path: File to fingerprint

    Returns:
        SHA-256 hex digest, or None if the file is absent
    """
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_bar_parquet(filepath: Path, rows: int) -> pd.DataFrame:
    """Write a minimal but structurally real bar file.

    Args:
        filepath: Destination path
        rows: Number of bars to write

    Returns:
        The DataFrame that was written
    """
    df = pd.DataFrame({
        'timestamp': pd.date_range('2026-01-15', periods=rows, freq='1min', tz='UTC'),
        'open': [1.1] * rows,
        'high': [1.2] * rows,
        'low': [1.0] * rows,
        'close': [1.15] * rows,
        'volume': [10.0] * rows,
        'tick_count': [5] * rows,
    })
    filepath.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(df), filepath, compression='snappy')
    return df


class TestScopedImportLeavesProductionAlone:
    """
    An import pointed at a temporary directory must not touch the real archive.

    `BarImporter` honoured its `data_dir` for RENDERING and then rebuilt the production
    index and 48 production discovery caches, because neither manager could be scoped.
    """

    def test_production_indexes_untouched_by_scoped_import(self, tmp_path):
        """A full import into tmp_path leaves both production index files byte-identical."""
        production = Path(AppConfigManager().get_data_processed_path())
        bars_index = production / BarsIndexManager.INDEX_FILE_PARQUET
        cache_index = production / 'discovery_caches' / DISCOVERY_INDEX_FILE

        before_bars = _fingerprint(bars_index)
        before_cache = _fingerprint(cache_index)

        # Without a production archive both fingerprints are None and the assertions
        # below hold trivially — a green test that verified nothing (#509). Say so.
        if before_bars is None and before_cache is None:
            pytest.skip(
                'No production archive on this machine — nothing to protect, '
                'so this test cannot prove the import was scoped')

        source = tmp_path / 'source'
        target = tmp_path / 'target'
        target.mkdir(parents=True, exist_ok=True)

        data = build_minimal_tick_json(symbol='BTCUSD', broker_type='kraken_spot')
        write_json_fixture(source, 'BTCUSD_ticks.json', data)

        importer = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            auto_render_bars=True,
            offset_registry={'kraken_spot': 0},
        )
        importer.process_all_exports()

        assert _fingerprint(bars_index) == before_bars, (
            'A scoped import rewrote the production bar index')
        assert _fingerprint(cache_index) == before_cache, (
            'A scoped import rewrote the production discovery cache index')

    def test_scoped_import_writes_its_own_bar_index(self, tmp_path):
        """The scoped index is written where the import was pointed, not where config says."""
        source = tmp_path / 'source'
        target = tmp_path / 'target'
        target.mkdir(parents=True, exist_ok=True)

        data = build_minimal_tick_json(symbol='ETHUSD', broker_type='kraken_spot')
        write_json_fixture(source, 'ETHUSD_ticks.json', data)

        importer = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            auto_render_bars=True,
            offset_registry={'kraken_spot': 0},
        )
        importer.process_all_exports()

        scoped_index = target / BarsIndexManager.INDEX_FILE_PARQUET
        assert scoped_index.exists()

        manager = BarsIndexManager(data_dir=str(target))
        manager.build_index()
        assert 'ETHUSD' in manager.list_symbols('kraken_spot')

    def test_production_import_still_rebuilds_discovery_caches(self):
        """
        The skip must not fire on a real import.

        The importer's target comes from `import_config.paths.import_output` while the
        discovery caches resolve `app_config`'s processed path — two different keys that
        happen to hold the same value. If they ever drift, a production import would stop
        rebuilding the caches and nothing would say so.
        """
        production_target = ImportConfigManager().get_import_output_path()

        assert BarImporter(data_dir=production_target)._writes_production_archive()
        assert BarImporter()._writes_production_archive()


class TestWriteTimeVerification:
    """
    A bar file is read back in full right after writing.

    Only a FULL read finds a corrupt column — projecting `timestamp` alone reported all
    128 files healthy while one carried an unreadable `low`.
    """

    def test_healthy_file_passes(self, tmp_path):
        """A file that was written correctly verifies without raising."""
        filepath = tmp_path / 'BTCUSD_M1_BARS.parquet'
        df = _write_bar_parquet(filepath, rows=20)

        _verify_bar_file(filepath, len(df))

    def test_truncated_file_is_caught(self, tmp_path):
        """A file cut short after writing cannot be read back and must be refused."""
        filepath = tmp_path / 'BTCUSD_M5_BARS.parquet'
        _write_bar_parquet(filepath, rows=50)

        # Drop the footer: the file keeps its data pages and loses the metadata that
        # describes them, which is what a partially written file looks like.
        raw = filepath.read_bytes()
        filepath.write_bytes(raw[:len(raw) // 2])

        with pytest.raises(BarFileVerificationException, match='unreadable'):
            _verify_bar_file(filepath, 50)

    def test_row_count_mismatch_is_caught(self, tmp_path):
        """A readable file carrying fewer rows than were written is refused too."""
        filepath = tmp_path / 'BTCUSD_M15_BARS.parquet'
        _write_bar_parquet(filepath, rows=7)

        with pytest.raises(BarFileVerificationException, match='row count mismatch'):
            _verify_bar_file(filepath, 8)


class TestIndexBuildReportsUnreadableFiles:
    """
    The balance at the end of an index build.

    A per-file warning scrolls away inside a rebuild log, and the CONSEQUENCE — an index
    row that is simply absent — announces nothing at all.
    """

    def test_corrupt_file_is_reported_on_error_level(self, tmp_path):
        """A file the scan cannot read is named in an error-level block."""
        bars_dir = tmp_path / 'kraken_spot' / 'bars' / 'BTCUSD'
        good = bars_dir / 'BTCUSD_M1_BARS.parquet'
        bad = bars_dir / 'BTCUSD_M5_BARS.parquet'

        _write_bar_parquet(good, rows=10)
        _write_bar_parquet(bad, rows=10)
        bad.write_bytes(b'not a parquet file at all')

        logger = RecordingLogger()
        manager = BarsIndexManager(logger=logger, data_dir=str(tmp_path))
        manager.build_index(force_rebuild=True)

        errors = logger.text_at('error')
        assert '1 of 2 bar files could not be read' in errors
        assert 'BTCUSD_M5_BARS.parquet' in errors
        assert 'bar_index_cli.py render' in errors

    def test_clean_build_reports_no_error_block(self, tmp_path):
        """A build where every file reads must not produce the error block."""
        bars_dir = tmp_path / 'kraken_spot' / 'bars' / 'BTCUSD'
        _write_bar_parquet(bars_dir / 'BTCUSD_M1_BARS.parquet', rows=10)

        logger = RecordingLogger()
        manager = BarsIndexManager(logger=logger, data_dir=str(tmp_path))
        manager.build_index(force_rebuild=True)

        assert 'error' not in logger.levels()
        assert 'from 1 bar files' in logger.text()


class TestThePriceBasisSurvivesIntoTheIndex:
    """
    The stamp needs a READ path, or it is decoration.

    A bar file records which basis it was rendered from. That only answers the question it
    exists for — "is this archive consistently rendered?" — if it reaches the index: the
    scan extracts a FIXED key set and would drop an unknown one, and the API reads bar rows
    with `pd.read_parquet`, which discards Arrow schema metadata entirely. So one file must
    answer for all of them, rather than 128 files answering one at a time.
    """

    def _write_stamped(self, filepath: Path, basis, rows: int = 5) -> None:
        """Write a bar file, with or without a price-basis stamp.

        Args:
            filepath: Destination
            basis: The stamped basis, or None to model a file written before it existed
            rows: Number of bars
        """
        df = pd.DataFrame({
            'timestamp': pd.date_range('2026-01-15', periods=rows, freq='1min', tz='UTC'),
            'open': [1.1] * rows, 'high': [1.2] * rows,
            'low': [1.0] * rows, 'close': [1.15] * rows,
            'volume': [10.0] * rows, 'tick_count': [5] * rows,
        })
        filepath.parent.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pandas(df)
        metadata = {
            'symbol': filepath.name.split('_')[0],
            'timeframe': 'M1',
            'broker_type': 'kraken_spot',
        }
        if basis is not None:
            metadata['price_basis'] = basis
        table = table.replace_schema_metadata(metadata)
        pq.write_table(table, filepath, compression='snappy')

    def test_the_stamp_reaches_the_index_and_survives_a_reload(self, tmp_path):
        """Scan, persist and load back — the whole round trip, not just the scan."""
        self._write_stamped(
            tmp_path / 'kraken_spot' / 'bars' / 'BTCUSD' / 'BTCUSD_M1_BARS.parquet',
            'order_driven')

        manager = BarsIndexManager(data_dir=str(tmp_path))
        manager.build_index(force_rebuild=True)
        assert manager.index['kraken_spot']['BTCUSD']['M1']['price_basis'] == 'order_driven'

        reloaded = BarsIndexManager(data_dir=str(tmp_path))
        reloaded.build_index()
        assert reloaded.index['kraken_spot']['BTCUSD']['M1']['price_basis'] == 'order_driven'

    def test_the_persisted_index_carries_it_as_a_column(self, tmp_path):
        """One file answers for the whole archive — that is the point of the column."""
        self._write_stamped(
            tmp_path / 'kraken_spot' / 'bars' / 'ETHUSD' / 'ETHUSD_M1_BARS.parquet',
            'order_driven')

        manager = BarsIndexManager(data_dir=str(tmp_path))
        manager.build_index(force_rebuild=True)

        df = pd.read_parquet(tmp_path / BarsIndexManager.INDEX_FILE_PARQUET)
        assert 'price_basis' in df.columns
        assert df['price_basis'].iloc[0] == 'order_driven'

    def test_a_file_written_before_the_stamp_reads_as_unknown(self, tmp_path):
        """
        Honest, and distinguishable from a declared basis.

        Borrowing today's configuration for an old file is exactly what the stamp exists to
        prevent — during a re-render, config describes what a render WOULD produce while
        half the archive still holds the previous answer.
        """
        # Everything a bar file always had, and no stamp — a file from before the change.
        self._write_stamped(
            tmp_path / 'kraken_spot' / 'bars' / 'SOLUSD' / 'SOLUSD_M1_BARS.parquet', None)

        manager = BarsIndexManager(data_dir=str(tmp_path))
        manager.build_index(force_rebuild=True)

        assert manager.index['kraken_spot']['SOLUSD']['M1']['price_basis'] == 'unknown'
