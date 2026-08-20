# Import Pipeline Tests Documentation

## Overview

The import pipeline test suite validates the full tick data import lifecycle: JSON schema validation, Parquet conversion, UTC offset application, metadata preservation, duplicate detection, structural validation, and file management.

**Test Location:** `tests/data/import_pipeline/`
**Config Source:** `configs/import_config.json` (offset registry, paths, processing)
**Total Tests:** 80

---

## Fixtures (conftest.py)

### Helper Functions

| Function | Description |
|----------|-------------|
| `build_minimal_tick_json()` | Builds synthetic MQL5 JSON with configurable symbol, broker_type, tick_count, bid/ask start, custom_ticks, extra_metadata |
| `write_json_fixture()` | Writes JSON dict to a directory as `{filename}` |

### Session Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `import_test_dirs` | session | Creates `source`, `target`, `finished` tmp directories via `tmp_path_factory` |
| `populate_persistent_test_output` | session, autouse | Writes reference Parquets to `data/test/import/processed/` (cleans first, then imports 4 symbols: BTCUSD, ETHUSD, EURUSD, GBPUSD) |

### Persistent Test Output

After each test session, reference Parquets are available in `data/test/import/processed/` (paths from `import_config.json` `test_paths`). The processed directory is cleaned at session start to avoid duplicate detection conflicts. This output:
- Persists for manual inspection (`parquet-tools`, IDE viewers)
- Serves as input for future bar renderer tests
- Contains both broker types: `kraken_spot` (BTCUSD, ETHUSD) and `mt5` (EURUSD, GBPUSD)

### Design Notes

- All test data uses **real broker_types** (`kraken_spot`, `mt5`) because `MarketConfigManager` validates against `configs/market_config.json`
- Unit tests use `tmp_path` for full isolation — persistent output is additional, not a replacement
- `build_minimal_tick_json()` generates realistic tick data with configurable parameters

---

## Test Files

### test_json_schema_validation.py (~10 tests)

Validates that the importer correctly accepts valid JSON and rejects invalid input.

**TestValidJsonAccepted:**
- Minimal valid JSON with required fields processes successfully
- Legacy `data_collector` field accepted as broker_type alias

**TestInvalidJsonRejected:**
- Missing `metadata` key raises ValueError
- Missing `ticks` key raises ValueError
- Empty ticks array is skipped (no crash, 0 processed)
- Missing `broker_type` (and no `data_collector`) raises ValueError
- Unknown broker_type not in market_config raises ValueError

**TestSchemaTypeDefinitions:**
- All TypedDict classes importable from `import_schema_types`
- Mandatory field lists contain expected entries

---

### test_conversion_pipeline.py (~6 tests)

Validates end-to-end conversion from JSON to Parquet.

- Basic conversion creates a Parquet file
- Output contains expected columns (timestamp, bid, ask, last, etc.)
- Tick count in Parquet matches JSON input
- Directory structure follows `{broker_type}/ticks/{SYMBOL}/` pattern
- Numeric columns use optimized dtypes (float32, int32)
- Timestamps parsed as datetime64 with UTC timezone

---

### test_offset_application.py (~10 tests)

Validates UTC offset handling and session recalculation.

**TestOffsetCorrectness:**
- Offset applied when registry has nonzero value for broker_type
- Offset not applied when registry value is 0
- Offset not applied when broker_type not in registry
- Offset direction correct (-3h means subtract 3 hours)

**TestSessionRecalculation:**
- Session recalculated after offset application
- Boundary test: 00:00 GMT+3 → 21:00 UTC maps to correct session
- Session preserved when no offset applied

**TestImportConfigOffsetRegistry:**
- ImportConfigManager returns correct offset for known brokers
- Returns 0 for unknown broker_type

---

### test_parquet_metadata.py (~12 tests)

Validates Parquet file header metadata.

**TestCoreMetadata:**
- `source_file` matches input filename
- `symbol` matches input
- `broker_type` matches input
- `importer_version` matches TickDataImporter.VERSION
- `tick_count` matches actual row count
- `utc_conversion_applied` flag correct based on offset
- `user_time_offset_hours` correct

**TestSourceMetadata:**
- `source_meta_` flat fields present (e.g., `source_meta_broker_type`)
- `source_meta_symbol_info` is valid JSON string
- Parsed nested metadata has correct content
- Already-captured keys (`symbol`, `broker`) not duplicated as `source_meta_`

---

### test_duplicate_detection.py (~5 tests)

Validates hash-based duplicate detection.

- First import succeeds (no duplicate)
- Second import of same file detected as duplicate (0 processed)
- Override mode allows re-import of duplicate
- Different source file not flagged as duplicate

**DUPLICATE AT LAST Policy:** The `populate_persistent_test_output` session fixture imports 4 reference files into `data/test/import/processed/` at session start. All duplicate detection tests use `tmp_path` for full isolation, so they don't conflict with the persistent import. However, the persistent import must complete first — if duplicate detection tests ever run against shared directories, they must be ordered last (after the reference data is established and directories are clean).

---

### test_collected_msc.py (~11 tests)

Tests for the `collected_msc` field (V1.3.0), `time_msc` offset consistency, tick order preservation, and `server_time` removal.

**TestCollectedMscPresence:**
- `collected_msc` column present in Parquet output
- `collected_msc` dtype is int64

**TestCollectedMscBackwardCompat:**
- Old JSON without `collected_msc` imports with default value 0

**TestCollectedMscValues:**
- V1.3.0 data preserves `collected_msc` values through import
- `collected_msc` not affected by time offset (stays unchanged)

**TestTimeMscOffset:**
- `time_msc` shifted by same offset as `timestamp`
- `time_msc` unchanged when no offset applied
- `timestamp` and `time_msc` consistent (same UTC moment) after offset

**TestTickOrderPreservation:**
- Parquet row order matches JSON array order — a burst of three ticks sharing one `time_msc`, distinguishable only by their arrival stamp, with bid values as position markers. Their JSON order *is* the arrival order, so any re-sort would lose it
- `collected_msc` stays non-decreasing through the import

**TestServerTimeRemoved:**
- New imports do not contain `server_time` column

---

### test_quality_checks.py (~6 tests)

Validates rejection behaviour and file management.

**TestImportRejection:**
- Ticks with bid <= 0 reject the file — nothing written, error recorded, batch survives
- A wide spread is a market condition, not a defect — the file still imports
- No temporary helper columns leak into the final Parquet

**TestMoveProcessedFiles:**
- With `move_processed_files=True`, JSON moved to finished directory
- With `move_processed_files=False`, JSON remains in source directory

---

### test_import_validation.py (~19 tests)

Unit tests for `TickImportValidator` — one case per rejection reason plus the cross-file plane. The
constants it enforces are measured values; each test names the measurement it rests on.

**TestHealthyFile:**
- A file satisfying every invariant passes untouched
- Ticks sharing an arrival millisecond are allowed (Kraken bursts several per ms)

**TestRejectionReasons:**
- Backwards `collected_msc` step
- Timezone-offset `collected_msc` (class A) — message names the migration
- Same defect in a file declaring `collected_msc_timebase: "utc"` — message names a collector defect instead
- `2^64`-scale anchor overflow (class C) — split into two segments
- Row count disagreeing with `summary.total_ticks`
- Inverted spread (`ask < bid`)
- `timestamp` and `time_msc` describing different moments
- Empty tick array

**TestTolerances:**
- Lag exactly at the ±30 s window edge is accepted, one millisecond past it is not
- A weekend-sized gap (48 h) is not a segment break
- A gap past the 7-day threshold is

**TestMissingColumns:**
- Missing or all-zero `collected_msc` warns, never rejects (pre-V1.3.0 data has no arrival clock)

**TestArchiveOrdering:**
- An ordered archive produces no findings
- Overlapping coverage of one symbol is reported
- Files touching at exactly 0 ms distance are not an overlap (227 such pairs exist in the archive)

---

### test_tick_index_persistence.py (~4 tests)

Validates that `data_format_version` survives the tick index write/read cycle — the index is the only path by which the version reaches a run report.

**TestVersionRoundTrip:**
- Persisted index file carries the `data_format_version` column
- A manager loading the index sees the real version, not `'unknown'`
- The empty-schema branch declares the same columns as a populated index

**TestLegacyIndexTolerance:**
- An index file without the version column still loads (entries intact) and reads as `'unknown'` — never an exception that silently empties the index

---

## Architecture Notes

- Tests are **fully isolated** — each test creates temporary directories, no shared state
- Uses `TickDataImporter` directly (not via CLI) for precise control
- `auto_render_bars=False` in most tests to skip bar rendering overhead
- Synthetic data covers both `kraken_spot` (offset 0) and `mt5` (offset -3) broker types
- No production data required — all test data generated by `build_minimal_tick_json()`
- **DUPLICATE AT LAST** — persistent session fixture imports first; duplicate detection tests use `tmp_path` isolation but must conceptually run after reference data is established
