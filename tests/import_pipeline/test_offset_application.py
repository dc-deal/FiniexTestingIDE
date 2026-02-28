"""
Test Offset Application.

Tests time offset logic and the offset registry integration.
"""

import pandas as pd
import pytest

from python.configuration.import_config_manager import ImportConfigManager
from python.data_management.importers.tick_importer import TickDataImporter
from tests.import_pipeline.conftest import (
    build_minimal_tick_json,
    find_tick_parquets,
    write_json_fixture,
)


class TestOffsetCorrectness:
    """Verify that offsets are applied correctly per broker_type."""

    def test_offset_applied_when_registry_has_nonzero(self, tmp_path):
        """With offset_registry={mt5: -3}, MT5 timestamps should shift by -3h."""
        source = tmp_path / "source"
        target = tmp_path / "target"

        # Ticks at 15:00 — with -3h offset, should become 12:00
        data = build_minimal_tick_json(
            symbol="EURUSD",
            broker_type="mt5",
            tick_count=3,
            custom_ticks=[
                {"timestamp": "2026.01.15 15:00:00", "bid": 1.1, "ask": 1.1001,
                 "last": 1.1, "tick_volume": 0, "real_volume": 0.0,
                 "chart_tick_volume": 1, "spread_points": 1, "spread_pct": 0.01,
                 "tick_flags": "BUY", "session": "london", "server_time": "2026.01.15 15:00:00"},
                {"timestamp": "2026.01.15 15:01:00", "bid": 1.1001, "ask": 1.1002,
                 "last": 1.1001, "tick_volume": 0, "real_volume": 0.0,
                 "chart_tick_volume": 1, "spread_points": 1, "spread_pct": 0.01,
                 "tick_flags": "BUY", "session": "london", "server_time": "2026.01.15 15:01:00"},
                {"timestamp": "2026.01.15 15:02:00", "bid": 1.1002, "ask": 1.1003,
                 "last": 1.1002, "tick_volume": 0, "real_volume": 0.0,
                 "chart_tick_volume": 1, "spread_points": 1, "spread_pct": 0.01,
                 "tick_flags": "BUY", "session": "london", "server_time": "2026.01.15 15:02:00"},
            ],
        )
        write_json_fixture(source, "EURUSD_ticks.json", data)

        importer = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            offset_registry={"mt5": -3},
            auto_render_bars=False,
        )
        importer.process_all_exports()

        parquet_file = find_tick_parquets(target)[0]
        df = pd.read_parquet(parquet_file)

        # 15:00 - 3h = 12:00
        assert df["timestamp"].iloc[0].hour == 12

    def test_offset_not_applied_when_registry_has_zero(self, tmp_path):
        """With offset_registry={kraken_spot: 0}, timestamps should stay unchanged."""
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
            offset_registry={"kraken_spot": 0},
            auto_render_bars=False,
        )
        importer.process_all_exports()

        parquet_file = find_tick_parquets(target)[0]
        df = pd.read_parquet(parquet_file)

        # Original first tick is at 10:00, should remain 10:00
        assert df["timestamp"].iloc[0].hour == 10

    def test_offset_not_applied_when_broker_not_in_registry(self, tmp_path):
        """If broker_type is not in offset_registry, no offset is applied."""
        source = tmp_path / "source"
        target = tmp_path / "target"

        data = build_minimal_tick_json(
            symbol="ADAUSD",
            broker_type="kraken_spot",
            tick_count=3,
        )
        write_json_fixture(source, "ADAUSD_ticks.json", data)

        # Registry has only mt5, not kraken_spot
        importer = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            offset_registry={"mt5": -3},
            auto_render_bars=False,
        )
        importer.process_all_exports()

        parquet_file = find_tick_parquets(target)[0]
        df = pd.read_parquet(parquet_file)
        assert df["timestamp"].iloc[0].hour == 10

    def test_offset_direction_correct_negative(self, tmp_path):
        """Negative offset should subtract hours (e.g. -3: 15:00 → 12:00)."""
        source = tmp_path / "source"
        target = tmp_path / "target"

        data = build_minimal_tick_json(
            symbol="USDJPY",
            broker_type="mt5",
            tick_count=1,
            custom_ticks=[{
                "timestamp": "2026.01.15 18:00:00", "bid": 150.0, "ask": 150.01,
                "last": 150.0, "tick_volume": 0, "real_volume": 0.0,
                "chart_tick_volume": 1, "spread_points": 1, "spread_pct": 0.01,
                "tick_flags": "BUY", "session": "new_york",
                "server_time": "2026.01.15 18:00:00",
            }],
        )
        write_json_fixture(source, "USDJPY_ticks.json", data)

        importer = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            offset_registry={"mt5": -3},
            auto_render_bars=False,
        )
        importer.process_all_exports()

        parquet_file = find_tick_parquets(target)[0]
        df = pd.read_parquet(parquet_file)
        # 18:00 - 3h = 15:00
        assert df["timestamp"].iloc[0].hour == 15


class TestSessionRecalculation:
    """Verify sessions are recalculated after offset application."""

    def test_session_recalculated_after_offset(self, tmp_path):
        """After offset, session should reflect UTC hour, not original."""
        source = tmp_path / "source"
        target = tmp_path / "target"

        # Tick at 00:00 GMT+3 with session "sydney_tokyo"
        # After -3h offset: 21:00 UTC → should be "transition"
        data = build_minimal_tick_json(
            symbol="GBPUSD",
            broker_type="mt5",
            tick_count=1,
            custom_ticks=[{
                "timestamp": "2026.01.15 00:00:00", "bid": 1.25, "ask": 1.2501,
                "last": 1.25, "tick_volume": 0, "real_volume": 0.0,
                "chart_tick_volume": 1, "spread_points": 1, "spread_pct": 0.01,
                "tick_flags": "BUY", "session": "sydney_tokyo",
                "server_time": "2026.01.15 00:00:00",
            }],
        )
        write_json_fixture(source, "GBPUSD_ticks.json", data)

        importer = TickDataImporter(
            source_dir=str(source),
            target_dir=str(target),
            offset_registry={"mt5": -3},
            auto_render_bars=False,
        )
        importer.process_all_exports()

        parquet_file = find_tick_parquets(target)[0]
        df = pd.read_parquet(parquet_file)

        # 00:00 - 3h = 21:00 UTC → session should be "transition"
        assert df["session"].iloc[0] == "transition"

    def test_session_preserved_when_no_offset(self, tmp_path):
        """Without offset, session should remain as-is from input."""
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
            offset_registry={"kraken_spot": 0},
            auto_render_bars=False,
        )
        importer.process_all_exports()

        parquet_file = find_tick_parquets(target)[0]
        df = pd.read_parquet(parquet_file)
        # Original session was "24h", should stay "24h"
        assert df["session"].iloc[0] == "24h"


class TestImportConfigOffsetRegistry:
    """Verify ImportConfigManager offset registry works correctly."""

    def test_config_returns_correct_mt5_offset(self):
        """ImportConfigManager should return -3 for mt5."""
        config = ImportConfigManager()
        assert config.get_default_offset("mt5") == -3

    def test_config_returns_zero_for_kraken(self):
        """ImportConfigManager should return 0 for kraken_spot."""
        config = ImportConfigManager()
        assert config.get_default_offset("kraken_spot") == 0

    def test_config_returns_zero_for_unknown_broker(self):
        """Unknown broker_type should return 0 (no offset)."""
        config = ImportConfigManager()
        assert config.get_default_offset("nonexistent_broker") == 0
