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
- The live transport: the SSE stream (#468) with its frame decoder, boot bridge and
  producer-registry reader

**Total Tests:** 301

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

### test_signal_health_probe.py (#141 Part 2a)

Which producer journal a live session consumed from. Same local-stub discipline as the
transport suites.

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

### test_signal_source_resolver.py (#141 Part 2a)

Which source feeds a session's SIGNAL workers, resolved once by `SignalSourceResolver`. Three
answers, asked in one order — no SIGNAL worker → `NONE` (the installation-wide transport setting
does not apply to this profile); a prepared package → `MOUNTED` (its **presence** decides, never its
contents); otherwise → `LIVE` with the transport named.

Pinned: `MOUNTED` outranks an enabled transport; two signal kinds against one live transport is an
error (#258); `stream.enabled` resolves to the push transport, and where both transports are on the
stream wins — one connection delivers what `/latest` structurally cannot, and two transports filling
one inbox is not a fallback, it is a duplicate.

Two regression cases carry their own class, because both were invisible to this suite before —
pytest sets `FINIEX_CONFIG_ISOLATION`, so the workspace override that enables the transport was
never seen, and the CLI path was the one that broke:

- a profile **without** a SIGNAL worker must not be aborted by the installation-wide switch (it
  aborted 20 of 24 profiles, including four live trading profiles and both field-study release gates)
- a **mounted** session must not open a live transport (it mounted the archive *and* polled the
  production producer, folding live envelopes into a replay whose purpose is determinism)

### test_signal_sse_decoder.py (#468)

The stream's frame parser, driven by the producer's **committed frame sample** rather than by
hand-written strings. Reading that file by eye has already cost this project once: reissue 5 carried
`breaking_episode_start` as a flag while our declaration typed it as a timestamp, every live envelope
was rejected, and the rejection was misfiled as the producer's outage.

Two halves, and the second is what a single well-formed file cannot give:

- **The sample** — the whole documentation header is SSE comments and must dispatch nothing; every
  frame carries a named event and every name is one the contract names; every payload is one line of
  JSON; `retry: 5000` is read but never obeyed (settled cross-repo as a default for a client with no
  policy of its own — ours governs).

  These assertions are deliberately about the CONTRACT and not about the file's inventory. An
  earlier version counted the frames exactly (3 signal / 2 heartbeat / 5 control), listed the control
  codes in order, and pinned a heartbeat's `seq` — all of which pin which episode the producer
  happened to draw from. A reissue would then go red for the wrong reason, and a red for the wrong
  reason trains people to update the number instead of reading it. Frames are now found by the
  property under test (the cold start by `head_seq == 0`, not by being last in the file), and the
  recovery frames assert the RELATIONS a handler reads — `oldest_available_seq > requested_since`,
  `requested_since > head_seq` — rather than the numbers a sample happens to show.
- **The grammar** — a frame split across reads, a **multi-byte character** split across reads,
  multiple `data:` lines, one leading space stripped and only one, a blank line that dispatches
  nothing but still clears the pending event name, an `id:` line ignored entirely (honouring one
  would make a conforming client send `Last-Event-ID`, a header the producer does not read), CRLF
  equal to LF, and an unterminated frame **held rather than delivered** — a socket dying mid-frame
  must not put half an envelope in the inbox.

One more belongs to the unattended month rather than to the grammar: an unterminated line is
**bounded**. The decoder holds a line until its newline arrives, so a producer emitting bytes
without one would grow the buffer until the process dies — and it would die for a reason nothing in
the logs explains. Past the bound the line is refused as a contract violation and the decoder resets,
so the connection that follows starts clean rather than poisoned.

The strongest of them re-decodes the whole sample at chunk sizes of 1, 7, 64 and 997 bytes and
requires an identical frame sequence. A decoder that assumes one frame per chunk works until the
first slow network and then stops working for reasons nobody can see.

`epoch_changed` was **absent from reissue 6**, so its check skipped with a reason rather than
passing silently — the mistake this suite's sibling made by looping over an empty set and proving
nothing. **Reissue 7 carries it and the check now runs.** The skip branch stays: it is what made the
gap visible for the days the frame did not exist, and a future reissue that drops the frame will say
so again instead of passing over nothing.

The sample's own missing final blank line was documented the same way and is likewise closed — the
decoder stays strict (an unterminated frame at connection close is discarded on purpose, because a
socket dying mid-frame must never deliver half an envelope), and the producer's generator now
refuses to write a sample that does not end terminated.

**Reissue 7 landed green — no assertion moved**, which was the point of a hardening pass made just
before it arrived. Several checks had been pinning the file's INVENTORY rather than the contract, and
those would have gone red for the wrong reason. See the contract-versus-inventory note above.

### test_signal_stream_source.py (#468)

The push transport, against a **local mock producer** that enforces the connect contract — bearer
auth, an unknown pipeline as 404, `history` and `since` as mutually exclusive, `since` without
`epoch` as 400. A mock that accepted everything could not catch the request being built wrong, which
is the likeliest defect in a transport.

What is pinned is mostly the edges, where a transport quietly does the wrong thing for weeks:

- **The request** — the pipeline travels in the PATH, not the query. Their authorization derives the
  grant from the route's first path parameter, so a query-parameter form would be authenticated but
  ungated. A first session asks `?history=1` (the pre-stream archive carries no cursor); a resumed
  one asks `?since=&epoch=`.
- **The five control codes**, with the two rewind diagnoses routed **apart**: `epoch_changed` means
  the producer rewound → reconnect at the new epoch's head; `cursor_ahead` means somebody else did,
  most likely our own store was restored → stop and alert, never a silent resume. `auth_revoked`
  stops without retrying. An unknown code or event name is contract GROWTH and must not become an
  outage.
- **Refusals are not outages** — 401/403, 404 (a misspelled pipeline id) and 400 all STOP. A client
  that cannot tell "does not exist" from "exists but idle" waits forever on a typo while the panel
  shows a healthy reconnect loop.
- **Gap recovery** — the cursor is the last CONTIGUOUS position, not the highest seen. An envelope
  arriving past a hole is still enqueued (withholding a valid envelope helps nobody) while the cursor
  stays behind the hole so a reconnect can ask for it. The same boundary is asked for **once**: a
  second encounter means the producer cannot fill it, and reconnecting forever against an unfillable
  hole turns a reported gap into an outage of our own making.
- **Deduplication** — the replay redelivers what was accepted past the hole. Harmless for the series,
  which deduplicates by the same key, but a second count in the observed accumulator is a wrong
  number in the run report.
- **Resilience** — a silent socket past the watchdog is a CONNECTION fault; a closed connection
  reconnects without losing the cursor; a 5xx backs off and retries; an unreadable envelope, an
  unsupported schema major, undecodable JSON and a malformed **control or heartbeat** frame are all
  contract errors that leave the connection **open**, because they are our schema disagreeing with
  their answer and dropping the connection retries a mismatch retrying cannot fix.

**A quiet stretch inside a connection has its own class, and it exists because this suite could not
produce one.** Every other scripted reply writes its whole body at once and then holds or closes, so
the read loop never had to survive a silence and come back — and it did not. The first version polled
the socket with a one-second timeout, and CPython marks a socket file object PERMANENTLY timed out
after its first expiry: the second read raises a plain `OSError` rather than `TimeoutError`, escapes
the handler, and the healthy connection is torn down as a *transport fault*. In production the stream
would have degraded into a reconnect loop — worse than the pull path it replaces — with the panel
blaming the producer.

The suite's own timings hid it. At `heartbeat_seconds` 0.4 and multiple 2.0 the watchdog is 0.8 s,
BELOW the one-second poll: the single configuration in which a second read never happens. Production
is the opposite. So the class scripts a mid-connection gap and asserts what a real feed looks like —
**one** connection, both envelopes, zero transport errors — and its sibling asserts the other side of
the boundary, that silence past the watchdog IS a fault. That second assertion was unreachable
before: the OSError always arrived first, so the silence error could never fire at all.

**Stopping while the producer hangs** has its own class for the same reason. A producer that accepts
the connection and never sends a response head left `stop()` with nothing to shut down, because the
socket handle was published only after the response was read — so a session end blocked for the whole
watchdog, and in a live session that wait sits *ahead of closing open positions*. The test measures
that `stop()` returns in under two seconds against a six-second watchdog.

### test_signal_boot_bridge.py (#468)

What a live session knows before its first envelope arrives. Without the bridge it knows nothing:
the workers start empty and the first decision waits out a full producer cadence. On a thirty-day
unattended run that is every restart, and a restart at 03:00 is exactly when nobody is watching.

The distinction the file is built around is **BLIND versus STALE** — knowing something old is a
strictly better input to a staleness contract than knowing nothing, because "old" is a fact a
decision logic can act on.

Pinned: an empty archive starts blind and SAYS so; the cursor is the newest position in the slice;
a pre-stream archive yields no cursor at all (a property of the first session, not a bug); an archive
spanning the contract boundary takes the newest **identity-bearing** row rather than the last row;
both halves are required, because a seq belongs to an epoch and half a cursor earns a 400. The
lookup window is the producer's own replay window, so the mounted slice and the bounded replay meet
rather than overlap — and a cursor older than that window is flagged, because the replay will be
truncated and the operator should hear it before it happens rather than as a surprise.

### test_signal_mock_producer.py (#468)

The local stand-in producer, driven by the REAL transport over a real socket.

It exists because of a gap that is structural rather than accidental: every mock session in
this project mounts its signal series from the archive, so the resolver answers MOUNTED and
**no connection is ever opened**. Everything behind the inbox is therefore richly covered by
mock runs and everything in front of it is unreachable from one — including four of the five
control codes, which a healthy producer will not emit on request.

- **The stand-in speaks the contract** — a healthy stream goes live, delivers its snapshot and
  does NOT reconnect; the registry carries both served stream values, without which a session
  refuses to start.
- **One test per control code**, because their responses differ on purpose: `epoch_changed`
  reconnects and carries the NEW epoch into the cursor; `cursor_ahead` and `auth_revoked` are
  terminal with one connection; `replay_truncated` continues, which is the whole reason it is
  a separate code.
- **A revoked token raises no transport error** — counting it as one sends the operator to the
  wrong system.
- **Parametrized over the whole enum**, so a sixth control code fails here until the stand-in
  can produce it. A code nobody can emit is a code nobody will ever look at.

Nothing is patched. If the stand-in stopped speaking the producer's frame grammar, the
production reader would be the first thing to refuse it.

### test_signal_feed_stream_observer.py (#468, #466)

The release certificate's reader over the push transport, run UNMOCKED against a local producer
that serves the same four routes the real one does, over a real socket — patching the reads would
skip exactly what breaks against a real server.

Two of the assertions are the defects this observer exists to remove:

- **the transport is RECORDED from the run.** It was a module constant written straight into the
  artifact, so a certificate taken over the stream would have claimed `poll` — the same defect class
  as an adapter certificate that re-read a config file instead of recording what its run did;
- **the raw envelope survives beside its parsed form.** Roughly two thirds of the certificate's
  checks read the WIRE and not the model: a field's absence, its wire type and its location are all
  unanswerable once a payload has become an object. `collected_msc` is the clearest case — never on
  the wire, always on the model.

Also pinned: the connect asks for **three** envelopes, because one position cannot show that a series
moved and the validator's comparison loop would run zero times; all four free routes are recorded, so
a later reader can see the run spent nothing; and every way the run can fail to proceed is a NAMED
failure rather than a silence — a producer that does not serve the stream block, an unregistered
pipeline, a rejected credential (reported as a credential condition, never as unreachability), a
connection that opens and delivers nothing, and a frame our own reader refuses (counted apart from a
transport fault, with the readable frames still counting — one bad envelope is not a dead feed).

The mock producer gained a `held()` helper for a reason worth recording: a reply that closes after
its body makes the transport reconnect and re-deliver the same snapshot, so a 0.6 s observation
collected the same three envelopes sixteen times. That is a property of the test rig, not of the
producer, and reading it as one would have hidden the real behaviour.

### test_signal_stream_probe.py (#468)

`signal_index_cli.py stream-probe` — the operator's window onto a transport that otherwise only
shows itself inside a running session.

What is pinned is not that it renders. It is that it **refuses to call a dead producer healthy**: a
socket that opened and then delivered nothing leaves the transport in `connecting`, and counting
that as success is precisely the failure a probe exists to expose (the CLI exits non-zero on it).
Alongside, the three guards in front of the connection — an unreadable registry, a producer that
does not yet serve the stream values, an unregistered pipeline id — each refusing with the same
reason a session would give, before a socket is opened. And that the probe claims **no cursor**: one
that advanced a session's position would consume envelopes the session it was meant to diagnose
still needs.

### test_signal_pipelines_reader.py (#468)

Three numbers live on `GET /v1/pipelines` that we deliberately do **not** configure: the evaluation
cadence, the keep-alive interval and the replay window. Each was a candidate for a constant on our
side, and a local copy of somebody else's number reports a feed outage that never happened on the day
they change it.

The shape is engine-wide values in the response and per-stream values on the row — a per-row copy of
an engine property claims to be per-stream, and someone eventually sets two of them differently.

Pinned: both shapes read (the bare list from before the stream values joined it, and the envelope);
a row without an id is skipped rather than keyed by nothing; **an absent value is reported as absent**
— a partial `stream` block yields no settings at all, a missing cadence is `None` and never `0` (zero
would divide, and a staleness threshold computed from it would be instant), and a boolean is not a
number. A refused credential stays separable from an unreachable address, because only one of them is
the producer's outage.

---

## Fixtures

`tests/fixtures/signals/sentiment_sample.jsonl` — int-ms `collected_msc`, covering
success / no-news / partial / error (empty result) / breaking paths.

`tests/fixtures/signals/signal_stream_frames_reissue7.sse` — the producer's committed stream-frame
sample, shared with the import suite's contract checks. The decoder suite parses it; the transport
suite scripts its own frames, because what it exercises is connect, replay and control routing rather
than the wire shape.

The mock SSE producer is a **code-level** fixture in `conftest.py` (`MockStreamServer`,
`MockStreamReply`): a script of replies, one per connection, so reconnect, gap replay and epoch
change are expressible as tests rather than as timing luck.

---

## Running

```bash
python -m pytest tests/framework/signal_workers/ -v
```
Or via launch.json: `🧩 Pytest: Signal Workers (All)`.
