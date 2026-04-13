# Tick Parquet Reader Tests

## Purpose

Verifies that `read_tick_parquet()` correctly normalizes broker-native column names to the framework's canonical schema. The central reader is the single entry point for all tick parquet loading — column normalization bugs here propagate to every downstream consumer.

## Why This Matters

Tick parquet files from different brokers use different column names:

| Broker | Parquet Column | Framework Column |
|--------|---------------|-----------------|
| Kraken (crypto) | `real_volume` | `volume` |
| MT5 (forex CFD) | `real_volume` (= 0.0) | `volume` |
| Legacy / pre-normalized | neither | `volume` (= 0.0) |

Before this reader existed, three independent consumers each had their own `real_volume` → `volume` mapping. When one was missed, OBV and other volume-dependent workers silently received null values.

## What Is Tested

### Unit Tests (`TestColumnNormalization`)

| Test | Description |
|------|-------------|
| `test_crypto_real_volume_normalized` | `real_volume` renamed to `volume` with correct values |
| `test_forex_zero_volume` | `real_volume=0.0` becomes `volume=0.0` |
| `test_legacy_no_volume_column` | Neither column present: `volume=0.0` added |
| `test_already_normalized_passthrough` | Existing `volume` column passes through unchanged |
| `test_raw_columns_preserved` | bid, ask, tick_volume, tick_flags, time_msc survive normalization |

### Integration Test (`TestVolumeChain`)

| Test | Description |
|------|-------------|
| `test_volume_chain_parquet_to_bar` | Full path: parquet with `real_volume` → `read_tick_parquet()` → `VectorizedBarRenderer` → `bar.volume > 0` |

The integration test exercises the exact bug path that caused OBV null output: volume must survive from raw parquet through normalization into rendered bars.

## Test Data

All tests use **synthetic parquet files** generated via `tmp_path` fixtures (no external data dependencies). Four fixtures cover the normalization matrix:

- `crypto_parquet` — Kraken-style with `real_volume` > 0
- `forex_parquet` — MT5 CFD-style with `real_volume` = 0.0
- `legacy_parquet` — No volume columns at all
- `already_normalized_parquet` — Pre-normalized with `volume` column

## Files

- `tests/framework/tick_parquet_reader/test_tick_parquet_reader.py` — Test suite
- `python/framework/data_preparation/tick_parquet_reader.py` — Module under test

## Running

```bash
pytest tests/framework/tick_parquet_reader/ -v --tb=short
```

VS Code: **"Pytest: Tick Parquet Reader (All)"** launch configuration.
