"""
Test Duplicate Detection.

Tests that the importer correctly detects and handles duplicate imports.
"""

import pytest

from python.data_management.index.data_loader_exceptions import (
    ArtificialDuplicateException,
)
from python.data_management.importers.tick_importer import TickDataImporter
from tests.import_pipeline.conftest import (
    build_minimal_tick_json,
    find_tick_parquets,
    write_json_fixture,
)


class TestDuplicateDetection:
    """Verify duplicate import prevention and override behavior."""

    def test_first_import_no_duplicate(self, tmp_path):
        """First import of a file should succeed without duplicate error."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        data = build_minimal_tick_json(
            symbol="BTCUSD",
            broker_type="kraken_spot",
            tick_count=5,
        )
        write_json_fixture(source, "BTCUSD_20260115_ticks.json", data)

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
        source = tmp_path / "source"
        target = tmp_path / "target"
        data = build_minimal_tick_json(
            symbol="ETHUSD",
            broker_type="kraken_spot",
            tick_count=5,
        )

        # First import
        write_json_fixture(source, "ETHUSD_20260115_ticks.json", data)
        importer1 = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            auto_render_bars=False,
        )
        importer1.process_all_exports()
        assert importer1.processed_files == 1

        # Second import — same source file name
        write_json_fixture(source, "ETHUSD_20260115_ticks.json", data)
        importer2 = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            auto_render_bars=False,
        )
        importer2.process_all_exports()

        # Should have duplicate error
        assert len(importer2.errors) > 0
        assert "DUPLICATE" in importer2.errors[0]

    def test_override_allows_reimport(self, tmp_path):
        """With override=True, duplicate file should be replaced."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        data = build_minimal_tick_json(
            symbol="ADAUSD",
            broker_type="kraken_spot",
            tick_count=5,
        )

        # First import
        write_json_fixture(source, "ADAUSD_20260115_ticks.json", data)
        importer1 = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            auto_render_bars=False,
        )
        importer1.process_all_exports()

        # Second import with override
        write_json_fixture(source, "ADAUSD_20260115_ticks.json", data)
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
        source = tmp_path / "source"
        target = tmp_path / "target"

        # First file
        data1 = build_minimal_tick_json(
            symbol="XRPUSD",
            broker_type="kraken_spot",
            tick_count=3,
            start_time="2026.01.15 10:00:00",
        )
        write_json_fixture(source, "XRPUSD_20260115_ticks.json", data1)
        importer1 = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            auto_render_bars=False,
        )
        importer1.process_all_exports()

        # Different file (different name = different source)
        data2 = build_minimal_tick_json(
            symbol="XRPUSD",
            broker_type="kraken_spot",
            tick_count=3,
            start_time="2026.01.16 10:00:00",
        )
        # Remove old file from source
        for f in source.glob("*"):
            f.unlink()
        write_json_fixture(source, "XRPUSD_20260116_ticks.json", data2)
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
