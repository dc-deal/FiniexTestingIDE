# Data Import Pipeline

## Overview

The import pipeline converts JSON tick exports from data collectors into optimized Parquet files with UTC-normalized timestamps, quality metrics, and preserved source metadata. After tick import, bars are pre-rendered for all standard timeframes (M1 through D1).

**Related**: [tick_collector_guide.md](tick_collector_guide.md) — MQL5 collector usage, JSON schema, error classification.

**Flow:**
```
Data Collectors (JSON)
├─ MQL5 TickCollector (MT5 broker ticks)
└─ Kraken Data Collector (Kraken WebSocket ticks)
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
  │   └─ Weekend/holiday exclusion (Forex only, see below)
  ├─ Parallel rendering (symbol-level, ProcessPoolExecutor)
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

### Optional Metadata Fields

| Field | Type | Description |
|-------|------|-------------|
| `broker` | string | Broker company name |
| `server` | string | Server identifier |
| `broker_utc_offset_hours` | int | Broker's UTC offset (informational) |
| `local_device_time` | string | Device time at collection start |
| `broker_server_time` | string | Server time at collection start |
| `start_time_unix` | int | Unix timestamp of start_time |
| `data_format_version` | string | Schema version of the data collector |
| `collection_purpose` | string | Purpose (e.g. "backtesting") |
| `operator` | string | Collector operator identifier |
| `timeframe` | string | Collection timeframe |
| `volume_timeframe` | string | Volume aggregation timeframe |
| `volume_timeframe_minutes` | int | Volume timeframe in minutes |
| `symbol_info` | object | Symbol specification (see nested schema) |
| `collection_settings` | object | Collector configuration (see nested schema) |
| `error_tracking` | object | Error tracking config (see nested schema) |

### Metadata Timestamp Architecture

Four metadata fields capture temporal context at collection start. Their timezone semantics differ by broker:

| Field | MT5 | Kraken | Purpose |
|-------|-----|--------|---------|
| `start_time` | Broker time (GMT+3) | UTC | Collection start as formatted string |
| `start_time_unix` | UTC epoch (seconds) | UTC epoch (seconds) | Absolute UTC anchor |
| `local_device_time` | Collector machine local time (GMT+1) | Collector machine local time (GMT+1) | Device clock at start |
| `broker_server_time` | Broker time (GMT+3) | UTC | Exchange/broker server time |

**Key invariant**: `start_time_unix` is always a UTC epoch timestamp regardless of broker. It serves as the absolute reference point.

**Deriving the collector timezone** from any file:

```
collector_utc_offset = local_device_time - epoch_to_datetime(start_time_unix)
```

Example (MT5, real data):
```
start_time_unix:     1773002406  → 2026.03.08 17:40:06 UTC
local_device_time:   "2026.03.08 18:40:06"
                     18:40:06 - 17:40:06 = +1h → GMT+1
```

Example (Kraken, real data):
```
start_time_unix:     1772991694  → 2026.03.08 17:41:34 UTC
local_device_time:   "2026.03.08 18:41:34"
                     18:41:34 - 17:41:34 = +1h → GMT+1
```

> **Note**: These metadata timestamps are informational only — they are preserved in Parquet metadata but never used in pipeline calculations. Actual tick timing relies exclusively on `time_msc` and `collected_msc` (epoch-based, unambiguous).

### Optional Tick Fields

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
| `time_msc` | int64 | Broker matching engine timestamp (Unix epoch ms), UTC-converted by importer. Not monotonic in arrival order |
| `collected_msc` | int64 | Local device clock at tick receipt (Unix epoch ms). Monotonic. Added in V1.3.0. Default `0` for older data |

> **Note — `timestamp` redundancy**: The mandatory `timestamp` field (human-readable, seconds precision) is derivable from `time_msc` with the broker UTC offset. It remains mandatory for backward compatibility but may be deprecated in a future data format revision.

> **Note — `collected_msc`**: Per-tick collection timestamp (`collected_msc`, Unix epoch ms, monotonic) added in V1.3.0. 

> **Note — `server_time` removed**: The per-tick `server_time` field (string, same precision as `timestamp`) was removed from the import schema. It was redundant with `time_msc`. Old data files may still contain it — the importer drops it during Parquet export (column filter). New collectors no longer produce it.

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

Import configuration lives in `configs/import_config.json` with optional user overrides in `user_configs/import_config.json`.

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
        "auto_render_bars": true,
        "bar_render_workers": 16
    }
}
```

### Offset Registry

Each broker type has a registered UTC offset. During import, the offset is looked up per-file based on the `broker_type` in the JSON metadata:

- **mt5:** `-3` hours (MT5 brokers typically report GMT+3, so subtract 3h for UTC)
- **kraken_spot:** `0` hours (already UTC)

To add a new broker or override an offset, create `user_configs/import_config.json`:
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
| `get_bar_render_workers()` | int (fallback: 1, see `processing.bar_render_workers` in config) |

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

### Source Metadata (preserved from JSON)

Original MQL5 metadata is preserved with `source_meta_` prefix:
- Flat scalars: `source_meta_broker_type`, `source_meta_data_format_version`, etc.
- Nested objects stored as JSON strings: `source_meta_symbol_info`, `source_meta_collection_settings`, `source_meta_error_tracking`

### Data Format Version Tracking

`data_format_version` flows from Parquet metadata through the tick index into batch execution reports:

```
Parquet metadata → TickIndexManager (index entry) → SharedDataPreparator
    → SingleScenario.data_format_versions → PortfolioSummary (warning)
```

**Index**: `TickIndexManager` extracts `data_format_version` from each Parquet file's custom metadata and stores it per index entry. Cached index files without this field default to `'unknown'` (requires index rebuild to populate).

**Report warning**: When any scenario uses pre-V1.3.0 data (version is `'unknown'` or < `'1.3.0'`), the aggregated portfolio section displays:

```
⚠️  Data includes pre-V1.3.0 files (186/186): inter-tick intervals based on synthesized collected_msc
```

This warns that inter-tick interval calculations use synthesized `collected_msc` (derived from `time_msc`) rather than authentic collector timestamps. Pre-V1.3.0 data was restored via `restore_collected_msc.py` which synthesizes monotonic `collected_msc` from `time_msc` — accurate for interval ordering but not for true collection timing.

**Data flow**: Uses Channel C (main-process only, no subprocess serialization) — see [architecture_execution_layer.md](../architecture/architecture_execution_layer.md#batch-data-flow-main-process--subprocesses--reports).

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

## Parquet → Simulation Pipeline

After import, tick data flows from Parquet into the simulation engine:

```
Parquet (data/processed/{broker_type}/ticks/{SYMBOL}/)
       ↓
  SharedDataPreparator
  ├─ Loads tick Parquet via pd.read_parquet()
  ├─ Filters by timestamp (UTC-aware)
  ├─ serialize_ticks_for_transport(df) → trimmed dicts
  │   Only TickTransportColumn fields cross the process boundary
  └─ Passes trimmed tick dicts via ProcessDataPackage (pickle)
       ↓
  process_deserialize_ticks_batch()          [process_serialization_utils.py]
  ├─ Derives timestamp from time_msc (epoch ms → UTC datetime)
  ├─ Symbol from scenario config (not from dict)
  └─ Produces TickData objects for tick loop
       ↓
  ProcessTickLoop
  ├─ Iterates TickData objects in order
  ├─ Inter-tick interval: collected_msc (monotonic, preferred)
  │   Fallback: time_msc when collected_msc == 0 (pre-V1.3.0 data)
  └─ Feeds TradingEnvironment per tick
```

### Transport Contract (`TickTransportColumn`)

Only fields defined in `TickTransportColumn` (`market_data_types.py`) cross the process boundary. All other Parquet columns are trimmed before serialization to reduce pickle payload (~50% reduction).

| Transport Field | Parquet Column | TickData Field | Notes |
|----------------|---------------|----------------|-------|
| `TIME_MSC` | `time_msc` (int64) | `time_msc` + `timestamp` | timestamp derived via `datetime.fromtimestamp()` |
| `COLLECTED_MSC` | `collected_msc` (int64) | `collected_msc` | Optional, default 0 (pre-V1.3.0) |
| `BID` | `bid` (float) | `bid` | Mandatory |
| `ASK` | `ask` (float) | `ask` | Mandatory |
| `VOLUME` | `volume` (float) | `volume` | Optional, default 0.0 |

### Dropped at Transport Boundary

These fields exist in Parquet but are **not** transported to subprocesses:

| Field | Reason |
|-------|--------|
| `timestamp` | Derived from `time_msc` during deserialization (eliminates Pandas Timestamp from pickle) |
| `symbol` | Injected from scenario config during deserialization |
| `last`, `tick_volume`, `real_volume`, `chart_tick_volume` | Not consumed by tick loop |
| `spread_points`, `spread_pct` | Quality checks only (pre-transport) |
| `tick_flags`, `session` | Import metadata only |

> **Note**: `collected_msc` and `time_msc` are preserved as int64 throughout. The importer applies UTC offset to `time_msc` (not `collected_msc`). The simulation reads both from TickData and decides which to use for inter-tick intervals.

---

## Duplicate Detection

The importer calculates a SHA-256 hash of each JSON file and compares against existing Parquet metadata. If a file was already imported, it raises `ArtificialDuplicateException` (skipped gracefully). Use `--override` to force re-import.

---

## Gap Handling

It is standard exchange/broker behavior to not render bars for time periods where no tick updates occurred. The bar renderer follows this principle — gaps result in timestamp jumps, not fill bars.

| Situation | Bar Output | Meaning |
|-----------|-----------|---------|
| Weekend (Sat/Sun) | No bars (time jump) | Expected market closure — Forex only |
| Holiday (Christmas, New Year) | No bars (time jump) | Expected market closure — Forex only |
| Data gap (collector outage) | No bars (time jump) | Data quality problem detected via gap detection |
| Crypto weekend | Normal bars | 24/7 market, no closure |

This behavior is consistent across all three renderers: `VectorizedBarRenderer` (batch import), `BarRenderer` (tick loop / backtesting), and live operation.

The market closure behavior is controlled by `market_config.json` → `market_rules.{market_type}.weekend_closure`. Forex has `weekend_closure: true`, Crypto has `weekend_closure: false`.

### Gap Detection

Gap detection (`DataCoverageReport`) uses timestamp jumps between consecutive bars at the configured granularity (`discoveries_config.json` → `data_coverage.granularity`, default: M1). Gaps are classified via `MarketCalendar`:

- **WEEKEND / HOLIDAY** — expected market closure, allowed by default
- **SHORT** (< 30 min) — minor interruption, allowed by default
- **MODERATE** (30 min – 4h) — requires attention, blocks scenario generation
- **LARGE** (> 4h) — data collection problem, blocks scenario generation

Allowed gap categories are configured in `app_config.json` → `data_validation.allowed_gap_categories`. The block generator splits only at non-allowed gap categories.

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
