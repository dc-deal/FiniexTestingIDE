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
files by range via `get_relevant_files(data_sentiment_type, symbol, start, end)`. The raw archive may
be **rotated into time buckets** (`<pipeline_id>/<bucket>.jsonl`, e.g. daily `2026-05-03.jsonl`); the
importer converts each bucket to its own parquet and the reader concatenates the buckets that
overlap the query range — rotation changes *where* lines live, not what they mean.

Unlike `TickIndexManager`, the signal resolution returns one bucket **beyond** the overlap where
needed — see *Resolution contract* below. Ticks are consumed as they arrive; a signal is resolved
backwards from the tick, so the two contracts differ by design.

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

## Resolution contract — the first tick is never blind

A SIGNAL worker resolves `nearest(tick)`: the newest snapshot **at or before** the tick. The
consequence is a rule the file selection must honour, not an optimization:

> **At the window's first tick a snapshot must already exist.** If none does, the worker resolves
> a gap — empty result, `is_stale=True` — and the mandatory `on_signal_stale` hook fires
> immediately.

This mirrors the live contract: at startup the AutoTrader pulls the producer's **last known**
signal, regardless of how long ago it was determined. The backtest must behave the same way.

`SignalIndexManager.get_relevant_files` therefore returns the buckets overlapping
`[start, end]` **plus the preceding bucket** whenever no overlapping one begins at or before
`start`. With daily buckets and a producer that stamps *after* the bar close, that is the normal
case for a window opening at a day boundary:

```
window start        2026-08-11 00:00:00
last snapshot       2026-08-10 23:50:29   ← lives in the 08-10 bucket
first own snapshot  2026-08-11 00:00:31   ← 31s AFTER the first tick
```

Without the preceding bucket the run starts blind until its own day's first snapshot — seconds
normally, but bounded only by that first snapshot: after a producer restart (e.g. the archive's
2026-08-10, whose first entry is 13:20) it would be hours. The reader's `start` trim keeps exactly
the one pre-start snapshot and drops the rest of that bucket, so the extra file costs one
projected read and no runtime memory.

**When extending this:** any new consumer of a signal archive resolves through
`get_relevant_files` — do not re-implement window selection against the index entries directly, or
the pre-start snapshot is lost again. Tests: `tests/data/signal_import/` (`two_bucket_index`).

## Cadence — what the series actually looks like

The producer runs on a bar-close grid (M10 for the current sources), but the series is **not** a
fixed interval. Three things make a distance shorter or longer than the nominal one, all of them
legitimate:

| | Effect on the timeline |
|---|---|
| **Processing time** | a pass takes seconds to complete, so a snapshot lands *after* its bar close (measured: ~97% within 60s). `:00:23` is normal, not late. |
| **Producer restart** | the engine emits an initial-state snapshot immediately on restart, off-grid and *in addition to* the regular one |
| **Breaking events** | an urgent story jumps the eval queue instead of waiting for the next bar close — deliberately off-grid, and the interesting case |

Consequences for any consumer:

- **Never assume 600s spacing, and never key a record on a rounded timestamp.** Bucket by nearest
  bar close if a grid is needed; the raw `collected_msc` is the identity.
- **Extra snapshots are never a defect.** They shorten a distance, so gap detection (which only
  reacts to distances that grow) is unaffected by both restarts and breaking wakes.
- **Duplicates per bar are expected**, not a bug — a restart snapshot and the regular one can land
  in the same bar. The reader keeps one row per `collected_msc`; both survive as distinct snapshots.
- **Resolution is tick-gated today.** The worker resolves `nearest(tick)`, so a breaking snapshot
  arriving between two ticks is first seen at the *next* tick. Firing a signal event at its own
  timestamp — and more than once per bar where the data says so — is what #375's ordered event
  timeline changes.

## Coverage + gaps

An archive is rarely continuous — a producer outage leaves a hole, and the index range says
nothing about what is inside it. `SignalCoverageReport` walks the snapshot timeline and
classifies the holes, mirroring the tick-side `DataCoverageReport`:

```bash
python python/cli/discoveries_cli.py signal-coverage validate
python python/cli/discoveries_cli.py signal-coverage show crypto_sentiment BTCUSD
```

The reports run in the batch's Phase 1 and feed `ScenarioDataValidator`:

- a scenario whose window **closes before the source begins** is an error — no snapshot can ever
  resolve (the typical cause: a scenario left on an old window after the source was re-imported).
- a scenario whose window opens **before the first snapshot** gets a warning — every tick until
  the first snapshot resolves to a gap (empty result, `is_stale=True`). This is the signal
  analogue of warmup: not "N entries of history", but "a snapshot must exist at the first tick".
  There is no warmup window for signals — a SIGNAL worker resolves `nearest(tick)` and nothing more.
- a scenario whose window opens **inside a hole** gets a warning naming the age of the snapshot
  the run will start on.
- a hole **inside the loaded tick stretch** whose category is not in
  `data_validation.allowed_gap_categories` (`app_config.json`, shared with the tick check) is an
  error — the scenario is excluded, the batch continues (§33).
- a window reaching **past the last snapshot** is NOT flagged: that is the contracted staleness
  degradation (#434), and `sentiment_forex_demo` relies on it deliberately.

Signal gaps carry their own thresholds (`discoveries_config.json` → `signal_coverage.thresholds`):
short < 30min, moderate < 1h, large above. Weekends are never an expected closure here — the
producing engine runs 24/7 regardless of the traded market, so a weekend hole is a real outage.
Full detail: [Discovery System](../discovery_system.md).

## Parquet columns — lean projection

The parquet is the **runtime + report layer**, not the archive. It carries only the worker-consumed
fields plus a small set of cheap, dictionary-encoded prompt-provenance scalars:

- **Runtime (worker-consumed):** `signal`, `sentiment_score`, `confidence`, `reasoning`, `urgency`,
  `is_breaking`, `basis` (per-symbol signal quality — `llm` / `no_data` / `degraded`), `status`,
  `schema_version`, plus the `collected_msc` / `symbol` lookup keys. This is `SIGNAL_RUNTIME_COLUMNS`
  — the exact set the reader projects into the subprocess payload.
- **Traceability (envelope-scalar):** `pipeline_id`, `prompt_version`, `prompt_id`, `prompt_hash`,
  `data_origin`, `config_fingerprint`, `trigger_reason` — so a prompt change, the data's nature, a
  producer-config change and the cause of each pass stay visible in the data. Read by the index /
  report path only, not at runtime. What each one answers: see *Envelope fields* below.
  `trigger_reason` is the single field taken out of `metadata`; the rest of that block stays
  archive-only.

The heavy provenance (`sources`, `metadata`, `errors`) is **deliberately not persisted** — it lives
in the raw JSONL archive, the audit source. Dropping it shrinks the parquet by ~80–85%. The projected
runtime series is bit-identical to the raw-JSONL path on the consumed fields, `basis` included (a
parity test guards this).

## Envelope fields — what each one actually means

Several fields read as self-explanatory and are not. This is the reference; the traps below were
each found the hard way against the real archive.

### Time

| Field | Meaning |
|---|---|
| `collected_msc` | **The merge key.** Epoch-ms, normalized to a UTC datetime on read. A worker resolves the nearest snapshot with `collected_msc <= tick` — no look-ahead by construction. |
| `timestamp` | The producer's analysis wall-clock. **Not persisted, never read by us.** Present in the raw JSONL only. Do not reason about look-ahead from it — `collected_msc` is the only key that decides anything. |

The producer stamps *after* the bar close, so a snapshot lands 3–60s past its M10 boundary
(measured median ~17s, tail to ~580s). See *Cadence* above for why the series is not a fixed
interval.

### Quality — `status` and `basis`

These two are the most misread fields in the archive.

| `status` | Meaning |
|---|---|
| `success` | the pass ran and every configured source was reached |
| `partial` | results were produced, but something degraded — **most often one quarantined feed** |
| `error` | nothing could be produced; `result` is empty |

> **`status` is not a quality verdict.** A `partial` envelope's signals can be perfectly sound —
> the flag says "one of seven feeds was unreachable", not "this analysis is wrong". Filtering on
> `status == 'success'` silently drops a whole pipeline for as long as one feed is quarantined
> (up to 24h). Read `metadata.sources_reached` / `sources_configured` in the raw archive if the
> degree of degradation matters.

| `basis` (per symbol) | Meaning | Share in the real archive |
|---|---|---|
| `llm` | a real LLM call on retrieved context | ~94 % |
| `no_data` | retrieval came back empty → **mechanical HOLD**, no LLM call, no cost | ~4 % |
| `degraded` | the output guard or the budget breaker rewrote it | ~2 % |

> **`basis` is the field that says what actually happened — read it before trusting a HOLD.**
> Roughly 11 % of all HOLDs are mechanical, not opinions, and they cluster exactly where it hurts:
> thin-corpus days and outage windows. A backtest that treats them as a neutral market view reads
> an outage as a signal. Split on `basis` before aggregating.

### Signal content

| Field | Meaning |
|---|---|
| `signal` | `BUY` / `SELL` / `HOLD` — the verdict for this symbol at this snapshot |
| `sentiment_score` | −1.0 … +1.0, the news tone |
| `confidence` | 0.0 … 1.0. A `no_data` HOLD carries `0.0` by contract |
| `urgency` | 0.0 … 1.0, how time-critical the producer judged the story |
| `is_breaking` | an urgent story drove this symbol's signal. **A content flag, not a scheduling marker** — a normal grid pass carries it too. Real rate: ~6 % of result rows (crypto), ~3 % (forex) |
| `reasoning` | the producer's one-line justification. Human-readable only; nothing keys on it |

### Provenance

| Field | Answers | Empty means |
|---|---|---|
| `pipeline_id` | which producer stream | — (always set; it is the source identity) |
| `prompt_id` / `prompt_version` | which prompt template | — |
| `prompt_hash` | *has the prompt text changed?* | — |
| `data_origin` | *is this real or generated?* | **unknown**, never "real" |
| `config_fingerprint` | *has the producer's input config changed?* | **unknown**, never "unchanged" |
| `trigger_reason` | *why did this pass run?* | **unknown**, never "scheduled" |

**`data_origin`** (`synthetic` / `live`) is the mock-versus-real discriminator. Without it a
generated archive and a real one are identical in every other field — the generator mirrors the
prompt identity on purpose, so `prompt_hash` cannot tell them apart. A scenario binding a
`synthetic` source gets a pre-run warning (see [Discovery System](../discovery_system.md)).

**`config_fingerprint`** is a hash over the producer's *effective* input configuration — the feed
set, weights, retrieval thresholds, model. A feed added, disabled or re-weighted shifts the score
distribution while every other provenance field stays byte-identical, so this is the only field
that catches it. Two archive stretches are one comparable series when **`prompt_hash` and
`config_fingerprint` both agree**.

Its practical use is not bookkeeping: it is a **validity condition for multi-window experiments**.
In-sample/out-of-sample validation and parameter sweeps assume that window A and window B differ
only in market conditions. If the fingerprint moved between them, a performance drop in B may be
the data source changing rather than the strategy overfitting — and without the field those two
are indistinguishable.

**`trigger_reason`** says *why the producing pass ran*. Closed vocabulary today — `scheduled` (the
bar-close grid), `boot` (first pass after an engine restart), `breaking` (an out-of-band wake that
jumped the eval queue), `manual` (operator at the console), `external` (an API caller). One value
per envelope; every symbol of that pass shares it.

Two rules the producer states and we honour:

- **`boot` beats `scheduled`.** A restart that happens to land on a bar close still reads `boot` —
  the field names why the pass ran *now*.
- **An unrecognized value is "other", never an error.** The vocabulary is closed on the engine side
  but the field is a plain string, so a future engine version can add one without breaking our
  reader.

Its use here is to **replace a heuristic with a fact**. Before it, the only way to tell a grid pass
from an off-grid one was "distance to predecessor < 300s" — which misclassifies whenever a
scheduled pass runs long (at a 580s pass duration the next scheduled envelope is ~36s behind and
looks off-grid). Filtering on `scheduled` yields the clean single-cadence series; the rest are real
analyses, just not points of that grid.

`trigger_reason` is the **one field lifted out of the envelope's `metadata`**, which is otherwise
archive-only (see below). The exception is deliberate and rests on the same criterion the rule
does: it is a short, dictionary-encoded scalar of the same weight class as the provenance columns —
not part of the heavy `sources` / `stage_timings` payload.

> **Absence is not "unchanged".** Archives collected before the producer stamped these fields carry
> no column at all; the reader projects each only where the schema has it, and an empty value reads
> as *unknown*. Backfilling a constant would be a false claim of comparability — the real archive
> demonstrably changed its symbol set on 2026-07-24, so a single value across it would be wrong.
> For `trigger_reason` the same rule bites harder: mapping a missing value to `scheduled` would
> silently fold restart and wake passes into the bar-close series.

*Status (2026-08-16): `data_origin` is present in all four sources. `config_fingerprint` and
`trigger_reason` are stamped in the two **mock** sources; the live engine ships them after its next
restart, so the real archives currently read `unknown` for both. The import path is in place, so a
newly exported day carries them without a code change.*

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
