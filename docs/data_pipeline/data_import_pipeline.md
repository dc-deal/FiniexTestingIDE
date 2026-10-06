# Data Import Pipeline

## Overview

The import pipeline converts JSON tick exports from data collectors into optimized Parquet files
with UTC-normalized timestamps, validated on the way in, and preserved source metadata. After tick import,
bars are pre-rendered for all standard timeframes (M1 through D1).

**Related**: [tick_collector_guide.md](tick_collector_guide.md) — MQL5 collector usage, JSON schema, error classification.

**Scope — this doc covers the TICK path only.** Signal data (LLM sentiment envelopes, JSONL → parquet) has its own importer with its own duplicate rule; see [signal_data_source.md](signal_data_source.md).

**Flow:**
```
Data Collectors (JSON)
├─ MQL5 TickCollector (MT5 broker ticks)
└─ Kraken Data Collector (Kraken WebSocket ticks)
       ↓
  TickDataImporter
  ├─ Validate JSON schema
  ├─ Detect duplicates (by source file name)
  ├─ Convert the server clock to UTC (the broker's rule, market_config.json)
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
| `broker_utc_offset_hours` | int | Always 0 — written by MT5 collectors 1.0.5–1.3.0 from a formula that could only yield 0; never read |
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
| `error_tracking` | object | The collector's own quality-check settings — read, not stored (see below) |

### Metadata Timestamp Architecture

Four metadata fields describe the moment a file was opened. **None of them is a UTC anchor for
MT5**, and none is used by the pipeline:

| Field | MT5 | Kraken |
|-------|-----|--------|
| `start_time` | The server's wall clock (New York time + 7 h — UTC+2 in US winter, UTC+3 in US summer) | UTC |
| `start_time_unix` | That same wall clock read as if it were UTC — **not** a UTC instant | UTC epoch (seconds) |
| `local_device_time` | The collector machine's clock. **Synthesized** for every file before 2026-03-08 by an early restoration (`start_time − 2 h`), not measured | The collector machine's clock |
| `broker_server_time` | The server's wall clock; before 2026-03-08 copied from `start_time` by the same restoration | UTC |

So no timezone can be derived from these fields for MT5. Example: `start_time_unix` 1773002406 read
as UTC is 2026-03-08 20:40:06 — the server's wall clock, which ran at UTC+3 that day, so the file
opened at 17:40:06 UTC.

> **Tick timing relies exclusively on `time_msc` and `collected_msc`.** `time_msc` is the server's
> wall clock as epoch milliseconds and is converted at import through the broker's server clock
> rule (see *Server Clock* below). `collected_msc` is the arrival time in UTC: written so by the MT5
> collector from format 1.5.0, restored into UTC for older files by one-time migrations.

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
| `time_msc` | int64 | Broker matching engine timestamp (Unix epoch ms) — the event time. UTC-converted by importer. Non-decreasing; ties are normal on burst-heavy feeds |
| `collected_msc` | int64 | Local clock at tick receipt (Unix epoch ms) — the arrival time. Already UTC from data format 1.5.0, so the importer does not convert it. Non-decreasing. Default `0` for pre-V1.3.0 data |

> **Note — `timestamp` redundancy**: The mandatory `timestamp` field (human-readable, seconds
> precision) is derivable from `time_msc` with the broker UTC offset. It remains mandatory for
> backward compatibility but may be deprecated in a future data format revision.

> **Note — `collected_msc` time base**: The field is UTC from data format 1.5.0 onwards, declared by
> `collected_msc_timebase` in the metadata. Older files carry device-local time and are converted
> once by the restoration migration (`python/experiments/restore_collected_msc_v3.py`) — **not** by
> the importer, which never rewrites source content.

> **Note — `server_time` removed**: The per-tick `server_time` field (string, same precision as
> `timestamp`) was removed from the import schema. It was redundant with `time_msc`. Old data files
> may still contain it — the importer drops it during Parquet export (column filter). New collectors
> no longer produce it.

### Nested Metadata Schemas

`symbol_info` and `collection_settings` are stored as JSON strings in Parquet under the
`source_meta_*` prefix. `error_tracking` is read and deliberately NOT stored: it configures the
collector's own quality checks, and nothing downstream reads them. The raw file keeps it.

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

**error_tracking** (raw file only):

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
| Already imported (same source file name in parquet metadata) | `ArtificialDuplicateException`, warning + skipped (use `--override`) |

Full TypedDict definitions: `python/framework/types/import_schema_types.py`

### Structural Validation — the importer refuses, it never repairs

`TickImportValidator` (`python/framework/validators/tick_import_validator.py`) checks the
invariants the archive is required to hold. A violation rejects that one file with a
`TickFileValidationException`; the batch keeps running and the file is simply not written. The
importer never rewrites source content to make a file pass — repairing tick data is a one-time
migration in `python/experiments/`.

| Condition | Result |
|-----------|--------|
| `time_msc` or `collected_msc` steps backwards | File rejected |
| Row count disagrees with `summary.total_ticks` | File rejected |
| `bid ≤ 0`, `ask ≤ 0`, or `ask < bid` | File rejected |
| `timestamp` and `time_msc` disagree by more than 1 s | File rejected |
| `collected_msc` further than ±5 min from the tick's UTC event time | File rejected; the message names the migration for legacy files, names a clock conversion when the distance is a whole number of hours (two clocks a whole hour apart, not a late delivery), and otherwise reports a collector defect for files declaring `collected_msc_timebase: "utc"` |
| A week that opens inside the file (Friday close, a 40–56 h gap) opens a whole number of hours away from the market's day anchor (17:00 New York for forex) | File rejected — the server clock was converted by the wrong number of hours. A late open that is no whole hour (a holiday, a feed gap) is only a warning. Markets without weekends are not checked |
| A tick inside the hour a daylight saving change repeats or skips | File rejected — that wall-clock time has no single UTC time (forex is closed then, so the archive has none) |
| Wide spread within the file's declared `max_spread_percent` | Warning, import continues |
| `collected_msc` absent or zero throughout | Warning, import continues (pre-V1.3.0 data has no arrival clock) |
| Ticks sharing an arrival millisecond | Metric only, never a verdict — bursts are legitimate |

The window and threshold constants are measured, not chosen. The ±5 min plausibility window has to
cover more than the receive lag (Kraken median 7 ms): a restored file's residual also carries the
collector's accumulated session drift — measured at ~1 s/day across sessions of 8–11 days — plus the
lift the restoration applies at an anchor change to keep arrival continuous. The worst case measured
over the repaired archive is 30.1 s, from a 21.8 s lift plus 8.4 s of drift. Five minutes leaves an
order of magnitude of headroom and still sits an order of magnitude below the smallest defect class
(a 1 h timezone offset). The 7-day segment-split threshold sits between the largest legitimate
intra-file gap in the archive (48.17 h) and the smallest anchor jump (21.35 d).

**Gap severity is deliberately not judged here.** Whether a gap is a market closure or an outage is
a verdict and belongs to the coverage layer
(`python/framework/discoveries/data_coverage/data_coverage_report.py`), which classifies gaps
against `MarketCalendar`. Gaps are not an import problem — only ordering is.

A second plane runs across files, off the tick index rather than the data:
`validate_archive_ordering()` checks two invariants per symbol.

| Invariant | Reads | Catches |
|---|---|---|
| Files never cover overlapping **event** ranges | `start_time` / `end_time` | two collectors on one symbol, a double import |
| `collected_msc` never steps back from one file to the next (**arrival**) | `collected_start` / `collected_end` | a system-clock correction between two collector sessions |

Which files follow each other is decided by the tick bounds in the index, not by the file name and
not by the header — a file opened at the Friday close carries its first tick 48 h later, so both
would mislead.

The arrival plane covers what a collector cannot see itself: a collector clamps its own clock
within a session, but a correction that happens while it is *not* running leaves no trace in either
file. Only the boundary between them shows it.

An index without `collected_start` / `collected_end` (written before those bounds existed) makes the
arrival plane unverifiable. It then reports one aggregate line naming the number of unchecked
transitions rather than passing silently — a skipped plane that logs like a passed one is how the
check once did nothing while reporting success. Rebuilding the index restores it.

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
    "processing": {
        "move_processed_files": true,
        "auto_render_bars": true,
        "bar_render_workers": 2
    }
}
```

> ⚠️ **Known issue — parallel bar rendering can exhaust memory (work in progress):**
> Each render worker loads the complete tick history of its symbol into RAM. On large tick
> archives (multi-GB per broker type), too many parallel workers can exceed the available
> memory — the OS kills a worker and the whole pool aborts with
> `A process in the process pool was terminated abruptly while the future was running or pending.`
> (`BrokenProcessPool`). Until memory-aware worker scheduling lands, `bar_render_workers`
> stays at a conservative default of `2`. Raise it only for small datasets or RAM-rich systems.

### Server Clock

The importer turns a venue's own timestamps into UTC through the broker's **server clock rule**,
declared on its entry in `configs/market_config.json` — a property of the venue, like its price
formation, so the import, the coverage report and a live adapter read the same rule:

```json
"mt5":         { "server_clock": { "timezone": "America/New_York", "hours_ahead": 7 } }
"kraken_spot": { "server_clock": { "timezone": "UTC",              "hours_ahead": 0 } }
```

A rule rather than a number, because an MT5 server on New York close time follows New York's
daylight saving changes. One rule, two offsets:

| Server wall clock | Season | UTC |
|---|---|---|
| 2026-01-09 15:30 | US standard time — server UTC+2 | 13:30 (the US payroll release, 08:30 New York) |
| 2026-04-03 15:30 | US daylight time — server UTC+3 | 12:30 (the same release) |
| Monday 00:00 | winter / summer | Sunday 22:00 / 21:00 — the forex week opening at 17:00 New York |

The conversion is `time_utils.server_clock_to_utc_ms()`, per tick, so a file spanning a season change
would resolve both offsets. The field is required: a broker without a declared clock refuses to load.

Until 2026-10 this was a fixed `-3` hours in `import_config.json`. Everything MT5 recorded in US winter
(2025-11-02 → 2026-03-08) was therefore stored one hour early until it was re-imported.

### Test Paths

The `test_paths` block provides isolated directories for the test suite, preventing tests from
touching production data. The test session fixture generates reference Parquets (BTCUSD, ETHUSD as
`kraken_spot` + EURUSD, GBPUSD as `mt5`) into `data/test/import/processed/`. The processed directory
is cleaned at session start to avoid duplicate detection conflicts.

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
| `get_data_raw_path()` | Source directory path |
| `get_import_output_path()` | Output directory path |
| `get_data_finished_path()` | Finished directory path |
| `get_move_processed_files()` | bool |
| `get_auto_render_bars()` | bool |
| `get_bar_render_workers()` | int (fallback: 2, see `processing.bar_render_workers` in config) |

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
| `utc_conversion_applied` | "true" when the broker's server clock is not UTC |
| `server_clock_rule` | The rule that converted the file (e.g. "America/New_York+7h", "UTC+0h"); absent on files imported before 2026-10 |
| `user_time_offset_hours` | The UTC offset(s) the rule resolved to for this file — "-2" for MT5 in US winter, "-3" in summer, comma-joined across a season change, "0" for a UTC clock |

### Source Metadata (preserved from JSON)

Original MQL5 metadata is preserved with `source_meta_` prefix:
- Flat scalars: `source_meta_broker_type`, `source_meta_data_format_version`, etc.
- Nested objects stored as JSON strings: `source_meta_symbol_info`, `source_meta_collection_settings`,
  `source_meta_origin`, and the records of one-time repairs to the arrival time
  (`source_meta_collected_msc_restoration`, `source_meta_collected_msc_server_clock_correction`)
  (a file imported before 2026-09-28 also carries `source_meta_error_tracking`, which nothing reads)

### Data Format Version Tracking

`data_format_version` flows from Parquet metadata through the tick index into batch execution reports:

```
Parquet metadata → TickIndexManager (index entry, persisted) → SharedDataPreparator
    → SingleScenario.data_format_versions → PostRunValidator (advisory)
```

**Index**: `TickIndexManager` extracts `data_format_version` from each Parquet file's custom
metadata, stores it per index entry and persists it as a column of the index file. An index written
before the field was persisted has no such column and reads as `'unknown'` — a one-time
`python python/cli/tick_index_cli.py rebuild` populates it.

**Report warning**: exactly one advisory, and it speaks only about the index — when no version is recorded for a file (`'unknown'`):

```
⚠️  Data format version unknown for 186/186 file(s) — the tick index carries no version for them
```

**The version declares a schema; it is not a quality signal.** `DataFormatVersion` is an
operator-set input of the collector, so a collector that starts recording a field without a version
bump is invisible in it — which has happened (the MT5 collector gained `collected_msc` while its
running instance kept declaring `1.1.0`). Any advisory deriving "this data's `collected_msc` is
synthesized" from the version would therefore make false statements on real archives, and none does.

Versions are compared component-wise, never as strings (`'1.10.0' < '1.3.0'` is `True` lexicographically) — see `python/framework/utils/version_utils.py`.

**Coverage report**: `discoveries_cli.py data-coverage show <broker> <symbol>` lists the version as
time **spans**, so the run-report's file count becomes a window — which collector schema produced
which period. The version is a declaration (an operator-set collector input), not a measurement of
how a field was obtained. See [discovery_system.md](../discovery_system.md).

**Data flow**: Uses Channel C (main-process only, no subprocess serialization) — see [architecture_execution_layer.md](../architecture/architecture_execution_layer.md#batch-data-flow-main-process--subprocesses--reports).

---

## CLI Usage

```bash
# Standard import — each broker's server clock (market_config.json) converts its times to UTC
python -m python.cli.data_index_cli import

# Override mode (re-import existing files)
python -m python.cli.data_index_cli import --override
```

The importer names the server clock of every configured broker when a batch starts:
```
Override Mode: DISABLED
Server clock: mt5 → America/New_York+7h
Server clock: kraken_spot → UTC+0h
```

and, in its summary, how many files each resolved offset converted:
```
✅ UTC offsets applied: mt5: -2h × 12 file(s) · -3h × 40 file(s)
```

---

## Tick Parquet Column Normalization

Tick parquet files use broker-native column names that vary by source. The central reader `read_tick_parquet()` normalizes these before any consumer sees the data.

**Module:** `python/framework/data_preparation/tick_parquet_reader.py`

**Normalization rules:**

| Parquet State | Action | Result |
|---------------|--------|--------|
| Has `real_volume`, no `volume` | Rename `real_volume` → `volume` | Standard crypto path (Kraken) |
| Has `volume`, no `real_volume` | No-op | Already normalized |
| Neither column present | Add `volume = 0.0` | Legacy data graceful handling |

**All consumers must use `read_tick_parquet()` instead of `pd.read_parquet()`** for tick files. This ensures a single canonical column contract. Current call sites:

- `SharedDataPreparator` — simulation pipeline tick loading
- `BarImporter` — batch bar rendering from tick files
- `MockTickSource` — AutoTrader mock tick replay

**What it does NOT do:** Drop raw columns, interpret market type, or cache. It is a pure normalization function. Column trimming happens downstream at the transport boundary (`serialize_ticks_for_transport()`), and caching is planned for #21.

---

## Parquet → Simulation Pipeline

After import, tick data flows from Parquet into the simulation engine:

```
Parquet (data/processed/{broker_type}/ticks/{SYMBOL}/)
       ↓
  read_tick_parquet()                        [tick_parquet_reader.py]
  └─ Normalizes columns (real_volume → volume)
       ↓
  SharedDataPreparator
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
  ├─ Inter-tick interval: collected_msc (arrival clock, preferred)
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
| `last`, `tick_volume`, `chart_tick_volume` | Not consumed by tick loop |
| `spread_points`, `spread_pct` | Quality checks only (pre-transport) |
| `tick_flags`, `session` | Import metadata only |

> **Note**: `collected_msc` and `time_msc` are preserved as int64 throughout. The importer applies
> the UTC offset to `time_msc` only — `collected_msc` arrives in UTC already (data format 1.5.0) or
> is converted beforehand by the restoration migration. The simulation reads both from TickData and
> decides which to use for inter-tick intervals.

---

## Duplicate Detection

The importer stores each source file's **name** in the parquet metadata (`source_file`) and, before
writing, searches every collector directory for a parquet claiming the same name. On a match it
raises `ArtificialDuplicateException` — a warning, the file is skipped, the batch continues. Use
`--override` to delete the existing parquet and re-import. A file the structural validation refuses
is refused before this step, so `--override` deletes nothing for it: its old parquet stays — which is
why a re-import is checked by more than count and names (see *Re-importing from the raw archive*).

**It compares NAMES, not content.** This is worth stating plainly because the guarantee is narrower
than it looks: a file renamed between two exports is imported twice, and a file whose name is reused
with different content is refused as a duplicate. What the check actually protects against is the
common case — the same export handed to the importer twice, possibly from a different collector
directory, which is why the search is cross-collector.

The signal importer has no equivalent check: it decides on the presence of the target parquet
(`SignalAlreadyImportedError`, also a warning + skip), because a signal day's file name IS its date
and the projection is per day.

---

## Re-importing from the raw archive

A change to what the importer EXTRACTS from a raw file — a corrected time conversion, a new column —
reaches the data already imported only through a re-import. The raw files are kept unchanged in zip
archives under `data/finished/Archives/`, and
`python/experiments/raw_archive_reimport/raw_archive_reimport.py` prepares a re-import and proves it
complete:

```
plan      which members a scope selects — by the member's own name, never the zip's — the disk it
          needs, and whether extract may start                                  (reads only)
extract   streams the selected members into data/raw/ and writes a manifest beside them
          ... a migration the change needs runs on these extracted copies, never inside a zip
import    python python/cli/data_index_cli.py import --override
verify    count · names · inbox · rewritten — exit code 0 only when all four pass (reads only)
clean     deletes each moved copy that is byte-identical to its archived member; keeps one that
          differs, which then awaits archiving
```

**Count and names are not enough.** The importer refuses a defective file before `--override`
deletes its old parquet, so a refused file keeps its old parquet under the same name, and the
archive and the index still agree file for file. `verify` therefore also checks that no selected file
is left in the inbox and that every selected parquet was written after its member was extracted. The
tool's own docstring has the details; it has no entry point in `python/cli/` on purpose.

---

## Gap Handling

It is standard exchange/broker behavior to not render bars for time periods where no tick updates occurred. The bar renderer follows this principle — gaps result in timestamp jumps, not fill bars.

| Situation | Bar Output | Meaning |
|-----------|-----------|---------|
| Weekend (Sat/Sun) | No bars (time jump) | Expected market closure — Forex only |
| Holiday (Christmas, New Year) | No bars (time jump) | Expected market closure — Forex only |
| Data gap (collector outage) | No bars (time jump) | Data quality problem detected via gap detection |
| Crypto weekend | Normal bars | 24/7 market, no closure |

This behavior is consistent across all three renderers: `VectorizedBarRenderer` (batch import), `BarRenderer` (tick loop / backtesting), and AutoTrader sessions.

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
├── ticks_index.parquet
├── bars_index.parquet
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

## What the archive guarantees about a bar file

A bar parquet can be corrupt in a single column while every cheap check passes. The
footer keeps reporting the right row count, and a projection of one column reads
cleanly — measured on a real case, projecting `timestamp` reported 128 healthy files
while one of them carried an unreadable `low`. Such a file survived a day and two green
test runs before anything noticed.

Two checks exist, and they answer different questions.

**At write time** the renderer reads each bar file back in full immediately after
writing it and compares the row count against what it wrote
(`bar_importer.py::_verify_bar_file`). A file that cannot be read back raises
`BarFileVerificationException`, which fails that symbol's render while the cause is
still known; other symbols continue. The read costs roughly 1 % of a render.

**At index time** every bar file is opened and read completely — the index needs the
tick-count and volume aggregates, so the scan already decodes every column. A file the
scan cannot read is therefore detected, but the consequence used to be invisible: the
row is simply absent from the index, and the failure surfaces much later, somewhere
else, as `Timeframe 'M1' not found`. The build now ends with an explicit balance — how
many of the files scanned could not be read, and which — on error level as its own
block. That makes the index rebuild the archive's integrity check; no separate command is
needed — but pass `--no-caches` when that is all you want, because the rebuild otherwise
also regenerates every discovery cache, which costs far more than the scan:

```bash
python python/cli/bar_index_cli.py rebuild --no-caches
```

Measured 2026-09-16 over 128 bar files: the index rebuild alone **3.4 s**, the discovery
caches on top of it **2 min 15 s**.

**What is NOT guaranteed.** Neither check says anything about whether the bars are
*correct* — only that the file is readable and carries the rows it claims. Nothing
detects a bar file that has silently gone stale against its ticks, and nothing runs on a
schedule: between one render and the next index build, a file damaged by something
outside this pipeline is unnoticed.

**The repair is always the same, because bars are DERIVED** — see
[Data Storage Layout](../architecture/data_storage_layout.md): delete and re-render.
Nothing is lost.

```bash
python python/cli/bar_index_cli.py render --all --clean
```

## Scope of an import

`BarImporter` and both index managers accept a `data_dir`. An import directed at a
scratch directory writes its bars, its tick index and its bar index there and leaves the
real archive alone.

The discovery caches are the one exception, and it is a stated limitation rather than an
oversight: `DiscoveryCacheManager` resolves its own paths through `AppConfigManager` and
builds four sub-caches that each do the same, so it cannot be pointed anywhere. An
import that is scoped elsewhere therefore **skips** the cache rebuild and says so,
instead of rebuilding production caches from bars it never touched. Scoping them belongs
to the index-manager convergence in #175.
