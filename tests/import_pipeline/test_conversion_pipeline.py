"""
Test Conversion Pipeline.

Tests the full JSON → Parquet conversion pipeline for correctness.
"""

import pandas as pd
import pyarrow.parquet as pq
import pytest

from python.data_management.importers.tick_importer import TickDataImporter
from tests.import_pipeline.conftest import (
    build_minimal_tick_json,
    find_tick_parquets,
    write_json_fixture,
)


class TestBasicConversion:
    """Verify the core JSON → Parquet conversion produces correct output."""

    def test_basic_conversion_creates_parquet(self, tmp_path):
        """Valid JSON should produce a .parquet file."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        data = build_minimal_tick_json(
            symbol="ADAUSD",
            broker_type="kraken_spot",
            tick_count=10,
        )
        write_json_fixture(source, "ADAUSD_ticks.json", data)

        importer = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            auto_render_bars=False,
        )
        importer.process_all_exports()

        parquet_files = find_tick_parquets(target)
        assert len(parquet_files) == 1
        assert importer.processed_files == 1

    def test_parquet_has_expected_columns(self, tmp_path):
        """Output Parquet should contain essential tick columns."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        data = build_minimal_tick_json(
            symbol="ETHUSD",
            broker_type="kraken_spot",
            tick_count=5,
        )
        write_json_fixture(source, "ETHUSD_ticks.json", data)

        importer = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            auto_render_bars=False,
        )
        importer.process_all_exports()

        parquet_file = find_tick_parquets(target)[0]
        df = pd.read_parquet(parquet_file)

        expected_cols = ["timestamp", "bid", "ask", "last", "real_volume"]
        for col in expected_cols:
            assert col in df.columns, f"Missing column: {col}"

    def test_tick_count_matches_input(self, tmp_path):
        """Number of rows in Parquet should match input tick count."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        tick_count = 17
        data = build_minimal_tick_json(
            symbol="XRPUSD",
            broker_type="kraken_spot",
            tick_count=tick_count,
        )
        write_json_fixture(source, "XRPUSD_ticks.json", data)

        importer = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            auto_render_bars=False,
        )
        importer.process_all_exports()

        parquet_file = find_tick_parquets(target)[0]
        df = pd.read_parquet(parquet_file)
        assert len(df) == tick_count

    def test_directory_structure_correct(self, tmp_path):
        """Output path should follow broker_type/ticks/SYMBOL/ structure."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        data = build_minimal_tick_json(
            symbol="SOLUSD",
            broker_type="kraken_spot",
            tick_count=3,
        )
        write_json_fixture(source, "SOLUSD_ticks.json", data)

        importer = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            auto_render_bars=False,
        )
        importer.process_all_exports()

        parquet_file = find_tick_parquets(target)[0]
        relative = parquet_file.relative_to(target)
        parts = relative.parts
        # Should be: kraken_spot/ticks/SOLUSD/SOLUSD_*.parquet
        assert parts[0] == "kraken_spot"
        assert parts[1] == "ticks"
        assert parts[2] == "SOLUSD"
        assert parts[3].startswith("SOLUSD_")
        assert parts[3].endswith(".parquet")

    def test_datatype_optimization_applied(self, tmp_path):
        """bid/ask/last should be float32, tick_volume should be int32."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        data = build_minimal_tick_json(
            symbol="DOTUSD",
            broker_type="kraken_spot",
            tick_count=5,
        )
        write_json_fixture(source, "DOTUSD_ticks.json", data)

        importer = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            auto_render_bars=False,
        )
        importer.process_all_exports()

        parquet_file = find_tick_parquets(target)[0]
        df = pd.read_parquet(parquet_file)
        assert df["bid"].dtype.name == "float32"
        assert df["ask"].dtype.name == "float32"
        assert df["tick_volume"].dtype.name == "int32"

    def test_timestamps_parsed_as_datetime(self, tmp_path):
        """Timestamp column should be datetime64 dtype."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        data = build_minimal_tick_json(
            symbol="LINKUSD",
            broker_type="kraken_spot",
            tick_count=3,
        )
        write_json_fixture(source, "LINKUSD_ticks.json", data)

        importer = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            auto_render_bars=False,
        )
        importer.process_all_exports()

        parquet_file = find_tick_parquets(target)[0]
        df = pd.read_parquet(parquet_file)
        assert "datetime64" in str(df["timestamp"].dtype)
