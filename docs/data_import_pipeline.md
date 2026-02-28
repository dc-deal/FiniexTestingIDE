# Data Import Pipeline

## Overview

The import pipeline converts MQL5 JSON tick exports into optimized Parquet files with UTC-normalized timestamps, quality metrics, and preserved source metadata. After tick import, bars are pre-rendered for all standard timeframes (M1 through D1).

**Flow:**
```
MQL5 TickCollector (JSON)
       ↓
  TickDataImporter
  ├─ Validate JSON schema
  ├─ Detect duplicates (hash-based)
  ├─ Apply UTC offset (from offset registry)
  ├─ Recalculate sessions (UTC-based)
  ├─ Quality checks (prices, spreads)
  └─ Write Parquet (with source metadata)
       ↓
  BarImporter (auto-triggered, same target_dir)
  ├─ Load all ticks for symbol (from target_dir, not config)
  ├─ VectorizedBarRenderer → M1, M5, M15, M30, H1, H4, D1
  └─ Write bar Parquet files
       ↓
  Index Update (tick + bar indexes, target_dir-aware)
```

---

## JSON Input Schema

The MQL5 JSON tick export has two top-level keys: `metadata` and `ticks`.

### Mandatory Metadata Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `symbol` | string | Always | Trading instrument (e.g. "EURUSD", "BTCUSD") |
| `start_time` | string | Always | Collection start timestamp |
| `broker_type` | string | One of both | Broker identifier (e.g. "mt5", "kraken_spot") |
| `data_collector` | string | One of both | Legacy alias for `broker_type` (older MQL5 exports) |

### Mandatory Tick Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `timestamp` | string | Always | Format: "YYYY.MM.DD HH:MM:SS" |
| `bid` | float | Always | Bid price |
| `ask` | float | Always | Ask price |

### Optional Metadata Fields (v1.0.5+)

| Field | Type | Description |
|-------|------|-------------|
| `broker` | string | Broker company name |
| `server` | string | Server identifier |
| `broker_utc_offset_hours` | int | Broker's UTC offset (informational) |
| `local_device_time` | string | Device time at collection start |
| `broker_server_time` | string | Server time at collection start |
| `start_time_unix` | int | Unix timestamp of start_time |
| `data_format_version` | string | Schema version (e.g. "1.0.5") |
| `collection_purpose` | string | Purpose (e.g. "backtesting") |
| `operator` | string | Collector operator identifier |
| `timeframe` | string | Collection timeframe |
| `volume_timeframe` | string | Volume aggregation timeframe |
| `volume_timeframe_minutes` | int | Volume timeframe in minutes |
| `symbol_info` | object | Symbol specification (see nested schema) |
| `collection_settings` | object | Collector configuration (see nested schema) |
| `error_tracking` | object | Error tracking config (see nested schema) |

### Optional Tick Fields (v1.0.5+)

| Field | Type | Description |
|-------|------|-------------|
| `last` | float | Last trade price |
| `real_volume` | float | Real trade volume (crypto > 0, forex = 0 — source-dependent) |
| `tick_volume` | int | Tick-based volume |
| `chart_tick_volume` | int | Chart tick volume counter |
| `spread_points` | int | Spread in points |
| `spread_pct` | float | Spread as percentage |
| `tick_flags` | string | Tick type flags (e.g. "BUY") |
| `session` | string | Trading session label |
| `server_time` | string | Server-side timestamp |
| `time_msc` | int | Millisecond-precision timestamp |

### Nested Metadata Schemas

These are stored as JSON strings in Parquet under `source_meta_*` prefix.

**symbol_info:**

| Field | Type | Description |
|-------|------|-------------|
| `point_value` | float | Point value for the symbol |
| `digits` | int | Decimal places |
| `tick_size` | float | Minimum price movement |
| `tick_value` | float | Monetary value of one tick |

**collection_settings:**

| Field | Type | Description |
|-------|------|-------------|
| `max_ticks_per_file` | int | Maximum ticks before file rotation |
| `max_errors_per_file` | int | Maximum errors before abort |
| `include_real_volume` | bool | Whether real volume is collected |
| `include_tick_flags` | bool | Whether tick flags are collected |
| `stop_on_fatal_errors` | bool | Abort on fatal errors |

**error_tracking:**

| Field | Type | Description |
|-------|------|-------------|
| `enabled` | bool | Error tracking active |
| `log_negligible` | bool | Log minor issues |
| `log_serious` | bool | Log serious issues |
| `log_fatal` | bool | Log fatal issues |
| `max_spread_percent` | float | Spread warning threshold |
| `max_price_jump_percent` | float | Price jump warning threshold |
| `max_data_gap_seconds` | int | Data gap warning threshold |

### Minimal Valid JSON

The absolute minimum the importer accepts:

```json
{
  "metadata": {
    "symbol": "BTCUSD",
    "start_time": "2026.01.15 10:00:00",
    "broker_type": "kraken_spot"
  },
  "ticks": [
    { "timestamp": "2026.01.15 10:00:00", "bid": 42000.50, "ask": 42001.20 },
    { "timestamp": "2026.01.15 10:00:01", "bid": 42001.00, "ask": 42001.70 }
  ]
}
```

### Validation Rules

**Always Required:**
- Top-level `metadata` and `ticks` keys must be present
- `metadata.symbol` and `metadata.start_time`
- At least one of `broker_type` or `data_collector` in metadata
- `broker_type` value must exist in `market_config.json`
- Each tick must have `timestamp`, `bid`, `ask`

**Edge Case Behavior:**

| Condition | Result |
|-----------|--------|
| Missing `metadata` or `ticks` key | Error collected, file skipped |
| Missing `broker_type` and `data_collector` | Error collected, file skipped |
| Unknown `broker_type` (not in market_config) | Error collected, file skipped |
| Empty ticks array | File skipped silently (no error, no output) |
| Invalid prices (bid ≤ 0) | Warning logged, import continues |
| Extreme spreads (spread_pct > 5%) | Warning logged, import continues |
| Duplicate file (same SHA-256 hash) | `ArtificialDuplicateException`, skipped (use `--override`) |

Full TypedDict definitions: `python/framework/types/import_schema_types.py`

---

## Configuration

Import configuration lives in `configs/import_config.json` with optional user overrides in `user_config/import_config.json`.

### Structure

```json
{
    "version": "1.0",
    "paths": {
        "data_raw": "data/raw",
        "import_output": "data/processed",
        "data_finished": "data/finished"
    },
    "test_paths": {
        "data_raw": "data/test/import/raw",
        "import_output": "data/test/import/processed",
        "data_finished": "data/test/import/finished"
    },
    "offset_registry": {
        "mt5": {
            "default_offset_hours": -3,
            "description": "MetaTrader 5 brokers typically report GMT+3"
        },
        "kraken_spot": {
            "default_offset_hours": 0,
            "description": "Kraken reports in UTC natively"
        }
    },
    "processing": {
        "move_processed_files": true,
        "auto_render_bars": true
    }
}
```

### Offset Registry

Each broker type has a registered UTC offset. During import, the offset is looked up per-file based on the `broker_type` in the JSON metadata:

- **mt5:** `-3` hours (MT5 brokers typically report GMT+3, so subtract 3h for UTC)
- **kraken_spot:** `0` hours (already UTC)

To add a new broker or override an offset, create `user_config/import_config.json`:
```json
{
    "offset_registry": {
        "my_broker": {
            "default_offset_hours": -2,
            "description": "My broker reports GMT+2"
        }
    }
}
```

### Test Paths

The `test_paths` block provides isolated directories for the test suite, preventing tests from touching production data. The test session fixture generates reference Parquets (BTCUSD, ETHUSD as `kraken_spot` + EURUSD, GBPUSD as `mt5`) into `data/test/import/processed/`. The processed directory is cleaned at session start to avoid duplicate detection conflicts.

```
data/test/import/
├── raw/           ← Synthetic JSON fixtures (generated, then moved)
├── processed/     ← Reference Parquets (persist after test run)
└── finished/      ← Moved JSONs after successful import
```

### Config API

`ImportConfigManager` (in `python/configuration/import_config_manager.py`) provides:

| Method | Returns |
|--------|---------|
| `get_default_offset(broker_type)` | Offset hours for broker (0 if unknown) |
| `get_offset_registry()` | Full `{broker_type: hours}` dict |
| `get_data_raw_path()` | Source directory path |
| `get_import_output_path()` | Output directory path |
| `get_data_finished_path()` | Finished directory path |
| `get_move_processed_files()` | bool |
| `get_auto_render_bars()` | bool |

---

## Parquet Metadata

Each output Parquet file includes metadata in the file header:

### Core Fields (always present)

| Key | Description |
|-----|-------------|
| `source_file` | Original JSON filename |
| `symbol` | Trading symbol |
| `broker_type` | Broker identifier |
| `market_type` | Market category (forex, crypto) |
| `importer_version` | TickDataImporter.VERSION |
| `tick_count` | Number of ticks |
| `data_format_version` | Schema version |
| `utc_conversion_applied` | "true"/"false" |
| `user_time_offset_hours` | Applied offset (e.g. "-3") |
| `session_recalculated` | "true"/"false" |

### Source Metadata (preserved from JSON)

Original MQL5 metadata is preserved with `source_meta_` prefix:
- Flat scalars: `source_meta_broker_type`, `source_meta_data_format_version`, etc.
- Nested objects stored as JSON strings: `source_meta_symbol_info`, `source_meta_collection_settings`, `source_meta_error_tracking`

---

## CLI Usage

```bash
# Standard import (reads offset registry from config)
python -m python.cli.data_index_cli import

# Override mode (re-import existing files)
python -m python.cli.data_index_cli import --override
```

The CLI displays the active offset registry on startup:
```
════════════════════════════════════════
📥 Tick Data Import
════════════════════════════════════════
Override Mode: DISABLED
Offset Registry:
   mt5:            -3h (MetaTrader 5 brokers typically report GMT+3)
   kraken_spot:     0h (Kraken reports in UTC natively)
════════════════════════════════════════
```

---

## Duplicate Detection

The importer calculates a SHA-256 hash of each JSON file and compares against existing Parquet metadata. If a file was already imported, it raises `ArtificialDuplicateException` (skipped gracefully). Use `--override` to force re-import.

---

## Directory Structure

```
data/processed/
├── .parquet_tick_index.json
├── .parquet_bars_index.json
├── {broker_type}/
│   ├── ticks/
│   │   └── {SYMBOL}/
│   │       └── {SYMBOL}_ticks_YYYYMMDD_HHMMSS.parquet
│   └── bars/
│       └── {SYMBOL}/
│           ├── {SYMBOL}_M1_BARS.parquet
│           ├── {SYMBOL}_M5_BARS.parquet
│           └── ...
```
