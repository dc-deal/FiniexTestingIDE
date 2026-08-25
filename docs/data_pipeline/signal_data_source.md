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
- **Source ≠ kind.** The **source** is that `pipeline_id` — *where* the data comes from. The
  **kind** is what a worker declares via `CONSUMED_SIGNAL_KIND` (e.g. `llm_sentiment`) — *what
  shape* of payload it reads, and the slot the preparation layer hands the series to. The two are
  orthogonal: one kind is read from many sources. Only `pipeline_id` is the identity shared by
  archive, index, coverage report and run report, so it is the leading id everywhere; the word
  "source" is reserved for it.
- **Raw JSONL** arrives under `data/raw/signals/<pipeline_id>/`; the import writes **parquet + index**
  under `data/processed/signals/<pipeline_id>/` and archives the consumed JSONL to
  `data/finished/signals/<pipeline_id>/`. Paths are configured in
  `configs/import_config.json → signal_paths`.

## Import

```bash
python python/cli/signal_index_cli.py import [--override]   # JSONL → parquet + rebuild index
python python/cli/signal_index_cli.py status                # coverage per source / symbol
python python/cli/signal_index_cli.py rebuild               # force index rebuild
python python/cli/signal_index_cli.py inspect crypto_sentiment BTCUSD
python python/cli/signal_index_cli.py connect-check              # is the producer reachable, and which one
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

### Raw is an inbox, finished is the archive

A successfully imported JSONL moves to `data/finished/signals/`, keeping its directory structure —
the same convention the tick import follows, switched by the shared
`import_config.json → processing.move_processed_files`. The raw directory therefore holds only what
still needs importing, and a re-run costs nothing instead of failing on every file that already has
a parquet.

The move preserves the path **relative to the directory the file was found in**, rather than
rebuilding it from the resolved `pipeline_id`. A file sitting in a folder that does not match its
own `pipeline_id` is an anomaly worth seeing; normalizing it during the move would repair it
silently. A file whose import failed is not moved.

A pipeline folder left empty by the move is removed, so an emptied inbox looks empty instead of
occupied. Removal is by `rmdir`, which refuses a non-empty directory — a folder still holding
anything (an unimported file, a failed import, a stray note) survives by construction. The inbox
root itself is kept.

`--override` reads the finished archive **in addition to** the raw inbox. Overriding means
rebuilding what is already imported, and that is where those files live — without this, the flag
would quietly do nothing once the inbox is empty. When the same relative path exists in both, the
raw copy wins: a re-exported day supersedes its archived copy, and the move then replaces it.

> **The archive is not a by-product.** The parquet is a lean projection; an envelope's `sources`,
> `metadata` and `errors` survive nowhere else. The archived JSONL is the audit source and must
> stay readable — this is a move, never a delete.

Once a batch is archived it is typically packed into `data/finished/Archives/signals-<YY-MM-DD>.zip`,
the same convention the tick archives follow, and the loose folder is removed. The zip keeps the
`signals/<pipeline_id>/<bucket>.jsonl` structure, so a day stays addressable inside it.

Tooling that rewrites raw envelopes — `python/experiments/restore_signal_envelope_field.py` — works
on plain files, so an archived day must be unpacked first, stamped, and re-imported with
`--override`. Read this as a cost, not an obstacle: it is the reason to stamp a day **before** it
is packed, while it still sits in the inbox.


### Producer endpoints — switching environments is one word

`sentiment_config.json` registers the reachable producer instances and names the active one:

```json
"producer": {
  "active": "dev",
  "endpoints": {
    "dev":        { "base_url": "http://host.docker.internal:8100",
                    "credentials_file": "rag_credentials_dev.json" },
    "production": { "base_url": "https://finiex-rag.duckdns.org",
                    "credentials_file": "rag_credentials.json" }
  }
}
```

Switching is one word in `user_configs/sentiment_config.json`:
`{"producer": {"active": "production"}}`.

**The address and the credential belong to the endpoint, not to the transport, and they switch
together.** That pairing is the point: a production token against a development address answers
`401`, and a `401` stops the poll loop — so a switch that moved only the address would present as
a feed outage diagnosed at the wrong system. It also removes a second hazard: `poll` and `stream`
used to carry an address each, so they could name different producers, and the identity probe
would then answer about an engine that is not the one delivering.

An `active` naming an unregistered endpoint is a **hard error** listing the known names, never a
fallback to the previous one. The session log states which endpoint answered, next to the
credential's source file:

```
📡 Producer endpoint ← production (https://finiex-rag.duckdns.org) · credential user_configs/credentials/rag_credentials.json
```

### Connect check — reachability and credential, before a session needs them

`connect-check` probes the configured producer and answers three questions a live session
answers only expensively: is the address reachable, **which** producer answered, and was our
credential accepted.

It probes **only the two free routes** — `/v1/health` and `/v1/pipelines/{id}/latest` — and never
the paid run route, so the check itself can never cost money. `/v1/health` is probed **without** a
token (the producer documents it as the one no-token route), which is what separates the two
failure modes: health failing is the *address*, `/latest` failing alone is the *credential*.

```
📡 PRODUCER CONNECT CHECK
   Endpoint:   production
   Address:    https://finiex-rag.duckdns.org
   Credential: user_configs/credentials/rag_credentials.json
   ✅ GET /v1/health                              journal 138c68e48b15 (production) · engine 0.3.3
   ✅ GET /v1/pipelines/crypto_sentiment/latest   seq 331 · epoch 1 · schema 2.0 · origin live
   ✅ Reachable and authenticated.
```

It prints the credential's **source file**, never the token, and says so explicitly when the file
is empty — with a tracked empty default and a gitignored override, "configured" and "empty" are
otherwise indistinguishable. Exit code is non-zero on failure, so it works as a pre-flight.

**A rejected credential is not an outage.** `401` / `403` are reported as a credential problem and
never as unreachability — the same distinction the running poll source now makes, where it also
**stops polling**: retrying cannot fix a token, and the staleness contract then declares the feed
blind, which is a state the decision logic is required to handle.

**An unreadable envelope is not an outage either.** The producer answered; our schema could not
read what it said. The poll source classifies that separately — state `contract`, its own
`contract_errors` counter, and a session-logger error naming the field that disagreed:

```
📡 Producer envelope failed OUR schema at `available_msc`: Input should be a valid datetime.
   This is NOT a producer outage — they answered, we could not read it.
```

Three properties, each with a reason:

- **It is not counted as a transport error.** Blaming their infrastructure for our own schema
  sends the diagnosis to the wrong system — the same misattribution the `401` rule prevents.
  It happened for real when the producer added `breaking_episode_id` / `breaking_episode_start`
  additively, with no `schema_version` change, and our declared type was wrong: every envelope
  was rejected, silently, as *their* fault.
- **Polling continues**, unlike the credential case. One malformed pass must not end a session,
  and a producer-side fix should be picked up without a restart.
- **The error goes to the session logger**, so it enters the §35 error pot — which means the run
  grades `finished_with_errors` and exits `3` (#372) instead of finishing clean on a feed that
  delivered nothing.

An additive field with no version bump is invisible to a consumer until something breaks; this is
what makes the break diagnosable instead of silent.

**The version signal, from the producer's #65 note onward:** they bump the **MINOR** for an additive
field and the **MAJOR** for a breaking one. So we **pin the major and let the minor pass** — a minor
we have not seen means the shape grew and is readable by construction. Both paths now gate on it
from one shared list (`SUPPORTED_SCHEMA_MAJORS`, `signal_data_types.py`): the archive reader always
did, and the live transport now does too, routing an unsupported major through the same contract
report above. Before that, a breaking bump would have been *mis-read* rather than refused — the
mirror image of rejecting a readable envelope, and the quieter of the two failures.

**A grown shape is named, once.** A minor bump says the shape grew; it does not say *what* grew, and
our models discard whatever they do not declare, so a new field would otherwise stay invisible until
something depended on it. The transport therefore diffs the arriving payload against the declared
field names and announces the difference — once per distinct set, at NOTICE level:

```
📡 Producer envelope (schema 2.1) carries fields we do not read: result.half_life_minutes,
   sentiment_regime. Not an error — the shape grew and we ignore the new parts.
```

Three deliberate properties: the values stay **discarded** (the projection is lean by design — a
diagnosis needs the field's name, not its content); **nothing is stored** on the snapshot or in the
parquet, so no per-row field exists to carry the answer; and it is computed **in the transport**, the
one place holding the raw payload and the parsed object at the same time. Once per set, because a
grown shape would otherwise log on every poll for the life of the session.


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
  the first snapshot resolves **blind** (empty result, `is_stale=True`). This is the signal
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

## Source vs. decision basis — two planes, two questions

The coverage report above describes the **source**: what the archive could offer. It cannot say
what a run actually consumed. That is the second plane, captured per tick by the SIGNAL workers and
rendered in the run's **📡 SIGNAL CONFIGURATION** section (#433):

| Plane | Question | Where it comes from |
|---|---|---|
| Archive | what *can* happen | `SignalCoverageReport`, read once in preparation Phase 1 |
| Decision basis | what the strategy *actually decided on* | per-tick counters on every SIGNAL worker |

### A live session has only one of them

A simulation and an AutoTrader **mock** run read their signal facts out of a finished archive. A
**live** session has no archive: envelopes arrive while it runs. So the run report is built from
whichever plane exists, and says which one it is:

| | Archive | Feed |
|---|---|---|
| Provenance, composition, cadence, extent, stream position | read from parquet | accumulated from arrivals |
| Gap classification, window coverage | measured against the market calendar | **not applicable** |

`Archive:` and `Feed:` are different lines in the report on purpose. An absent gap analysis is
**not** the same as a clean one: rendering an empty gap map as `no gaps` would assert continuity for
a series that was never analysable, and a `coverage_ratio` default of `1.0` would claim 100 % of a
window that never existed. Both are stated as absent instead.

Live cadence is the **producer's own reported interval**, labelled `(producer)` rather than
`(measured)` — a session that received three envelopes has no sample to take a median from.

**Live is not missing an outage view.** When the feed actually breaks, that is the
disturbance-episode protocol (📉 FEED STABILITY), which derives its spans from observed state across
both staleness domains. The signal section does not duplicate it.

The counters are three mutually exclusive classes that sum to the run's tick count:

- **fresh** — a snapshot resolved and is younger than `max_staleness_minutes`
- **stale** — a snapshot resolved but has aged out
- **blind** — nothing resolved at all

**`blind` is not an archive gap.** A hole *inside* the series resolves to the last snapshot before
it, which is `stale`. `blind` means there was nothing to resolve at all, which in practice only
happens at the head of a run — exactly the case the "window opens before the first snapshot"
warning predicts.

Why both planes are needed, in one example. The real `crypto_sentiment` archive has a 22-minute
hole on 2026-07-23 (09:30 → 09:52), which the coverage report lists as a SHORT GAP. With
`max_staleness_minutes: 30` the snapshot is 21 minutes old when the next arrives, so the run
produces **zero** stale ticks — the strategy never noticed. With `max_staleness_minutes: 15` the
same hole costs seven minutes of stale ticks. Same data, same coverage report, different outcome:
the counters measure data **×** parameter, which is precisely what a parameter-centric backtest has
to be able to see.

The run summary carries the run's weakest channel as `signal_fresh_ratio` (the minimum over all
scenario usages, unset when no SIGNAL worker ran) and writes it into the run-results ledger, so a
parameter sweep or a multi-window robustness pass can tell whether two rows were produced on
comparable data.

### When and how often — the third question (#451)

The counters say *how much*, not *when*: 52% fresh can be one long outage or forty short hiccups,
and the two demand different reactions. The **📉 FEED STABILITY** section answers that, for the
signal sources and the tick stream alike — one row per source with the stale time, the episode
count and each episode's observed span:

```
   crypto_sentiment (signal)   3,613 fresh · 1,387 stale · 0 blind   (72.3% fresh)
     stale 2026-04-27 06:30 → 2026-04-27 07:10   (40m 1s)   🧪 [STRESS] "sentiment feed dies 60min"
```

The span is always what the run experienced, not what a stress window planned — a carved
60-minute hole shows here as the ~40 minutes of staleness it actually caused, because
`max_staleness_minutes` has to elapse first. An episode still open when the run ends renders as
`→ run end`.

Deterministic test cases for all of this are carved with the stress module rather than hand-built —
see [Stress Test](../stress_test.md) and the `signal_resolution_cases` fixture set.

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
| `breaking_episode_id` | the story's identity — see below. **Opaque**, empty outside an episode |
| `breaking_episode_start` | a **flag**: true only on the pass that opened the episode |

### The breaking EDGE — derived here, never imported

`is_breaking` is the **state** of one envelope. A decision usually wants the **transition**: the
first envelope of a story, or the one where it ends. The `CORE/llm_sentiment` worker therefore
derives `breaking_edge` — `entered` / `exited` / `none` — by comparing against the envelope it
served before.

The producer also offers a filtered breaking-only view, and we deliberately do not consume it. If
the producer derived the boundary for the live path while we derived it in simulation, the two could
drift and **the disagreement would be invisible** — each side internally consistent, the pair
silently wrong. The same rule as the disturbance episodes: a boundary is always derived from
observed state; an upstream declaration may contribute a label, never a boundary.

Three situations report `none` although the state differs from the one before, each for its own
reason:

| Situation | Why not an edge |
|---|---|
| the first envelope of a session | a session that boots into an active story has witnessed no entry; reporting one makes every restart look like a fresh event |
| a gap | nothing resolved means the state is **unknown**, not `false`. Reading it as `false` would emit an exit going in and an entry coming out |
| an overtaking pass (`evidence_regressed`) | an envelope resting on older evidence did not witness what came after it, so letting it flip the edge turns the producer's commit order into a transition that never happened |

### The breaking EPISODE — the identity to gate on

`is_breaking` and `breaking_edge` answer *"did this pass cross the threshold"*. Neither answers
*"is this the same story as before"*, and that is the question a strategy actually has. The
producer's own measurement settles which unit to react to:

| Gate on | Transitions per episode | Transitions per story (crypto) |
|---|---|---|
| raw `is_breaking` edge | 19–21 | ~25–28 |
| **episode** | 1 | 1.33 |

So `breaking_episode_id` is the identity, and the worker exposes two outputs over it:

| Output | Meaning |
|---|---|
| `breaking_episode_id` | the producer's label, passed through unchanged |
| `breaking_episode_edge` | `opened` / `changed` / `closed` / `none`, derived here like `breaking_edge` |

`changed` is the reason a separate edge exists: one story replaced by another with no quiet pass
between reports **`none` on `breaking_edge`** — correctly, since the flag never moved — while the
identity says a different story began. The same three restraints apply as for the boolean edge
(first envelope, gap, overtaking pass all report `none`).

**The id does NOT track `is_breaking`.** The producer sets it on every pass it counts as inside the
episode: the opener, a hold-band pass where `is_breaking` is `false`, and a dip that arrives before
the gap elapses. An episode outlives its own boolean — that hysteresis is why an id present only on
breaking rows would have holes and would flicker as often as the raw edge.

**Treat the id as opaque.** It reads `<pipeline_id>:<query>:<start>` and is meant to be legible in a
log line, but the middle segment is free-text pipeline config and the string carries further colons,
spaces and slashes. Never split it, never derive the symbol or the start instant from it — what it
guarantees is byte equality: same story, same value. Length is bounded by their query text (70–100
characters today), so store it as variable-length text and encode it if it ever reaches a URL path
or a filename.

**Availability.** Both fields are on the wire from 2026-08-24 onward and are **absent from
everything archived before that** — the parquet columns exist but read as `''` / `false`, which is
the pre-field era's meaning. Consequence worth stating plainly: an episode-gated strategy cannot be
backtested on the pre-2026-08-24 archive at all, only on data carrying the fields.

**Restart stability, with its condition.** The id survives the *producer's* restart — they replay
persisted envelopes through the same episode rule and adopt the id they find rather than minting a
new one. The guarantee is conditional and the condition matters to us: the replay window
(`breaking.episode_seed_hours`, 72 h today) must contain at least one recorded **breaking** pass of
the still-open episode. The opener itself may fall outside it. The producer treats that setting as
contract-relevant and will announce a change. Longest hold-band tail they have measured: 33 h.

### Sweeping the delay — `signal_delay_minutes`

A worker parameter (default `0`, so nothing changes until it is set) that resolves as-of
`now − delay` while measuring staleness against the **real** moment. A delayed resolution genuinely
serves an older snapshot; measuring its age against the shifted moment would make every delay look
free and hide the exact cost the sweep exists to measure.

It answers one open question — **is the strategy's edge latency?** Sweeping 0 / 1 / 5 / 15 minutes
against P&L decides whether heartbeat-paced delivery is enough or whether the event loop has to move
ahead of live hardening. The zero column really is zero: the archive carries no unrecorded delay,
measured against the producer's journal envelope for envelope.

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
> as *unknown*. Backfilling is only ever legitimate where the value is **known** for the whole
> stretch — never as a convenience. For `trigger_reason` it is not: mapping a missing value to
> `scheduled` would silently fold restart and wake passes into the bar-close series, and no
> reconstruction exists that does not use exactly the timing heuristic the field replaces.

`data_origin` and `config_fingerprint` **were** backfilled into the historical real archives, by
`python/experiments/restore_signal_envelope_field.py`. Both values were known for the whole stretch:
the folders only ever held producer output, and the operator supplied the running config's hash. The
one caveat — the archive's symbol set grew on 2026-07-24, so strictly two configs existed — does not
change a *symbol's* comparability, because retrieval runs per symbol against a feed-driven corpus:
adding a symbol does not alter the scores of the others. The backfill was later confirmed by the
producer itself, which computes the same hashes it was given.

**A partially stamped archive states its unknown share.** When a producer gains a field mid-archive,
the counted composition covers only part of it, and reporting the counts alone would read as if the
producer had made that many passes in total. Both the coverage report and the run report therefore
append the unattributed count, so the numbers add up to the snapshot count:

```
Snapshots:    2,512
Triggers:     54 scheduled · 2 boot · 2 breaking · 2,454 unknown (pre-contract)
```

*Status (2026-08-17): `data_origin` and `config_fingerprint` are complete in all four sources.
`trigger_reason` is complete in the two mock sources; in the real sources it starts at
2026-08-16 14:51:50 UTC, when the live engine restarted with the field — everything before reads
`unknown` and stays that way.*

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
