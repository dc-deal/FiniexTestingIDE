# Signal Data Source — `data_sentiment_type` (Import, Index, Parquet)

First-class scenario data source for **pre-collected signal data** (LLM sentiment and any future
external signal that follows the "collect at data-time, read at backtest-time" pattern). It mirrors
the tick pipeline shape-for-shape: a one-time **import** (JSONL → parquet) builds an **index**, and
scenarios reference the source by a **`data_sentiment_type`** field — the analogue of
`data_broker_type` for ticks.

The SIGNAL worker capability itself (worker type, provider, decision fusion) is the worker guide;
this doc covers only how the *data* is imported, indexed, and resolved.

## Identity + layout

- **`data_sentiment_type` = the archive's `pipeline_id`** (e.g. `crypto_sentiment`,
  `forex_macro_sentiment`). The symbol comes from `scenario.symbol` — exactly like a broker + symbol
  for ticks. One reader (`CORE/llm_sentiment`) consumes many pipelines.
- **Raw JSONL** lives under `data/raw/signals/<pipeline_id>/`; the import writes **parquet + index**
  under `data/processed/signals/<pipeline_id>/`. Paths are configured in
  `configs/import_config.json → signal_paths`.

## Import

```bash
python python/cli/signal_index_cli.py import [--override]   # JSONL → parquet + rebuild index
python python/cli/signal_index_cli.py status                # coverage per source / symbol
python python/cli/signal_index_cli.py rebuild               # force index rebuild
python python/cli/signal_index_cli.py inspect crypto_sentiment BTCUSD
```

The importer (`SignalDataImporter`) explodes each envelope into **one parquet row per
`(collected_msc, symbol)`** plus **one envelope-level sentinel row** (`symbol = '*'`). The sentinel
keeps every envelope's `collected_msc` resolvable for every covered symbol, so a `partial`/`error`
envelope (a symbol is absent) still resolves to a defensive HOLD instead of an earlier snapshot —
matching the JSONL behavior. `collected_msc` is stored as int epoch-ms (the merge key).

The `SignalIndexManager` keys the index as `{data_sentiment_type: {symbol: [files]}}` and resolves
files by range via `get_relevant_files(data_sentiment_type, symbol, start, end)` — the same contract
as `TickIndexManager`. The raw archive may be **rotated into time buckets**
(`<pipeline_id>/<bucket>.jsonl`, e.g. daily `2026-05-03.jsonl`); the importer converts each bucket to
its own parquet and the reader concatenates the buckets that overlap the query range — rotation
changes *where* lines live, not what they mean.

## Scenario usage

```json
"scenarios": [
  {
    "symbol": "BTCUSD",
    "data_broker_type": "kraken_spot",
    "data_sentiment_type": "crypto_sentiment"
  }
]
```

`data_sentiment_type` is **optional** (empty = the scenario has no SIGNAL input). During data-prep,
`SharedDataPreparator` resolves the source via the signal index → reads the parquet through the
projected reader (`load_signal_series_from_parquet`) → the resulting `SignalSeries` is injected as a
`SignalDataProvider` into the SIGNAL worker (the #141 chain, unchanged).

A missing `(data_sentiment_type, symbol)` in the index is a hard error at pre-flight (import it
first, or fix the type) — mirroring the tick "symbol not found in broker index" path.

## Parquet columns — lean projection

The parquet is the **runtime + report layer**, not the archive. It carries only the worker-consumed
fields plus a small set of cheap, dictionary-encoded prompt-provenance scalars:

- **Runtime (worker-consumed):** `signal`, `sentiment_score`, `confidence`, `reasoning`, `urgency`,
  `is_breaking`, `basis` (per-symbol signal quality — `llm` / `no_data` / `degraded`), `status`,
  `schema_version`, plus the `collected_msc` / `symbol` lookup keys. This is `SIGNAL_RUNTIME_COLUMNS`
  — the exact set the reader projects into the subprocess payload.
- **Traceability (envelope-scalar):** `pipeline_id`, `prompt_version`, `prompt_id`, `prompt_hash` —
  so a prompt change stays visible in the data. Read by the index / report path only, not at runtime.

The heavy provenance (`sources`, `metadata`, `errors`) is **deliberately not persisted** — it lives
in the raw JSONL archive, the audit source. Dropping it shrinks the parquet by ~80–85%. The projected
runtime series is bit-identical to the raw-JSONL path on the consumed fields, `basis` included (a
parity test guards this).

## `data_path` override (dev)

A worker config may still carry an explicit `data_path` (raw JSONL) as a development override; it
takes effect only when `data_sentiment_type` is not set on the scenario. The first-class
`data_sentiment_type` is the normal path.

## AutoTrader mock feed — `scenario_settings.data_sentiment_type` (#438)

The AutoTrader mock pipeline consumes the same archives through the **same field a sim scenario
uses** — the profile's `scenario_settings` block, prepared by the shared `MountPreparer`:

```json
"scenario_settings": {
  "data_sentiment_type": "crypto_sentiment",
  "start_date": "2026-04-27T05:26:21+00:00",
  "max_ticks": 20000,
  "balances": { "USD": 10000.0 }
}
```

The feed is resolved via the signal index against the **scenario window** (like the sim), carried
in the data package as a `SignalSeries`, and injected as a `SignalDataProvider` into each SIGNAL
worker (`inject_signal_providers`, the same function the sim subprocess uses). Validation is strict
and fails at startup (§35), never at the first tick:

| Case | Behavior |
|------|----------|
| SIGNAL worker, no `scenario_settings.data_sentiment_type` | Startup abort (no feed for the worker) |
| No index overlap with the scenario window | Startup abort (`SignalDataUnavailableError`) |
| Live tick source with a SIGNAL worker | Startup abort (live sentiment = the #375 event path, not available yet) |

A **deliberate outage** is expressed the sim way — a
`scenario_settings.stress_test_config.stale_data_stress` event carves a window out of the sentiment
series (data-plane), so the worker reports `is_stale` during that window and the decision degrades
(#438; the tick status-plane carve stays sim-only → #444). The session summary tags the feed as
`· 📡 Sentiment: <type>`.

## Scope

Sim (backtesting) pipeline + the AutoTrader **mock** feed above. Real-time/live sentiment
(API/EVENT, push) is a separate follow-up on the event timeline; the shared reader keeps both
worlds on one load path.
