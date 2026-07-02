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
as `TickIndexManager`.

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

## Projection — ship only consumed fields

The runtime reader loads only the worker-consumed columns (`signal`, `sentiment_score`,
`confidence`, `reasoning`, `urgency`, `is_breaking`, plus the lookup keys). The audit-only
provenance columns (`sources`, `metadata`, `errors`) and envelope metadata stay in the parquet for a
reporting / archive path but are **not** loaded into the subprocess payload — the columnar store
gives this projection for free. The projected runtime series is bit-identical to the raw-JSONL path
on the consumed fields (a parity test guards this).

## `data_path` override (dev)

A worker config may still carry an explicit `data_path` (raw JSONL) as a development override; it
takes effect only when `data_sentiment_type` is not set on the scenario. The first-class
`data_sentiment_type` is the normal path.

## AutoTrader mock feed — `sentiment_source` (profile block)

The AutoTrader mock pipeline consumes the same archives through a profile block that mirrors
`tick_source`:

```json
"sentiment_source": {
  "type": "mock",
  "data_sentiment_type": "crypto_sentiment"
}
```

At startup (`setup_sentiment_feed`, mirror of the sim's provider injection) the feed is resolved
against the **mock tick parquet's time range**, read through the same projected reader, and injected
as a `SignalDataProvider` into each SIGNAL worker. Validation is strict and fails at startup, never
at the first tick:

| Case | Behavior |
|------|----------|
| SIGNAL worker, no `sentiment_source` | Startup abort (clear config error) |
| `sentiment_source`, no SIGNAL worker | Warning (dead config), feed skipped |
| `type` other than `mock` | Startup abort (live sentiment = future event path) |
| `tick_source.type` not `mock` | Startup abort (recorded sentiment vs. live ticks is meaningless) |
| No index overlap with the tick window | Startup abort (`SignalDataUnavailableError`) |

`parquet_path` (a processed signal parquet) is the explicit override — used e.g. for **deliberate
outage tests**: a tick file entirely after the archive end resolves only the aged last snapshot, so
the worker reports `is_stale` for the whole session and the decision degrades (the index path would
correctly reject that window as non-overlapping). The session summary tags the feed as
`· 📡 Sentiment: <type>`.

## Scope

Sim (backtesting) pipeline + the AutoTrader **mock** feed above. Real-time/live sentiment
(API/EVENT, push) is a separate follow-up on the event timeline; the shared reader keeps both
worlds on one load path.
