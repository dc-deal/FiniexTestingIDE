"""
Test JSON Schema Validation.

Tests that the importer correctly handles valid and invalid JSON structures.
"""

import json

import pytest

from python.data_management.importers.tick_importer import TickDataImporter
from python.framework.types.import_schema_types import (
    ImportJsonSchema,
    ImportMetadataSchema,
    ImportTickSchema,
    MANDATORY_METADATA_FIELDS,
    MANDATORY_TICK_FIELDS,
    BROKER_IDENTIFICATION_FIELDS,
)
from tests.import_pipeline.conftest import (
    build_minimal_tick_json,
    find_tick_parquets,
    write_json_fixture,
)


class TestValidJsonAccepted:
    """Verify that well-formed JSON passes import without error."""

    def test_minimal_valid_json_imports_successfully(self, tmp_path):
        """A minimal valid JSON file should produce a Parquet file."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        data = build_minimal_tick_json(
            symbol="BTCUSD",
            broker_type="kraken_spot",
            tick_count=3,
        )
        write_json_fixture(source, "BTCUSD_ticks.json", data)

        importer = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            auto_render_bars=False,
        )
        importer.process_all_exports()

        # Verify parquet was created
        parquet_files = find_tick_parquets(target)
        assert len(parquet_files) == 1

    def test_legacy_data_collector_field_accepted(self, tmp_path):
        """JSON with data_collector instead of broker_type should be accepted."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        data = build_minimal_tick_json(broker_type="kraken_spot")
        # Remove broker_type, keep only legacy data_collector
        del data["metadata"]["broker_type"]
        data["metadata"]["data_collector"] = "kraken_spot"
        write_json_fixture(source, "LEGACY_ticks.json", data)

        importer = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            auto_render_bars=False,
        )
        importer.process_all_exports()

        parquet_files = find_tick_parquets(target)
        assert len(parquet_files) == 1


class TestInvalidJsonRejected:
    """Verify that malformed JSON is handled cleanly."""

    def test_missing_metadata_key_raises(self, tmp_path):
        """JSON without 'metadata' key should raise ValueError."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        data = {"ticks": [{"timestamp": "2026.01.15 10:00:00", "bid": 1.1, "ask": 1.2}]}
        write_json_fixture(source, "BAD_ticks.json", data)

        importer = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            auto_render_bars=False,
        )
        # Should not crash — errors are collected
        importer.process_all_exports()
        assert len(importer.errors) > 0

    def test_missing_ticks_key_raises(self, tmp_path):
        """JSON without 'ticks' key should raise ValueError."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        data = {"metadata": {"symbol": "TEST", "broker_type": "kraken_spot", "start_time": "2026.01.15 10:00:00"}}
        write_json_fixture(source, "NOTICKS_ticks.json", data)

        importer = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            auto_render_bars=False,
        )
        importer.process_all_exports()
        assert len(importer.errors) > 0

    def test_empty_ticks_array_skips_without_crash(self, tmp_path):
        """Empty ticks array should skip without error."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        data = build_minimal_tick_json(tick_count=0, custom_ticks=[])
        write_json_fixture(source, "EMPTY_ticks.json", data)

        importer = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            auto_render_bars=False,
        )
        importer.process_all_exports()

        # No errors, but also no parquet (empty ticks = skip)
        assert len(importer.errors) == 0
        parquet_files = find_tick_parquets(target)
        assert len(parquet_files) == 0

    def test_missing_broker_type_and_data_collector_raises(self, tmp_path):
        """JSON with neither broker_type nor data_collector should error."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        data = build_minimal_tick_json(broker_type="kraken_spot")
        del data["metadata"]["broker_type"]
        del data["metadata"]["data_collector"]
        write_json_fixture(source, "NOBROKER_ticks.json", data)

        importer = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            auto_render_bars=False,
        )
        importer.process_all_exports()
        assert len(importer.errors) > 0

    def test_unknown_broker_type_raises(self, tmp_path):
        """broker_type not in market_config should error."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        data = build_minimal_tick_json()
        data["metadata"]["broker_type"] = "nonexistent_broker"
        data["metadata"]["data_collector"] = "nonexistent_broker"
        write_json_fixture(source, "UNKNOWNBROKER_ticks.json", data)

        importer = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            auto_render_bars=False,
        )
        importer.process_all_exports()
        assert len(importer.errors) > 0


class TestSchemaTypeDefinitions:
    """Verify that schema TypedDicts are importable and have expected fields."""

    def test_schema_types_importable(self):
        """All schema types should be importable."""
        assert ImportJsonSchema is not None
        assert ImportMetadataSchema is not None
        assert ImportTickSchema is not None

    def test_mandatory_fields_defined(self):
        """Mandatory field lists should be non-empty."""
        assert len(MANDATORY_METADATA_FIELDS) > 0
        assert len(MANDATORY_TICK_FIELDS) > 0
        assert len(BROKER_IDENTIFICATION_FIELDS) > 0

    def test_mandatory_tick_fields_contain_essentials(self):
        """Tick mandatory fields must include timestamp, bid, ask."""
        assert "timestamp" in MANDATORY_TICK_FIELDS
        assert "bid" in MANDATORY_TICK_FIELDS
        assert "ask" in MANDATORY_TICK_FIELDS
