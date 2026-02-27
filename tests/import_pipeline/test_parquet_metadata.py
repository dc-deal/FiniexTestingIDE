"""
Test Parquet Metadata.

Tests that Parquet files contain correct metadata — both core import
metadata and preserved original MQL5 source metadata.
"""

import json

import pyarrow.parquet as pq
import pytest

from python.data_management.importers.tick_importer import TickDataImporter
from tests.import_pipeline.conftest import (
    build_minimal_tick_json,
    find_tick_parquets,
    write_json_fixture,
)


def _import_and_get_metadata(tmp_path, offset_registry=None, **kwargs):
    """Helper: import a JSON file and return its Parquet metadata as dict.

    Args:
        tmp_path: Pytest tmp_path fixture
        offset_registry: Optional offset registry for TickDataImporter
        **kwargs: Passed to build_minimal_tick_json()

    Returns:
        Dict of decoded Parquet file metadata
    """
    source = tmp_path / "source"
    target = tmp_path / "target"
    data = build_minimal_tick_json(**kwargs)
    write_json_fixture(source, f"{kwargs.get('symbol', 'TEST')}_ticks.json", data)

    importer = TickDataImporter(
        source_dir=str(source),
        target_dir=str(target),
        auto_render_bars=False,
        offset_registry=offset_registry or {},
    )
    importer.process_all_exports()

    parquet_file = find_tick_parquets(target)[0]
    pq_file = pq.ParquetFile(parquet_file)
    raw_metadata = pq_file.schema_arrow.metadata

    return {
        (k.decode("utf-8") if isinstance(k, bytes) else k):
        (v.decode("utf-8") if isinstance(v, bytes) else v)
        for k, v in raw_metadata.items()
    }


class TestCoreMetadata:
    """Verify essential import metadata fields are present."""

    def test_source_file_present(self, tmp_path):
        """source_file should contain the original JSON filename."""
        meta = _import_and_get_metadata(tmp_path, symbol="BTCUSD", broker_type="kraken_spot")
        assert "source_file" in meta
        assert "BTCUSD_ticks.json" in meta["source_file"]

    def test_symbol_present(self, tmp_path):
        """symbol should match input."""
        meta = _import_and_get_metadata(tmp_path, symbol="ETHUSD", broker_type="kraken_spot")
        assert meta["symbol"] == "ETHUSD"

    def test_broker_type_present(self, tmp_path):
        """broker_type should be in metadata."""
        meta = _import_and_get_metadata(tmp_path, symbol="ADAUSD", broker_type="kraken_spot")
        assert meta["broker_type"] == "kraken_spot"

    def test_importer_version_present(self, tmp_path):
        """importer_version should match TickDataImporter.VERSION."""
        meta = _import_and_get_metadata(tmp_path, symbol="DOTUSD", broker_type="kraken_spot")
        assert meta["importer_version"] == TickDataImporter.VERSION

    def test_tick_count_correct(self, tmp_path):
        """tick_count should match number of ticks imported."""
        meta = _import_and_get_metadata(
            tmp_path, symbol="XRPUSD", broker_type="kraken_spot", tick_count=7)
        assert meta["tick_count"] == "7"

    def test_utc_conversion_flag_true_when_offset(self, tmp_path):
        """utc_conversion_applied should be 'true' when offset is applied."""
        meta = _import_and_get_metadata(
            tmp_path, symbol="EURUSD", broker_type="mt5",
            offset_registry={"mt5": -3})
        assert meta["utc_conversion_applied"] == "true"

    def test_utc_conversion_flag_false_when_no_offset(self, tmp_path):
        """utc_conversion_applied should be 'false' when no offset."""
        meta = _import_and_get_metadata(
            tmp_path, symbol="BTCUSD2", broker_type="kraken_spot",
            offset_registry={"kraken_spot": 0})
        assert meta["utc_conversion_applied"] == "false"

    def test_user_time_offset_hours_correct(self, tmp_path):
        """user_time_offset_hours should match applied offset."""
        meta = _import_and_get_metadata(
            tmp_path, symbol="GBPUSD", broker_type="mt5",
            offset_registry={"mt5": -3})
        assert meta["user_time_offset_hours"] == "-3"


class TestSourceMetadata:
    """Verify original MQL5 metadata is preserved with source_meta_ prefix."""

    def test_source_meta_flat_fields_present(self, tmp_path):
        """Flat metadata fields should appear with source_meta_ prefix."""
        meta = _import_and_get_metadata(
            tmp_path, symbol="LINKUSD", broker_type="kraken_spot")
        assert "source_meta_data_format_version" in meta
        assert "source_meta_collection_purpose" in meta
        assert "source_meta_operator" in meta

    def test_source_meta_nested_json_valid(self, tmp_path):
        """Nested metadata (symbol_info etc.) should be valid JSON strings."""
        meta = _import_and_get_metadata(
            tmp_path, symbol="SOLUSD", broker_type="kraken_spot")

        # symbol_info should be parseable JSON
        assert "source_meta_symbol_info" in meta
        symbol_info = json.loads(meta["source_meta_symbol_info"])
        assert isinstance(symbol_info, dict)

        # collection_settings should be parseable JSON
        assert "source_meta_collection_settings" in meta
        settings = json.loads(meta["source_meta_collection_settings"])
        assert isinstance(settings, dict)

        # error_tracking should be parseable JSON
        assert "source_meta_error_tracking" in meta
        tracking = json.loads(meta["source_meta_error_tracking"])
        assert isinstance(tracking, dict)

    def test_source_meta_symbol_info_content(self, tmp_path):
        """Parsed symbol_info should contain expected fields."""
        meta = _import_and_get_metadata(
            tmp_path, symbol="AVAXUSD", broker_type="kraken_spot")
        symbol_info = json.loads(meta["source_meta_symbol_info"])
        assert "point_value" in symbol_info
        assert "digits" in symbol_info
        assert "tick_size" in symbol_info

    def test_no_duplicate_symbol_and_broker_in_source_meta(self, tmp_path):
        """symbol and broker should NOT be duplicated in source_meta_ prefix."""
        meta = _import_and_get_metadata(
            tmp_path, symbol="MATICUSD", broker_type="kraken_spot")
        # These are already top-level keys, should not appear as source_meta_
        assert "source_meta_symbol" not in meta
        assert "source_meta_broker" not in meta
