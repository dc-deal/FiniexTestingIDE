# Import Pipeline Tests Documentation

## Overview

The import pipeline test suite validates the full tick data import lifecycle: JSON schema validation, Parquet conversion, UTC offset application, metadata preservation, duplicate detection, quality checks, and file management.

**Test Location:** `tests/import_pipeline/`
**Config Source:** `configs/import_config.json` (offset registry, paths, processing)
**Total Tests:** 46

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

### test_quality_checks.py (~6 tests)

Validates quality validation and file management.

**TestQualityChecks:**
- Ticks with bid <= 0 trigger warning but don't crash import
- Ticks with spread_pct > 5.0 trigger warning but don't crash
- Temporary column `bid_pct_change` removed from final Parquet

**TestMoveProcessedFiles:**
- With `move_processed_files=True`, JSON moved to finished directory
- With `move_processed_files=False`, JSON remains in source directory

---

## Architecture Notes

- Tests are **fully isolated** — each test creates temporary directories, no shared state
- Uses `TickDataImporter` directly (not via CLI) for precise control
- `auto_render_bars=False` in most tests to skip bar rendering overhead
- Synthetic data covers both `kraken_spot` (offset 0) and `mt5` (offset -3) broker types
- No production data required — all test data generated by `build_minimal_tick_json()`
- **DUPLICATE AT LAST** — persistent session fixture imports first; duplicate detection tests use `tmp_path` isolation but must conceptually run after reference data is established
