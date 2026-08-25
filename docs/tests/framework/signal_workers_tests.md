# Signal Worker Tests Documentation

## Overview

The signal-worker suite validates the SIGNAL worker type (#141): the pydantic envelope types, the
JSONL loader, the `SignalDataProvider` lookup, the `CORE/llm_sentiment` worker, the orchestrator
dispatch, and the didactic `CORE/hybrid_sentiment_reference` decision fusion. All run against mock
fixtures via direct provider injection — no batch, no tick loop.

**Test Location:** `tests/framework/signal_workers/`

**Components Covered:**
- Types: `AnalysisEnvelope` / `SentimentResult` / `SignalSnapshot` / `SignalSeries` / `RunError`
- `SignalDataProvider` + `signal_jsonl_loader`
- `CORE/llm_sentiment` worker (`AbstractSignalWorker`)
- `CORE/hybrid_sentiment_reference` decision logic
- `WorkerOrchestrator` SIGNAL dispatch + the per-tick resolution counters (#433)

**Total Tests:** 53

---

## Test Files

### test_signal_provider.py
- Pydantic parse of the archived line — **int-ms `collected_msc`** normalization to UTC datetime,
  extra-tolerant metadata.
- JSONL loader — load + sort + `schema_version` gate + range trim + `status: error` (empty result).
- Provider — nearest `collected_msc ≤ tick` (gap → None, boundary inclusive, defensive HOLD on an
  empty/error snapshot).

### test_llm_sentiment_worker.py
- Worker contract — SIGNAL type, output schema, no warmup, factory registration.
- `compute_signal` mapping — gap → empty / confidence 0, snapshot field mapping, staleness flag.
- `should_refresh` — cold start, same window, new window.
- Orchestrator dispatch + snapshot recompute cadence (recompute on a new window, cache between).

### test_hybrid_sentiment_decision.py
- Fusion — RSI core + sentiment overlay (aligned boost, opposed block, stale ignored).
- #425 subscription — declared sentiment signals exist on the worker output schema.
- Factory registration.

### test_signal_outage_contract.py (#434)
- Staleness-flip refresh — a feed dying mid-session triggers exactly one recompute
  (envelope `is_stale` flips); recovery refreshes via the new snapshot window.
- Envelope guarantee — `result.is_stale` survives #425 subscription narrowing (status is
  the envelope, never a payload output).
- Startup validation — SIGNAL consumption without an `on_signal_stale` override is
  rejected; indicator-only decisions are unaffected.
- Edge-triggered dispatch — the hook fires once per fresh→stale flip (including a session
  that starts stale), resets on recovery.
- Reference reaction — `hybrid_sentiment_reference` warns to the session channel and emits a
  `signal_stale` event-tape entry.
- Stale-data slicer (#436 stress, data-plane carve) — `StaleDataSlicer` removes the snapshots
  inside a planned stale window from the refined series ([start, end) semantics, input series
  unchanged); lookups then resolve as-of the last pre-window snapshot and drive the REAL
  staleness chain: the aged resolution flips the worker's own `_evaluate_stale` (no flag forcing).

### test_signal_resolution_counters.py (#433 Part C)
- Classification — the worker splits what `_evaluate_stale` collapses into one boolean:
  `FRESH` / `STALE` / `BLIND` (nothing resolvable at all, distinct from an archive gap).
- Cached class — `should_refresh` keeps the class current on a tick without a recompute, which
  is what makes counting the cached state correct between refreshes.
- Tick-exact counting — one count per tick per worker in BOTH worker paths (sequential and
  parallel); a run whose worker computed twice over eight ticks counts eight, not two.
- Stale accumulation + row identity (worker name / signal source / symbol).
- Heartbeat — a heartbeat pass re-evaluates nothing and therefore counts nothing.

### test_signal_stream_identity.py (#141 Part 2a)

The rules that take over once a snapshot carries the producer's stream identity
(`seq` / `stream_epoch` / `available_msc`), and the fallbacks that keep the pre-stream archive
resolving exactly as before.

- **Pre-stream fallback** — no identity → the gate is `collected_msc` and the order key sorts by
  time ahead of any numbered epoch. Lookup behaviour is unchanged.
- **Resolution gate** — `available_msc` (the producer's publish instant) gates visibility where it
  exists, so a snapshot is invisible before it could really have been had.
- **Clock-correction clamp** — a producer-side stamp that steps backwards never makes a snapshot
  visible *earlier* than the one preceding it in the series. That is the only direction that would
  be look-ahead.
- **Ordering** — `seq` orders within an epoch; the epoch outranks `seq`, because a reset restarts
  the numbering. No clock takes part in the order.
- **Deduplication** — the producer is at-least-once, so a redelivered envelope is a no-op;
  identity is the `(stream_epoch, seq)` pair, since `seq` is unique only *within* an epoch.
- **Extension** — `extend()` keeps the series resolvable and reports how many were new.

### test_signal_off_tick_arrivals.py (#141 Part 2a)

An envelope that lands **between two ticks**. `process_heartbeat` forwards cached worker results
by design, so without this seam a pushed envelope would wait for the next tick — minutes on a
quiet instrument, which is what a push channel exists to avoid.

Two entry points, because the two loop paths need different things:

| Path | Call | Why |
|---|---|---|
| tick | `merge_signal_arrivals()` | merge only — the worker pass that follows picks the snapshot up through `should_refresh` exactly as it picks up a mounted one |
| heartbeat | `process_signal_arrivals()` | merge **and** refresh + run the shared signal pass, since nothing else would |

What the tests pin, and the second half matters as much as the first:

- an arrival reaches the decision without a tick, and an arrival that **ends an outage** recovers
  at the arrival moment rather than at the next tick;
- a redelivery is a no-op (the producer is at-least-once);
- an unknown source is ignored;
- **the three resolution counters stay tick-weighted** — an off-tick refresh increments
  `off_tick_arrivals` only, because the ledger's `signal_fresh_ratio` is defined on the tick base.
  Changing that base is #463's job;
- an empty drain does nothing at all — which is the simulation's and a mock session's case on
  every single pass, and is what keeps both bit-identical.

### test_signal_poll_source.py (#141 Part 2a)

The interim pull transport, used until the producer's stream exists. Runs against a **local stub
started inside the test** — never against a real producer: a suite that needs someone else's
container running is a suite that fails for reasons unrelated to the code.

Only the *responses* are scripted. The real transport makes a real HTTP request over a real socket,
builds its own headers (the bearer assertion reads them **server-side**), decodes real JSON and
validates through the production model. Patching the fetch would skip exactly what breaks against a
real server.

- **Arrival** — a new envelope is enqueued; receipt is stamped by us (`collected_msc` is absent on
  the wire) while the gate stays the producer's `available_msc`.
- **Restraint** — the producer republishes the same stored envelope until its next pass, so the
  same `(epoch, seq)` is enqueued once, not on every poll.
- **The degraded producer** — `status: error` + `VECTOR_STORE_ERROR` means "no envelope" and must
  never be enqueued: it would place a degraded HOLD into the series that the provider would later
  resolve as if it were sentiment. A *normal* `status: error` (an LLM timeout) **is** data and is
  kept.
- **Auth** — the header is sent only when a token is configured.
- **A grown shape** — an envelope carrying fields we do not declare is accepted and enqueued,
  and its unread field names are announced **once per distinct set** at NOTICE level. Pinned
  because the producer's minor bump says the shape grew without saying what grew, and our models
  discard the undeclared silently. The second poll of the same shape must stay quiet, or a grown
  envelope logs on every beat for the life of the session.
- **Contract violation** — an envelope the producer served and our schema cannot read is
  classified apart from a transport fault: state `contract`, its own counter, `transport_errors`
  untouched, nothing reaching the inbox, and a session-logger error naming the offending field.
  The first version of this loop counted it as a transport error and retried forever, so a
  mismatch on our side presented as the producer's outage — the same misattribution the `401`
  rule forbids. It happened for real when the producer added the episode fields additively.
- **Lifecycle** — an unreachable producer never raises into the loop; stop is idempotent.
- **Transport state** — the state must describe the transport *now*, not at the last arrival.
  The producer's beat is far longer than the poll interval, so most polls legitimately return an
  already-seen envelope and leave through the early return. A state that recovered only on arrival
  left a transient fault on the panel until the producer happened to publish again — **a healthy
  feed reading as a broken one**, the exact misreading the panel exists to prevent.
- **Health probe** — starts and stops with the transport it accompanies; its identity reaches both
  the panel and the shared tape. Without a probe the identity is reported as unknown, never
  fabricated.

### test_signal_health_probe.py (#141 Part 2a)

Which producer journal a live session consumed from. Same local-stub discipline as the poll source.

The probe exists because **nothing on an envelope says which store it came from**. Two producer
instances share a schema, a `pipeline_id` and a `seq` range, so a measurement taken against a
development instance is indistinguishable from one taken against the series a release is certified
on. The producer answers on its health endpoint and nowhere else.

The asymmetry the tests pin, because it is the whole point: **the id binds and the name does not.**
The id fingerprints the producer's database cluster and is fixed at its creation; the name is
resolved from a mapping on the producer's machine and may be renamed at any time.

- **Identity** — id, name, engine version and pass timeout are recorded from `/v1/health`; a name
  the producer could not resolve degrades to `unknown` **without** the id losing its meaning; the
  identity is written to the session logger, because a screen cannot be read after the run.
- **Unidentified** — a `null` journal is a real answer (no store attached, or an identifier the
  producer's role may not read), distinct from "the probe has not run". Warned once, not on every
  cycle; an identity that arrives later is not treated as a change.
- **Change** — the case the cyclic cadence exists for. The cursor built so far belongs to the
  previous journal, so a change is reported as an **error** (reaching the session summary, §35) and
  the flag is **sticky**: it describes the session, not the current answer. Losing an identity
  counts as a change too. An unchanged journal stays silent — half-hourly probes over a multi-week
  run must not narrate themselves.
- **Producer cadence** — the health document names how often the producer evaluates *our* source,
  which is the authoritative version of a value we otherwise only configure. A drift is reported
  once, because the configured value drives the staleness threshold: a producer that slowed down
  turns a healthy feed into one that keeps tripping the contract, and one that sped up hides a real
  outage inside the tolerance. Another pipeline's worker must never answer for ours.
- **Producer budget** — a producer that stops evaluating to save money reaches us as **silence and
  nothing else**: the transport stays healthy, envelopes simply stop. Only the transition is
  reported (in both directions), so a long suspension does not repeat itself across a multi-week run.
- **Lifecycle** — an unreachable producer never raises; a failed probe never erases what is known.

### test_signal_off_tick_arrivals.py — the compute that nobody counted

Beyond the merge/refresh split, this suite pins that an off-tick compute is **recorded as a
compute**. The tick path times every recompute and hands it to the performance logger; the arrival
path did not, so a worker whose first envelope lands *before the first tick* — which is the normal
case, the transport starts before the market does — never seeded on the tick path either and stayed
invisible for the rest of the session.

Measured on the first live observation run: the SIGNAL worker refreshed three times and the run
report said **`0 computes`** while the log beside it showed all three arrivals. A number an operator
reads must not contradict the log next to it.

### test_signal_evidence_regression.py (RC-4, #141 Part 2a)

The producer runs passes concurrently, so a long-running pass commits *after* a later one: it
carries the newer position and the older view of the world. A decision reading that as a CHANGE
reacts to a reversal that happened only in the ordering.

Two properties decide whether the detection works, and both are counter-intuitive:

1. **Per envelope, never per row.** A row's evidence stamp may legitimately fall between passes
   (its retrieved set changes) — measured on one mock week: **2073 per row against 17 per envelope**.
2. **The runtime series is projected to one symbol**, so a max over a projected snapshot's rows is
   that row's stamp. The importer therefore carries the envelope-level value alongside; without it
   simulation and live disagree — measured: **237 against 17**.

The tests pin the accessor's precedence, the flag on an overtaking pass, and — as importantly — the
cases that must **not** flag: the first envelope, a gap, an envelope resting on no evidence, and the
envelope after a regression (the flag marks an envelope, not a session).

### test_signal_breaking_edge.py (#141 Part 2a, Phase 4)

`is_breaking` is the **state** of one envelope; the edge is the transition between two consecutively
served ones. Derived on our side in both pipelines rather than taken from the producer's filtered
view — if the producer derived the boundary live while we derived it in simulation, the two could
drift and the disagreement would be invisible.

Most of the file pins the three ways an edge must **not** fire, each with a different reason: the
first envelope of a session (a boot is not an entry), a gap (unknown is not `false` — reading it as
`false` emits an exit going in and an entry coming out), and an overtaking pass (an envelope on
older evidence did not witness what came after it, so it must not flip the edge). The last one also
pins that the suppressed envelope is **not remembered**, or the next correctly ordered one would
compare against a view already discarded.

The file also pins the **episode edge** — `opened` / `changed` / `closed` / `none`, derived here from
the producer's identity label with the same three restraints as the boolean edge. The case that earns
it its own type is `changed`: one story replaced by another with no quiet pass between, which
`breaking_edge` reports as `none` because the flag never moved. Its mirror is also pinned — a
hold-band pass keeps the id (`none`) while the flag drops (`exited`). Plus the plainest one, easy to
forget: the id must actually *reach* the decision as an output, or no logic can gate on it.

And the producer's **episode identity** on the wire (their #65, live 2026-08-24):
`breaking_episode_start` is a **flag**, not a timestamp, and `breaking_episode_id` is set on every
pass the producer counts as inside the episode — including hold-band passes where `is_breaking` is
`false`. Use the id for identity, `is_breaking` for "this pass crossed the threshold". Pinned
because the field arrived with **no `schema_version` change**, so nothing in the envelope announced
it; our first declaration had the wrong type and every live envelope was rejected. The archive era
parses with empty defaults, which is what keeps historical replay working.

### test_signal_delay_lever.py (#141 Part 2a)

`signal_delay_minutes` resolves as-of `now − delay` while measuring staleness against the **real**
moment — a delayed resolution genuinely serves an older snapshot, and measuring against the shifted
moment would make every delay look free.

Pinned: the default is `0` and changes nothing; the delay shifts the series rather than skipping
through it; a delay past the whole archive goes blind rather than failing; and the refresh trigger
reads the **same** as-of moment as the resolution — a disagreement there produces results that are
correct individually and wrong in sequence.

---

## Fixtures

`tests/fixtures/signals/sentiment_sample.jsonl` — int-ms `collected_msc`, covering
success / no-news / partial / error (empty result) / breaking paths.

---

## Running

```bash
python -m pytest tests/framework/signal_workers/ -v
```
Or via launch.json: `🧩 Pytest: Signal Workers (All)`.
