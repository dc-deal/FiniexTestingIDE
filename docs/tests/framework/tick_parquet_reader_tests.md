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
| `test_a_projected_read_derives_the_same_price_and_volume` | a read of only some columns holds less and derives the same timestamp, time_msc, price and volume |

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

`TickData.price` resolves a venue's price basis from the data itself — the traded price where there
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

### A Tick-Count Window Reads What It Needs (`test_max_ticks_window.py`)

A scenario bounded by `max_ticks` instead of `end_date` — every AutoTrader mock profile is one —
read every tick file from its start to the archive's end, sorted them all and kept `max_ticks`:
measured 2026-10-12, 362 files and a 3.5 s sort per mock session for 15,000 ticks used, growing
with every import. Over a synthetic archive of daily files, one of them overlapping another and
repeating one of its stamps exactly:

| Test | What it pins |
|---|---|
| `test_it_stops_where_the_index_counts_reach_max_ticks` | only the files the index's tick counts say are needed are opened; a file straddling the start is read but not counted |
| `test_an_archive_too_short_is_read_to_its_end` | asking for more ticks than the archive holds reads every file after the start |
| `test_from_inside_a_file` | the ticks equal the first `max_ticks` of reading the whole archive |
| `test_across_the_overlap_and_its_repeated_stamp` | the same holds where files overlap and two ticks share a stamp — a tie is broken as it is when everything is read |

### A Price No Bar May Be Built From Is Refused (`test_unusable_tick_price.py`)

The reader derives every tick's price once, and a bar renders it as it stands. A price that is not
above zero would reach the bars silently, and so would a midpoint of a quote with a side at zero:
half the other side is a positive number and still no price. `refuse_unusable_price`
(`tick_price_check.py`) refuses such a
file with `TickPriceUnusableError` where the price is consumed: the bar import fails the symbol, and
the backtest refuses every scenario of that symbol and prepares the others. The reader itself
refuses nothing.

| Test | What it pins |
|---|---|
| `test_a_traded_price_passes` | an order-driven file reads its traded prices |
| `test_a_quote_driven_file_reads_its_midpoint` | a `last` of 0.0 on every row (a quote-driven venue) is no traded price, and the midpoint passes |
| `test_a_flat_file_without_the_column_passes` | a file before collector format 1.6.0 — bid = ask = the trade, no `last` — passes |
| `test_a_traded_tick_is_not_judged_by_its_quote` | where the price is what traded, the quote is not what a bar is built from |
| `test_a_price_of_zero_is_refused` | a tick with neither a traded price nor a quote is refused, and the message counts it |
| `test_a_midpoint_of_a_one_sided_quote_is_refused` | a fallback row with one side at zero is refused although its midpoint is positive |
| `test_a_price_that_is_not_a_number_is_refused` | a midpoint that is not a number is refused |
| `test_the_reader_itself_refuses_nothing` | a caller that reads the quote as data gets a tick without a bid as it is — the divergence report skips such a tick itself |
| `test_the_message_names_the_file_the_first_tick_and_an_absent_last` | the message names the file, the count and the first tick's time, and an absent traded price reads "no last", never 0.0 |
| `test_an_unusable_file_refuses_its_symbol_and_the_other_runs` | in `prepare_ticks` the refusal reaches every scenario of the symbol, with the file named, and another symbol is prepared |
| `test_a_mount_without_any_traded_price_still_ends_the_batch` | a mount without any traded price is not refused per symbol: the import refuses such a file, so it means the pipeline lost the column, and that fault still ends the batch |
| `test_the_refused_scenario_carries_its_validation_error` | the scenario receives a validation error with the check `tick_data_unusable`, and no package |
| `test_an_unusable_file_stops_the_symbols_load` | the bar import's load of a symbol stops at the refused file and names it — the render worker's existing catch then reports the symbol as failed, which this test does not run |

## Files

- `tests/framework/tick_parquet_reader/test_tick_parquet_reader.py` — Test suite
- `tests/framework/tick_parquet_reader/test_max_ticks_window.py` — the `max_ticks` read bound
- `tests/framework/tick_parquet_reader/test_unusable_tick_price.py` — the refusal of an unusable price
- `python/framework/data_preparation/tick_price_check.py` — the check where the price is consumed
- `python/framework/data_preparation/tick_parquet_reader.py` — Module under test

## Running the Tests

```bash
pytest tests/framework/tick_parquet_reader/ -v --tb=short
```

VS Code: **"Pytest: Tick Parquet Reader (All)"** launch configuration.
