# Tick-Route Parity Tests

The bar parity tests hand both pipelines one synthetic tick list, so they compare pipeline against
pipeline over identical input. That leaves out the one place the two runs differ by design: where
their ticks come from. A live-adapter session reads Kraken's WebSocket through the message parser; a
backtest reads the collector's file through the importer, the tick reader and the process transport.
A field one route fills and the other does not reaches a strategy as a difference no bar-parity
test can see. This suite sends the same events down both routes and compares what arrives.

It does not test the collector itself: the archive side is built in the collector's format from the
recorded events, which is this project's model of what the collector writes.

## What Is Tested

| File | Test | What it pins |
|------|------|------|
| `tests/parity/test_tick_parity_kraken_routes.py` | `test_every_trade_arrives_on_both_routes` | one tick per recorded trade on each route |
| | `test_prices_quotes_and_volumes_agree` | `time_msc`, `bid`, `ask`, `last`, `price`, `mid` and `volume` equal, tick by tick |
| | `test_a_trade_before_the_first_quote_is_flat_on_both_routes` | a trade before any quote reads bid = ask = last = price on both routes |
| | `test_a_trade_after_a_quote_carries_it_on_both_routes` | a later trade carries the quote standing at it, and its price is the traded one |
| | `test_the_event_time_agrees_to_the_millisecond` | the event time agrees to the millisecond (see below) |
| | `test_the_m1_bars_are_identical` | each pipeline renders the ticks of its own route, and the M1 bar histories are identical |

## Fixture Design

**Real frames.** `tests/fixtures/tick_sources/kraken_ws_frames.json` holds Kraken v2 frames recorded
verbatim on 2026-09-16: a ticker snapshot and update, single trades and a burst. They are fed in
their recorded order — a trade before any quote first — and replayed at four minute offsets with
the prices moved by a few dollars a minute and the trade ids moved on, so there are completed M1
bars to compare.

**Live route.** The frames through `KrakenTickMessageParser.parse_message`.

**Archive route.** The same events as the collector's format 1.7.0 writes them: one row per trade,
the quote standing at it as bid and ask, the trade price as `last`, the trade id, the quote's age —
and a trade before the first quote as a flat row (bid = ask = last, no quote age). The file goes
through `TickDataImporter` into a temporary folder, then `read_tick_parquet`,
`serialize_ticks_for_transport` and `process_deserialize_ticks_batch`. A format 1.7.0 file must
state its origin and keep its time stamps non-decreasing, or the importer refuses it whole.

**Left out of the comparison:** `collected_msc`. Live it is this machine's clock at receipt, in the
archive the collector's; the two never agree and are not meant to.

## One Known Difference: Below a Millisecond

The live parser keeps the venue's time to the microsecond in `timestamp` and derives `time_msc` from
it; the archive stores milliseconds and rebuilds `timestamp` from them. So the two `timestamp`
values differ by less than one millisecond, and only there: `time_msc` is equal on every tick, and
the bars agree.

## How to Run

```bash
pytest tests/parity/test_tick_parity_kraken_routes.py -v
```

VS Code: **"🧩 Pytest: Parity (All)"** runs it with the rest of `tests/parity/`.

## Shared Helpers

`tests/shared/parity_runners.py` (both pipelines over a tick list), `tests/shared/parity_comparators.py`
(`assert_bars_equal`) and the collector-file builder `build_minimal_tick_json` in
`tests/data/import_pipeline/conftest.py`.
