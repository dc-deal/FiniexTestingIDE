"""
Test Tick Index Persistence.

The tick index is the only path by which data_format_version reaches a run report
(parquet metadata → index → SharedDataPreparator → PostRunValidator). The field used to be
dropped when the index was written to disk, so every run warned about "pre-V1.3.0 data"
regardless of the data's actual age. These tests pin the round-trip through the persisted
index, plus the tolerant load of an index file written before the field existed.
"""

from pathlib import Path

import pandas as pd

from python.data_management.importers.tick_importer import TickDataImporter
from python.data_management.index.tick_index_manager import TickIndexManager
from tests.data.import_pipeline.conftest import (
    build_minimal_tick_json,
    write_json_fixture,
)


def _import_ticks(tmp_path, symbol="BTCUSD", data_format_version="1.3.0") -> Path:
    """Helper: import one synthetic JSON and return the target dir (index included).

    Args:
        tmp_path: Pytest tmp_path fixture
        symbol: Symbol name
        data_format_version: Version written into the JSON metadata

    Returns:
        Path of the import target directory
    """
    source = tmp_path / "source"
    target = tmp_path / "target"
    data = build_minimal_tick_json(
        symbol=symbol,
        broker_type="kraken_spot",
        data_format_version=data_format_version,
    )
    write_json_fixture(source, f"{symbol}_ticks.json", data)

    importer = TickDataImporter(
        source_dir=str(source),
        target_dir=str(target),
        auto_render_bars=False,
        offset_registry={"kraken_spot": 0},
    )
    importer.process_all_exports()

    return target


class TestVersionRoundTrip:
    """Verify data_format_version survives the write/read cycle of the index file."""

    def test_version_in_persisted_index_parquet(self, tmp_path):
        """The index file on disk should carry the version column."""
        target = _import_ticks(tmp_path, data_format_version="1.3.0")

        df = pd.read_parquet(target / TickIndexManager.INDEX_FILE_PARQUET)
        assert "data_format_version" in df.columns
        assert df["data_format_version"].iloc[0] == "1.3.0"

    def test_version_survives_reload(self, tmp_path):
        """A manager loading the persisted index should see the version, not 'unknown'."""
        target = _import_ticks(tmp_path, symbol="ETHUSD", data_format_version="1.3.0")

        manager = TickIndexManager(data_dir=str(target))
        manager.build_index()

        entry = manager.index["kraken_spot"]["ETHUSD"][0]
        assert entry["data_format_version"] == "1.3.0"

    def test_empty_index_carries_version_column(self, tmp_path):
        """The empty-schema branch should declare the same columns as a populated index."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        manager = TickIndexManager(data_dir=str(empty_dir))
        manager.save_index()

        df = pd.read_parquet(empty_dir / TickIndexManager.INDEX_FILE_PARQUET)
        assert "data_format_version" in df.columns


class TestLegacyIndexTolerance:
    """An index file written before the version field existed must still load."""

    def test_index_without_version_column_loads_as_unknown(self, tmp_path):
        """Missing column reads as 'unknown' — never an exception that empties the index."""
        target = _import_ticks(tmp_path, symbol="SOLUSD", data_format_version="1.3.0")
        index_file = target / TickIndexManager.INDEX_FILE_PARQUET

        # Simulate a pre-fix index: same rows, no version column
        df = pd.read_parquet(index_file)
        df.drop(columns=["data_format_version"]).to_parquet(index_file)

        manager = TickIndexManager(data_dir=str(target))
        manager.build_index()

        entries = manager.index["kraken_spot"]["SOLUSD"]
        assert len(entries) == 1
        assert entries[0]["data_format_version"] == "unknown"
