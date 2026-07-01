# Signal Import Tests

**Suite:** `tests/data/signal_import/` · **Mark:** `data` · **Issue:** #429

Validates the signal data source pipeline — JSONL import → columnar parquet → index → projected
reader — and, the key guarantee, **bit-identical parity with the v0 JSONL path** on the consumed
fields.

## What it covers

| Area | Checks |
|---|---|
| Import / explode | row counts (one row per `(collected_msc, symbol)` + one envelope sentinel per envelope), parquet schema + dtypes, provenance kept as JSON columns |
| Index | sources + symbols, whole-file coverage per symbol (incl. symbols absent in some envelopes), range resolution, unknown symbol → empty |
| Reader projection | one snapshot per envelope for the projected symbol; audit-only `sources` dropped from the runtime series |
| v0 parity | `SignalDataProvider` over the raw JSONL vs. over the parquet resolve identically across the range, for a symbol present in every envelope AND one absent in `partial`/`error` envelopes (defensive HOLD) |
| Import guards | mixed `pipeline_id` in one file → `SignalSchemaError` |

## Fixture

`tests/fixtures/signals/signal_import_sample.jsonl` — 6 envelopes (`pipeline_id = test_sentiment`,
symbols BTCUSD + ETHUSD) covering `success`, `partial` (one symbol absent) and `error` (empty
result). The `imported_signals` module fixture imports it into a temp parquet tree + index once.

## Run

```bash
pytest tests/data/signal_import/ -v
```

Related: the SIGNAL worker capability itself is covered by `tests/framework/signal_workers/`
([Signal Worker Tests](../framework/signal_workers_tests.md)); the data source is documented in
[Signal Data Source](../../data_pipeline/signal_data_source.md).
