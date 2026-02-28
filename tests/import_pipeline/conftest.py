"""
Import Pipeline Test Fixtures.

Provides synthetic JSON data builders and session-scoped
temp directories for isolated import pipeline testing.
Does NOT depend on real imported data.

Persistent test output: After each session, reference Parquets
are written to data/test/import/processed/ (from import_config.json
test_paths). These persist for inspection and as input for future
bar renderer tests. The processed directory is cleaned at session
start to avoid duplicate detection conflicts.
"""

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from python.configuration.import_config_manager import ImportConfigManager
from python.data_management.importers.tick_importer import TickDataImporter


# =============================================================================
# SYNTHETIC DATA BUILDERS
# =============================================================================

def build_minimal_tick_json(
    symbol: str = "TESTUSD",
    broker: str = "TestBroker",
    broker_type: str = "kraken_spot",
    start_time: str = "2026.01.15 10:00:00",
    tick_count: int = 5,
    bid_start: float = 1.10000,
    ask_start: float = 1.10010,
    data_format_version: str = "1.2.0",
    broker_utc_offset_hours: int = 0,
    extra_metadata: Optional[Dict[str, Any]] = None,
    custom_ticks: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Build a minimal valid MQL5 JSON tick export for testing.

    Args:
        symbol: Symbol name
        broker: Broker display name
        broker_type: Broker type identifier (must exist in market_config.json)
        start_time: Start timestamp string
        tick_count: Number of synthetic ticks to generate
        bid_start: Starting bid price
        ask_start: Starting ask price
        data_format_version: MQL5 data format version
        broker_utc_offset_hours: Broker UTC offset
        extra_metadata: Additional metadata fields to merge
        custom_ticks: Override auto-generated ticks with custom list

    Returns:
        Dict matching ImportJsonSchema structure
    """
    metadata = {
        "symbol": symbol,
        "broker": broker,
        "broker_type": broker_type,
        "start_time": start_time,
        "data_format_version": data_format_version,
        "broker_utc_offset_hours": broker_utc_offset_hours,
        "data_collector": broker_type,
        "server": "test_server",
        "collection_purpose": "testing",
        "operator": "automated",
        "symbol_info": {
            "point_value": 0.00001,
            "digits": 5,
            "tick_size": 0.00001,
            "tick_value": 1.0
        },
        "collection_settings": {
            "max_ticks_per_file": 50000,
            "max_errors_per_file": 1000,
            "include_real_volume": True,
            "include_tick_flags": True,
            "stop_on_fatal_errors": False
        },
        "error_tracking": {
            "enabled": True,
            "log_negligible": True,
            "log_serious": True,
            "log_fatal": True,
            "max_spread_percent": 5.0,
            "max_price_jump_percent": 10.0,
            "max_data_gap_seconds": 300
        }
    }

    if extra_metadata:
        metadata.update(extra_metadata)

    if custom_ticks is not None:
        ticks = custom_ticks
    else:
        ticks = []
        for i in range(tick_count):
            bid = bid_start + (i * 0.00001)
            ask = ask_start + (i * 0.00001)
            minute = i // 60
            second = i % 60
            ts = f"2026.01.15 10:{minute:02d}:{second:02d}"
            ticks.append({
                "timestamp": ts,
                "time_msc": 1769000000000 + (i * 1000),
                "bid": round(bid, 5),
                "ask": round(ask, 5),
                "last": round(bid, 5),
                "tick_volume": 0,
                "real_volume": 100.0 + i,
                "chart_tick_volume": 1,
                "spread_points": 1,
                "spread_pct": 0.01,
                "tick_flags": "BUY",
                "session": "24h",
                "server_time": ts
            })

    return {"metadata": metadata, "ticks": ticks}


def find_tick_parquets(target_dir: Path) -> List[Path]:
    """
    Find tick Parquet files in target directory, excluding index files.

    Args:
        target_dir: Target directory to search

    Returns:
        List of Parquet file paths (excluding hidden/index files)
    """
    return [f for f in target_dir.glob("**/*.parquet") if not f.name.startswith(".")]


def write_json_fixture(directory: Path, filename: str, data: Dict[str, Any]) -> Path:
    """
    Write a JSON dict to a file in the given directory.

    Args:
        directory: Target directory
        filename: File name (should end with _ticks.json)
        data: JSON-serializable dict

    Returns:
        Path to written file
    """
    directory.mkdir(parents=True, exist_ok=True)
    filepath = directory / filename
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return filepath


# =============================================================================
# SESSION-SCOPED FIXTURES
# =============================================================================

@pytest.fixture(scope="session")
def import_test_dirs(tmp_path_factory) -> Dict[str, Path]:
    """
    Create temporary source, target, and finished directories for import tests.

    Returns:
        Dict with 'source', 'target', and 'finished' Path objects
    """
    source = tmp_path_factory.mktemp("import_source")
    target = tmp_path_factory.mktemp("import_target")
    finished = tmp_path_factory.mktemp("import_finished")
    return {"source": source, "target": target, "finished": finished}


@pytest.fixture(scope="session", autouse=True)
def populate_persistent_test_output():
    """
    Write reference Parquets to persistent test directories.

    Uses test_paths from import_config.json. Cleans processed/
    before import to avoid duplicate detection conflicts.
    Output persists after test run for inspection and as input
    for future bar renderer tests.
    """
    config = ImportConfigManager()
    raw_dir = Path(config.get_test_data_raw_path())
    processed_dir = Path(config.get_test_import_output_path())
    finished_dir = Path(config.get_test_data_finished_path())

    # Clean all directories (avoid duplicate conflicts, fresh start)
    for dir_path in [raw_dir, processed_dir, finished_dir]:
        if dir_path.exists():
            for item in dir_path.iterdir():
                if item.is_dir():
                    shutil.rmtree(item)
                elif item.name != ".gitkeep":
                    item.unlink()

    # Create dirs
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    finished_dir.mkdir(parents=True, exist_ok=True)

    # Generate reference data into raw/ (authentic pipeline: raw → processed)
    fixtures = [
        ("BTCUSD", "kraken_spot", 0),
        ("ETHUSD", "kraken_spot", 0),
        ("EURUSD", "mt5", -3),
        ("GBPUSD", "mt5", -3),
    ]

    for symbol, broker_type, offset in fixtures:
        data = build_minimal_tick_json(
            symbol=symbol,
            broker_type=broker_type,
            tick_count=20,
            broker_utc_offset_hours=offset,
        )
        write_json_fixture(raw_dir, f"{symbol}_ticks.json", data)

    # Import: raw/ → processed/, move JSONs to finished/
    importer = TickDataImporter(
        source_dir=str(raw_dir),
        target_dir=str(processed_dir),
        offset_registry={"mt5": -3, "kraken_spot": 0},
        move_processed_files=True,
        finished_dir=str(finished_dir),
        auto_render_bars=False,
    )
    importer.process_all_exports()

    # After import:
    #   raw/       → empty (JSONs moved)
    #   processed/ → 4 Parquets (reference output)
    #   finished/  → 4 JSONs (raw files for inspection)

    yield
