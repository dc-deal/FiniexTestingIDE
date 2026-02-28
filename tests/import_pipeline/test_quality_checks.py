"""
Test Quality Checks.

Tests that the importer performs quality validation and
removes temporary columns from the output.
"""

import pandas as pd
import pytest

from python.data_management.importers.tick_importer import TickDataImporter
from tests.import_pipeline.conftest import (
    build_minimal_tick_json,
    find_tick_parquets,
    write_json_fixture,
)


class TestQualityChecks:
    """Verify quality check logic and cleanup."""

    def test_invalid_prices_do_not_crash_import(self, tmp_path):
        """Ticks with bid <= 0 should trigger warning but not crash import."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        data = build_minimal_tick_json(
            symbol="BADPRICE",
            broker_type="kraken_spot",
            tick_count=0,
            custom_ticks=[
                {"timestamp": "2026.01.15 10:00:00", "bid": -1.0, "ask": 1.1,
                 "last": 1.1, "tick_volume": 0, "real_volume": 100.0,
                 "chart_tick_volume": 1, "spread_points": 1, "spread_pct": 0.01,
                 "tick_flags": "BUY", "session": "24h",
                 "server_time": "2026.01.15 10:00:00"},
                {"timestamp": "2026.01.15 10:00:01", "bid": 1.1, "ask": 1.1001,
                 "last": 1.1, "tick_volume": 0, "real_volume": 100.0,
                 "chart_tick_volume": 1, "spread_points": 1, "spread_pct": 0.01,
                 "tick_flags": "BUY", "session": "24h",
                 "server_time": "2026.01.15 10:00:01"},
            ],
        )
        write_json_fixture(source, "BADPRICE_ticks.json", data)

        importer = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            auto_render_bars=False,
        )
        importer.process_all_exports()

        # Import should succeed (warnings logged, not errors)
        assert importer.processed_files == 1
        parquet_files = find_tick_parquets(target)
        assert len(parquet_files) == 1

    def test_extreme_spreads_do_not_crash_import(self, tmp_path):
        """Ticks with spread_pct > 5.0 should trigger warning but not crash."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        data = build_minimal_tick_json(
            symbol="BIGSPREAD",
            broker_type="kraken_spot",
            tick_count=0,
            custom_ticks=[
                {"timestamp": "2026.01.15 10:00:00", "bid": 1.0, "ask": 1.1,
                 "last": 1.0, "tick_volume": 0, "real_volume": 100.0,
                 "chart_tick_volume": 1, "spread_points": 1000, "spread_pct": 10.0,
                 "tick_flags": "BUY", "session": "24h",
                 "server_time": "2026.01.15 10:00:00"},
                {"timestamp": "2026.01.15 10:00:01", "bid": 1.1, "ask": 1.1001,
                 "last": 1.1, "tick_volume": 0, "real_volume": 100.0,
                 "chart_tick_volume": 1, "spread_points": 1, "spread_pct": 0.01,
                 "tick_flags": "BUY", "session": "24h",
                 "server_time": "2026.01.15 10:00:01"},
            ],
        )
        write_json_fixture(source, "BIGSPREAD_ticks.json", data)

        importer = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            auto_render_bars=False,
        )
        importer.process_all_exports()
        assert importer.processed_files == 1

    def test_temp_column_removed_from_output(self, tmp_path):
        """bid_pct_change temp column should not be in final Parquet."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        data = build_minimal_tick_json(
            symbol="CLEANOUT",
            broker_type="kraken_spot",
            tick_count=5,
        )
        write_json_fixture(source, "CLEANOUT_ticks.json", data)

        importer = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            auto_render_bars=False,
        )
        importer.process_all_exports()

        parquet_file = find_tick_parquets(target)[0]
        df = pd.read_parquet(parquet_file)
        assert "bid_pct_change" not in df.columns


class TestMoveProcessedFiles:
    """Verify the move-to-finished lifecycle."""

    def test_files_moved_when_enabled(self, tmp_path):
        """With move_processed_files=True, JSON should be moved to finished."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        finished = tmp_path / "finished"
        data = build_minimal_tick_json(
            symbol="MOVEME",
            broker_type="kraken_spot",
            tick_count=3,
        )
        write_json_fixture(source, "MOVEME_ticks.json", data)

        importer = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            move_processed_files=True,
            finished_dir=str(finished),
            auto_render_bars=False,
        )
        importer.process_all_exports()

        # Source should be empty (file moved)
        remaining = list(source.glob("*_ticks.json"))
        assert len(remaining) == 0

        # Finished should have the file
        moved = list(finished.glob("*_ticks.json"))
        assert len(moved) == 1
        assert moved[0].name == "MOVEME_ticks.json"

    def test_files_not_moved_when_disabled(self, tmp_path):
        """With move_processed_files=False, JSON should remain in source."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        data = build_minimal_tick_json(
            symbol="STAYPUT",
            broker_type="kraken_spot",
            tick_count=3,
        )
        write_json_fixture(source, "STAYPUT_ticks.json", data)

        importer = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            move_processed_files=False,
            auto_render_bars=False,
        )
        importer.process_all_exports()

        # Source should still have the file
        remaining = list(source.glob("*_ticks.json"))
        assert len(remaining) == 1
