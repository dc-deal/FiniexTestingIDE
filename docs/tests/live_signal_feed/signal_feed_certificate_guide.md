# Live Signal Feed Certificate — Operator Guide (#466)

A release ships a bot that decides on an external producer's data. Every other external
dependency in this project carries a release-gate certificate — throughput (benchmark),
broker execution (live adapters), real-money order lifecycle (field study). This is the
signal feed's, and it is the one input the strategy's edge is built on.

The suite proves that **the contract holds against the running producer**, emits a
PASS/FAIL certificate, and is excluded from the daily runner.

> **Cost: nothing.** The run calls only the producer's free routes — `GET /v1/health`,
> `GET /v1/build`, `GET /v1/pipelines/{id}/latest` — never `POST /run`, which turns a request
> into LLM spend on their side. Every call is recorded in the certificate and checked against
> an allow-list, so the property is verified rather than promised.
>
> | Route | Token | Gives |
> |---|---|---|
> | `GET /v1/health` | no | `journal_id`, `environment`, engine version, per-source cadence, budget state |
> | `GET /v1/build` | no | `commit`, `committed_at`, `dirty`, `started_at` — public by their default, behind a switch on their side |
> | `GET /v1/pipelines/{id}/latest` | yes | one envelope |

## What it certifies — and what it must not

It certifies that the producer's envelopes are **readable, correctly shaped and honestly
stamped**. It does **not** certify that the sentiment is correct — that is unknowable, and
a gate that turns red on an unlucky news day certifies nothing but the weather. The same
separation the live-adapter certificate makes: it proves an order goes through, not that it
was profitable.

---

## Components

| Piece | Location |
|---|---|
| Producer reads + the held stream | `python/framework/signal_data/producer/signal_feed_stream_observer.py` |
| Shared HTTP read + 401 classification | `python/framework/signal_data/producer/signal_http_reader.py` |
| The assertions | `python/framework/validators/signal_feed_contract_validator.py` |
| Certificate, PASS/FAIL, rewind chain | `python/framework/reporting/certificates/signal_feed_certificate.py` |
| Types | `python/framework/types/signal_certificate_types.py` |
| Suite | `tests/live_signal_feed/` (incl. `test_signal_feed_gate.py`, the catch-all) |
| Netless assertion tests (daily suite) | `tests/data/signal_import/test_signal_feed_contract_validator.py` |
| Certificates (committed) | `tests/live_signal_feed/reports/` |

**Three stages, kept apart:** the observer READS, the validator JUDGES, the certificate
RECORDS. The validator never opens a connection, which is what lets the same assertions
certify the interim pull transport today and the stream (#468) later — the certificate
gains a transport section instead of being rewritten.

---

## Pre-Flight

1. **The PRODUCTION producer must answer**, not the development instance. Check first:
   `python python/cli/signal_index_cli.py connect-check`
2. A real token in `user_configs/credentials/rag_credentials.json`.
3. `user_configs/sentiment_config.json` sets `producer.active: production`,
   `stream.enabled: true` and `stream.pipeline_id`.
4. `FINIEX_CONFIG_ISOLATION=0` — see the warning below. The launch entry sets it.

> **Why config isolation must be off.** `tests/conftest.py` switches off user-workspace
> overrides so no personal setting can decide a test outcome — right for every other suite.
> But the production address and its token live exactly there, so without the override this
> suite would resolve the **development** endpoint from the tracked default and produce a
> certificate that passes and certifies nothing. That is the single failure mode the whole
> gate exists to prevent, so the suite never proceeds under isolation.
>
> Whether it **skips or fails** is decided by declared intent, not by the misconfiguration:
>
> | Run | Behaviour |
> |---|---|
> | isolated, no `--release-version` | **skips** — a sweep like `pytest tests/` collects this suite incidentally and should not produce a wall of red from a release gate |
> | isolated, `--release-version X.Y.Z` given | **fails loudly**, naming the cause. Someone who named a release version meant to certify, and a release gate that quietly skips is the same failure family as one that exits 0 on failure (#372) |

---

## Operator Workflow

**Step 1 — the certification run** (launch.json → `🧩 Pytest: Live Signal Feed (All) -
production producer`, or from a terminal):

```bash
FINIEX_CONFIG_ISOLATION=0 pytest tests/live_signal_feed/ -v -m live_signal_feed \
    --release-version 1.4 --comment "pre-release check"
```

The certificate is written at session finish to `tests/live_signal_feed/reports/` and
printed as a summary. **Commit it.**

**Step 2 — read it back** (launch.json → `🧩 Pytest: Live Signal Feed Certificate
(validate committed)`):

```bash
pytest tests/live_signal_feed/test_signal_feed_certificate.py -v
```

It asserts the committed artifact exists, is not expired (90 days), shows `PASSED`, names a
journal, and is complete. It **skips** inside a certification run — that certificate is
written at session finish, so a read-back in the same session would describe the previous
run while appearing to describe this one.

### Options

| Option | Default | Meaning |
|---|---|---|
| `--release-version` | `dev` | Version this certificate covers |
| `--comment` | `''` | Free-text note recorded in the artifact |
| `--stream-seconds` | `25.0` | How long the stream is held open. The default crosses one keep-alive at the producer's 20 s beat, so the observation covers a quiet stretch and not only the connect snapshot |
| `--reports-dir` | `tests/live_signal_feed/reports` | Where the certificate is written and the previous one read from |

---

## The checks

**Transport** — thin by design, because this is the part the stream replaces.

| Check | Says |
|---|---|
| `health_route_answers` | `/v1/health` answered **without** a token, so the address is right |
| `credential_accepted` | the token opened `/latest`. A 401 is a credential condition, never their outage — their contract says so, and merging the two sends the operator to the wrong system |
| `latest_route_answers` | the source served an envelope |
| `run_touched_only_free_routes` | every recorded call was a free GET |

**Wire shape** — over the raw payload, so it still runs on an envelope our reader refused.

| Check | Says |
|---|---|
| `schema_major_supported` | the major is one we read. The minor is **recorded, not asserted** — a minor means an additive field, so an unseen one means the shape grew |
| `trigger_reason_at_top_level` | not in `metadata`. The producer spent a schema major on that move |
| `collected_msc_absent_on_wire` | the receive stamp is ours to set |
| `envelope_field_*` / `row_field_*` | every contracted field present, at its location, **with its type** — and `bool` held apart from `int`, so `is_breaking: 1` is refused |
| `evidence_matches_max_fetched_at` | each row's stamp equals the newest `fetched_at` it rests on, compared at millisecond resolution |
| `evidence_present_exactly_with_evidence` | a row resting on nothing carries no stamp |
| `no_evidence_after_available_msc` | no evidence stamped after the envelope became available |
| `episode_id_is_opaque_when_populated` | a populated id is not cleanly splittable. Says **"not exercised"** when no row carried one, rather than passing vacuously |
| `episode_start_implies_an_id` | a pass that raises the flag is inside an episode, so it names it |

> **Why the type column is the load-bearing one.** The producer's own frame-sample gate
> asserted each field's presence and location but never its type — which is how a `''`
> placeholder passed it three days before production began emitting `null`. A certificate
> repeating that omission would certify the same blind spot.

**Our reader, unmodified**

| Check | Says |
|---|---|
| `envelope_parses_through_production_reader` | through the shipped `SignalSnapshot`, no test-only shim |
| `resolution_key_is_available_msc` | the producer's publish instant, not our receive time |
| `order_key_is_well_formed` | `(stream_epoch, seq)` — chronological with no clock in it |
| `no_look_ahead_before_availability` | the production `SignalDataProvider` returns nothing one second earlier |
| `closed_vocabulary_values_tolerated` | proven by **mutation**: an unknown signal / basis / status / origin still parses |

**Build provenance — whose code produced this**

Two builds meet in one artifact: theirs produced the envelopes, ours read them. A certificate
naming neither cannot be re-derived by anybody.

| Check | Says |
|---|---|
| `producer_build_is_reproducible` | their `commit` is named and their tree was clean. **Not asserted** when they do not publish the route — it is public by their default but behind a switch, and reporting their policy as our failure would be asserting a promise nobody made |
| `consumer_build_is_committed` | our tree was clean — asserted **only when a release version was declared**. A rehearsal against a working tree is the normal case; an artifact *claiming* a version whose code exists only in one working tree cannot be re-derived by anyone, including us |

> **Why the version string is not enough.** Measured 2026-08-25: the producer deployed a new
> commit at 16:28 while `version` stayed `0.3.3`. Two certificates taken twenty minutes apart
> therefore came from **different code and looked identical**. Only the commit binds — the
> same relationship `journal_id` has to the environment name.

**Series, within the run**: `seq_never_steps_backwards`, `stream_epoch_stable_within_run`,
`producer_cadence_matches_registered` (their reported interval against ours — ours drives
the staleness threshold).

**Series, across two certificates** — the check only this artifact can make.

| Check | Says |
|---|---|
| `journal_matches_previous_certificate` | the same journal as the last certificate. The first one **establishes** the binding; every later one is checked against it |
| `seq_did_not_rewind_since_last_certificate` | no lower `seq` on the same journal |

> **Why a single session cannot see a rewind.** The producer's own near-miss: a "clean
> slate" wipe truncated the sequence *and* the journal, and boot reconciliation recovers a
> reset counter by reading `max(seq)` back out of the journal — so with both gone the engine
> re-mints from `seq 1` while a consumer holds a much higher cursor. Every new frame then
> sits below the mark and is ignored, and **the connection stays perfectly healthy**. From
> inside one session nothing steps backwards because nothing arrives at all, and the
> staleness contract reports "the producer went quiet": the right symptom, the wrong
> diagnosis. The certificate is the only artifact that survives between runs.
>
> The comparison is bounded to **one journal**: two producer instances share a `seq` range,
> so a development certificate beside a production one would otherwise read as a rewind.
>
> The rewind detail also names whether the producer **restarted** between the two
> certificates, from `started_at`. That is the one moment a counter gets re-minted, so it
> belongs in the sentence on both outcomes. Measured on 2026-08-25: the producer restarted at
> 16:28:23 between two certificates and the sequence continued cleanly (498 → 499) — their
> boot reconciliation recovering the counter from the journal, which is exactly the mechanism
> whose failure this check exists to catch.

**Provenance**: `producer_named_a_journal` (a `null` journal is a real answer and a **FAIL**
— a session against no identifiable series is not a series anything can be certified
against), `journal_is_not_a_development_instance`, `endpoint_aimed_at_production` (intent
beside answer), `data_origin_is_live` (their mock emits `synthetic` precisely so this can be
caught).

---

## Recorded, not asserted

A change here is a **comparability break the operator should see**, not a failure: both build
blocks (their `version` / `commit` / `committed_at` / `dirty` / `started_at`, and our `branch` /
`commit` / `committed_at` / `message` / `dirty` / `uncommitted_count`),
`config_fingerprint`, `prompt_version` / `prompt_hash`, `schema_version`, the observed
cadence, envelope age at fetch, frame size, row count, the transport used, `unread_fields`
(wire fields our reader does not consume), `unknown_vocabulary`, and
`rows_without_evidence` — the last one so an unexercised branch is visible instead of
silently green.

---

## When the reader refuses an envelope

The wire checks run over the raw payload **anyway**, so the certificate names the field that
disagreed instead of stopping at "our reader said no". That distinction is not theoretical:
a flag we had typed as a timestamp made the model reject every live envelope for a day, and
the rejection was filed as the producer's outage — our schema reading as their fault.

---

## The gate itself

`test_signal_feed_gate.py` carries two tests that exist for what the named assertions cannot
cover:

- `test_no_check_failed` — fails when **any** check failed, whatever it was. A release gate
  that exits 0 while its own artifact says FAILED certifies nothing (#372), and a check nobody
  named would otherwise fail silently into the certificate.
- `test_every_check_is_covered_by_a_named_test` — every check the validator can emit is either
  named by a test or covered by a prefix assertion. It guards the *diagnosis*, not the outcome:
  without it a future check would arrive as a bare name instead of the sentence explaining why
  it matters.

---

## The netless half

Every assertion is a pure function over an envelope, so the daily suite exercises them
against the frozen frame sample **and against deliberately broken copies of it**
(`tests/data/signal_import/test_signal_feed_contract_validator.py`). The negative cases are
the point: a validator that has only ever seen a correct envelope is an assertion nobody has
watched fail. Each one pins a mistake this project actually made — a flag typed as a
timestamp, a loop over an empty set, a gate that read presence but never type.

See also: [signal_import_tests.md](../data/signal_import_tests.md) ·
[signal_data_source.md](../../data_pipeline/signal_data_source.md)
