# Tick Parquet Reader Tests

## Purpose

Verifies that `read_tick_parquet()` correctly normalizes broker-native column names to the
framework's canonical schema. The central reader is the single entry point for all tick parquet
loading — column normalization bugs here propagate to every downstream consumer.

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

### Traded Price Across Its Boundaries (`test_traded_price_transport.py`)

`TickData.price` resolves a venue's basis from the data itself — the traded price where there
is one, the midpoint where there is not. That rests on a single invariant: **an absent traded
price arrives as `None`, never as `0.0`.** A zero is a price to everything downstream and
would render an entire quote-driven archive at zero, since `dropna` drops NaN and not zeros.

Two boundaries carry a tick and both are covered:

- **The pickle transport** between the main process and a scenario subprocess
  (`TickTransportColumn`). Every other optional column defaults to `0.0` on unpack, so the
  traded price is the deliberate exception — a zero must come back as `None`.
- **`TickData.to_dict()`**, which feeds the coordinator tick log and two executor forensics
  records. It carries `mid`, `last` and `price` together: `mid` is what a valuation used and
  `price` what a strategy saw, and a record with only one cannot tell them apart afterwards.

## Files

- `tests/framework/tick_parquet_reader/test_tick_parquet_reader.py` — Test suite
- `python/framework/data_preparation/tick_parquet_reader.py` — Module under test

## Running the Tests

```bash
pytest tests/framework/tick_parquet_reader/ -v --tb=short
```

VS Code: **"Pytest: Tick Parquet Reader (All)"** launch configuration.
