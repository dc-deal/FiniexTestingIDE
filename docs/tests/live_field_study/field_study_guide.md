# Live Field Study — Operator Guide (#332)

The Live Field Study is the **real-money acceptance gate**: an operator-driven, deterministic
phase sequence that drives the full AutoTrader pipeline against real Kraken Spot (real money,
min-lot), records everything as analysis-ready JSONL, and produces a **PASS/FAIL
acceptance certificate**. It is the real-money equivalent of the plan-driven
`margin_stress_probe` decision logic and the production-readiness gate before a
release tag.

It is **operator-driven by design** — there is no pytest equivalent for the real-money session.
The exhaustive branch coverage lives in the mock tests; the Field Study proves the
realism subset (real timing, fills, fees, slippage, broker_ref) that mocks cannot.

> **Cost:** ~$0.50 per full run on Kraken ETHUSD at min-lot (measured 2026-10-07; the budget ceiling
> `max_session_cost_usd` is 1.00). The older ~$0.08–0.20 is stale — see the re-measurement below.

---

## Components

| Piece | Location |
|---|---|
| Decision logic | `python/framework/decision_logic/core/live_field_study/live_field_study.py` |
| Phase state machine | `.../live_field_study/field_study_phase_machine.py` |
| JSONL recorder (the study's phases) | `python/framework/reporting/field_study_recorder.py` |
| Stream projection (its order and venue lines) | `python/framework/reporting/field_study_stream_projection.py` |
| Session wiring + preflight | `python/framework/autotrader/field_study_setup.py` |
| Certificate analyzer | `python/framework/reporting/certificates/field_study_certificate.py` |
| Certificate CLI | `python/cli/field_study_certificate_cli.py` |
| Profiles | `configs/autotrader_profiles/field_study/kraken_spot_{ethusd,btcusd}_field_study.json` |
| Certificates (committed) | `tests/live_field_study/reports/` |
| Offline tests | `tests/framework/field_study_recorder/` (recorder, projection, analyzer) · `tests/autotrader/field_study_machine/` · `tests/autotrader/integration/test_field_study_capture.py` |

---

## Pre-Flight Checklist

1. Credentials present; account balance above the profile's `min_balance`.
2. **No resting broker orders + account funded ~50/50 base/quote.** The Field Study
   sells *held* base in the SELL phases, so the account must hold base (e.g. ~50% ETH /
   50% USD). Startup verifies via broker truth-pull that **no resting orders** are present
   and records the starting balances; it aborts loudly only on resting orders (a non-quote
   balance is expected, not a contaminant). At the end the account returns to ~equilibrium
   minus fees. Security-guard behavior (rejections, circuit breaker) is **not** tested here
   — that is the separate security-component certification (#358).
3. `dry_run = false` acknowledged (the profile places real orders).
4. Recent **benchmark** + **live-adapter** certificates green.
5. `lot_size` in the profile matches the symbol's `volume_min`.

---

## Operator Workflow

1. **Launch** via launch.json → `🧪 AutoTrader: Field Study (Kraken Spot ETHUSD) - real money`
   (`--display --delay 1`).
2. **Observe**: phase indicator, real-time JSONL, drift/slippage audit footer (#327/#340),
   reconcile status line (#151), API performance panel (#351).
3. Phases run sequentially (see the Phase Sequence table below). LIMIT phases re-arm
   toward the market until filled (bounded by `max_rearm_attempts` + `max_session_cost_usd`).
4. Phase 17 idle: verify the `💓 Ns since last tick` heartbeat pulse.
5. Phase 18 force-close-all; phase 19 ends the session cleanly via `request_session_end`.
6. **Post-run**: generate the certificate from the JSONL (below).

The run **self-aborts** (cancel + close-all + graceful session end) if the realized
cost breaches `max_session_cost_usd` or the wall-clock `session_timeout_s` is exceeded.

---

## Phase Sequence

Phases are config (`phase_sequence` in the profile) — the engine is generic. Each phase is
`enabled`-toggleable and auto-skips when the broker lacks a required capability.

| # | Phase ID | Type | Side | Why / what it proves | Expected outcome | Notes |
|---|---|---|---|---|---|---|
| 1 | `market_long_open` | MARKET | LONG | async submit, polling, fill detection, slippage (#340) | filled | position opens |
| 2 | `market_long_close` | CLOSE_ALL | — | close path + cleanup | flat | — |
| 3 | `market_short_open` | MARKET | SHORT | spot SELL of held base (account funded ~50/50) | filled | sells base → quote; no rejection (base held) |
| 4 | `market_short_close` | CLOSE_ALL | — | buy back (restore) | flat | closing the short buys the base back |
| 5 | `reject_below_min` | MARKET | LONG | lot < `volume_min` → INVALID_LOT | rejection | strict — a fill here **fails** |
| 6 | `reject_oversized` | MARKET | LONG | lot ≫ balance → INSUFFICIENT_FUNDS | rejection | strict — a fill here **fails** |
| 7 | `limit_long_near_price` | LIMIT | LONG | resting BUY below market, #320 throttle, fill via poll | filled within budget | **re-arm-until-fill** (re-prices toward market) |
| 8 | `limit_long_close` | CLOSE_ALL | — | cleanup | flat | — |
| 9 | `limit_short_near_price` | LIMIT | SHORT | resting SELL above market, re-arm | filled within budget | **re-arm-until-fill**; sells held base |
| 10 | `limit_short_close` | CLOSE_ALL | — | buy back (restore) | flat | — |
| 11 | `limit_modify_test` | LIMIT + modify | LONG | AmendOrder in-place (txid stable), modify toward market | modified, filled | rests far, then modifies closer |
| 12 | `limit_modify_close` | CLOSE_ALL | — | cleanup | flat | — |
| 13 | `limit_cancel_test` | LIMIT + cancel | LONG | cancel before fill, no position created | cancelled | rests, then cancels |
| 14 | `stop_cancel_test` | STOP + cancel | LONG | the live STOP path (#500) — trigger on the wire, resting in the STOP world, cancelled there | cancelled | rests 3% ABOVE market, own `lots`; **a fill here fails** |
| 15 | `multi_concurrent_limits` | 3× LIMIT | LONG | per-order throttle + in-flight isolation | all resting | far from market — submitted one per tick |
| 16 | `multi_cancel_all` | cancel all | — | multi-cancel correctness | all cancelled | — |
| 17 | `partial_close_test` | MARKET → 50% → rest | LONG | partial-close path end-to-end at the venue | half, then flat | multi-step; lots-polling detects the partial |
| 18 | `idle_heartbeat_test` | IDLE | — | display pulse + heartbeat drain during a quiet period | pulse frame | no orders — wall-clock wait only |
| 19 | `force_close_all` | force-close | — | kill-switch / safety cleanup | account flat | cancels resting + closes positions |
| 20 | `final_summary` | session end | — | clean exit via `request_session_end` (#348) | session ends | no operator Ctrl+C needed |

### Why phase 14 exists, and why it costs nothing

The live STOP path was built in #500, and four of its layers cannot be proven by a mock: the
trigger mapping on the wire (Kraken's `price` field carries a stop's TRIGGER, not a limit — and
there is no `stopprice` request parameter, that name appears only in the responses), the
submit-response route that confirms the broker reference, the poll loop reading the stop world,
and the cancel path finding the order THERE rather than among the limits. A resting order
exercises all four, and a resting order that is cancelled pays no fee.

Two properties of the phase are deliberate. It rests **above** the market for a LONG, the mirror
of a resting buy limit — a stop is a breakout entry, so the side that makes a limit rest is the
side that makes a stop fire. And a **fill FAILS the phase**: Kraken does not refuse a mis-sided
trigger, it executes it immediately as a market order (our simulation does the same), so a fill
here means the offset put the order on the wrong side and the phase would otherwise report an
unintended real trade as a success.

Both of its numbers therefore differ from the limit phases, and neither is a typo. A buy stop's
order value is `lots × TRIGGER`, so at the default 0.002 with the limit phases' 0.4% offset it
lands on about **$5.00 — exactly Kraken's cost minimum**, and a small downward move fails the
phase for arithmetic rather than for a defect (measured 2026-09-07: `EOrder:Cost minimum not
met`). It carries its own `lots: 0.004` and a **3%** offset: ~$10 briefly reserved, no fill, and
far enough away that a thirty-second window cannot reach it. A limit phase wants to fill and
re-arms toward the market; this one wants the opposite.

**Cross-cutting behaviors:**
- **Safety integration** — submits route through the standard BUY/SELL decision action, so the safety circuit breaker can suppress new entries (override → FLAT); closes/cancels are never suppressed.
- **Budget / session guard** — the run self-aborts (cancel + close-all + graceful end) if realized
  cost breaches `max_session_cost_usd` or the wall-clock exceeds `session_timeout_s`. The COST half
  of that guard only became effective with #506: `OrderResult.commission` was a literal `0.0` before
  it, so every certificate up to 2026-09-08 records `realized_cost = 0` — because the field could
  not carry a figure, not because the run was free. The guard reads the portfolio's fee total,
  which books every fee through one site — both legs of a round trip, a full close included.
- **Step mode** — `halt_after_phase: <phase_id>` ends the session cleanly after a named phase (for incremental runs that spend only part of the budget).

## JSONL Schema

One JSON object per line, append-only, flushed per event (crash-safe, tail-able live).

**Two writers, one file.** The study writes its own choreography — the header, every phase's start
and result, the REST telemetry at the end, the session-end marker. Its order and venue lines are not
its own: they are the live core's order-event stream (`io/order_events.jsonl`), copied in while the
executor writes it. So the capture and the session's record cannot disagree about an order, and a
line can be joined back to the stream by `extra.stream_seq`.

**Line 1 — header:**
```json
{"record_kind":"header","schema_version":"2.0","started_utc":"...","profile":"...","symbol":"ETHUSD","phases":["market_long_open", ...]}
```

**Every event — stable core keys** (`ts_utc`, `seq`, `plane`, `event_type`, `phase`,
`phase_index`) plus per-event fields (`order_id`, `side`, `lots`, `price`, `status`,
`detected_via`) and typed sub-blocks (`slippage`, `reconcile`, `api_perf`, `extra`). None fields are
omitted.

- `plane` = `bot` (the study's phases and its orders) or `broker_truth` (what the venue answered).
  An order line joins the session's order-event stream by `extra.stream_seq`; a broker-truth line
  carries no `order_id`, so the planes do not join on one.
- **An order line names the phase that SUBMITTED its order**, not the phase that was running when
  the answer came. A fill that arrives after its phase passed still carries its own phase.
  An order the framework places on the study's behalf — a protective order once an entry fills, or a
  close held back until the venue confirms the protective order is gone — names the phase that
  submitted IT, which can be a later one than its entry's. A close shares its position's `order_id`;
  a protective order takes a fresh number from the order counter (`protect_pos_ethusd_41` protects
  `pos_ethusd_40`), so its entry is found through the stream row its `extra.stream_seq` points at,
  which carries the `position_id`.
- `event_type` includes: `phase_start`, `order_filled`, `order_rejected`, `order_unaccounted`,
  `order_cancelled`, `partial_close`, `phase_result`, `broker_snapshot`, `reconcile_alert`,
  `api_perf`, `session_end`.
  - A **full close** is an `order_filled` line with `extra.action: close`; a close that leaves lots is
    a `partial_close` with `extra.remaining_lots`.
  - A refusal made **before sending** — the rejection battery's lot-size refusals — is an
    `order_rejected` with status `denied` and its reason.
  - `order_rejected` and `order_cancelled` carry the order's own status — `denied` and `undelivered`
    beside `rejected`, `expired` beside `cancelled`.
- Every order line's `extra` carries `action`, `order_type` and `stream_seq`, and `submitted_seq`
  where the order was submitted — a refusal made before sending (`denied`) never was, so it has none.
  An ending adds who ended the order and why (`initiator`, `end_reason`); a fill adds its `fee` and
  `fee_currency`.
- A fill's `slippage` block carries what the fill is measured against — the values, not the
  measurement: `order_type`, `side`, `measured_against` (`submission_mid`, `limit_price` or
  `trigger_price`) and `reference_price`. A market order is measured against the mid when it was
  submitted, a limit against its own limit, a stop against its trigger. The certificate computes the
  figure.
- `broker_snapshot` is the venue's read at the session's start (phase `preflight`) and at its end
  (phase `session_end`): `extra.order_count`, the unfiltered `extra.balances` (`null` when the read
  gave up — never `{}`) and `extra.unread_parts`. A read whose order book could not be read has
  status `unread` and no count.
- `reconcile_alert` is written when the reconciliation picture CHANGED — and at most once per
  `broker_truth_min_interval_seconds` (300 s by default), so a divergence that opens and closes
  inside that window is not written: status `divergent`, with the divergence by identity in
  `reconcile`, or `clean` once it ended.
- `reconcile_summary` is written once, at the end: whether the session reconciled (`enabled`), its
  `cycles`, its `skipped` cycles and the `divergences_seen` — summed over every cycle, so one
  divergence counts once per cycle it stood. It is what tells a clean session from one that never
  compared.
- `api_perf` is written once, at the end, where the REST monitor ran: per endpoint its calls, latency
  and errors, with the session's slow calls and errors.

---

## Which profile the certificate is about

There are two field-study profiles and only ONE of them is on the release path:

| Profile | On the release path | Why it exists |
|---|---|---|
| `kraken_spot_ethusd_field_study.json` | **yes** | #332 names it primary, the release checklist runs it by name, and it is the only one with a launch entry |
| `kraken_spot_btcusd_field_study.json` | **no** | a second PAIR, run by hand |

The second profile is not redundancy. Venue behaviour is per **pair**, not per adapter — lot
minimum, tick size, decimals, liquidity — so a certificate over ETHUSD certifies ETHUSD and
nothing else. Exactly three values differ from the primary: the symbol, `lot_size` (0.0001 BTC
meets Kraken's ~$5 order minimum at that unit price where ETH needs 0.002), and
`max_rearm_attempts` (6 against 0, which means *unbounded* — the primary re-prices toward market
until the limit phases fill, so they end conclusive rather than timing out).

> ⚠️ **`--latest` picks the newest run by TIMESTAMP, not by profile.** A BTCUSD run started after
> an ETHUSD one becomes the certified run. The certificate records `profile` and `symbol` and
> prints both at generation, so the evidence is right there — but nothing asserts which one was
> expected. **Read the profile line the generator prints before committing the certificate**, or
> pass `--jsonl` explicitly.

## Certificate

After the run, generate the PASS/FAIL certificate:

```bash
python python/cli/field_study_certificate_cli.py generate --latest --release-version X.Y.Z
# or: --jsonl <path/to/field_study.jsonl>  [--comment "..."]
```

The certificate is written to `tests/live_field_study/reports/field_study_report_<version>_<ts>.json`
(mirrors the benchmark / live-adapter certificate conventions: `release_version`,
`git_commit`, `timestamp`, `valid_until`).

**PASS criteria (hard):**
- every phase reached a non-failing outcome (`pass` / `expected_rejection` / `skipped` /
  `inconclusive` — the last a market-dependent non-fill, not a mechanical failure)
- no phase is missing a result (a missing result means the run aborted mid-sequence)
- **no resting orders at session end** — read from the broker-truth snapshot of the `session_end`
  PHASE, never simply the last one recorded: a session-end snapshot that failed to be written would
  otherwise let the PREFLIGHT state answer the gate. A session-end read that could not see the order
  book (status `unread`, no count) does not pass either: the gate needs a counted, empty book. The account holds base by design, so order-book
  flatness (not a zero base balance) is the criterion; what the account actually MOVED is the
  certificate's `account_delta`, derived from the two snapshots rather than asserted in prose

**Informational (not pass-gating)** — each says where it came from, or that it is missing, never a
silent zero:
- `fees_charged` — what the run booked, read from the run's own report beside the capture
  (`io/run_summary.json`, source `run_record`); where that report is gone, no longer reads or names
  no single currency, the sum of the fill
  lines' fees (source `event_sum`). Both are our own booking — neither can show a fee we computed
  wrong — so the certificate needs the report: generate it before the run directory is pruned.
- `account_delta` — what the venue's account moved between the preflight and the session-end
  snapshot: the one figure that can contradict our booking.
- `slippage` — per order type, positive is adverse: a market order against the mid when it was
  submitted, a limit against its own limit (`vs_limit_max_pct`), a stop against its trigger. A fill
  whose reference was not captured is a leg, not a measurement; `status` says `measured`,
  `partly_measured` or `not_measured` — the last for every capture from before #566.
- `api_calls` — the session's REST calls, errors and slow calls, from the telemetry written at its
  end.
- `reconciliation` — `checked`, `partly_skipped`, `skipped`, `not_run` or `disabled`, with the
  cycles, skipped cycles and divergences seen, and the divergent pictures the capture holds;
  `not_recorded` for a capture from before #566. Never a bare zero: a session that did not compare
  says so.
- the detected-via mix.

Validate a committed certificate (CI-friendly, no real-money session):
```bash
pytest tests/live_field_study/test_field_study_certificate.py -v
```

---

## Sample Analysis Queries

Per-phase outcomes (`jq`):
```bash
jq -r 'select(.event_type=="phase_result") | "\(.phase)\t\(.status)"' field_study.jsonl
```

Fees of the fills (`jq`):
```bash
jq -s '[.[] | select(.event_type=="order_filled" or .event_type=="partial_close") | .extra.fee // 0] | add' field_study.jsonl
```

An order line back to its full record in the order-event stream (`jq`):
```bash
jq -r 'select(.extra.stream_seq != null) | "\(.extra.stream_seq)\t\(.phase)\t\(.event_type)"' field_study.jsonl
```

Two-plane merge (pandas):
```python
import pandas as pd, json
rows = [json.loads(l) for l in open('field_study.jsonl')][1:]
df = pd.DataFrame(rows)
bot = df[df.plane == 'bot']
truth = df[df.plane == 'broker_truth']
```

---

## V1.3 Pilot Reference Data (2026-05-21)

| Data Point | Value | Note |
|---|---|---|
| Submit-to-trades-query latency | ~2000 ms | polling-only baseline; #331 push → sub-second |
| Sub-threshold FEE drift | ~0.04 % | float rounding, Tier-0 ETHUSD |
| Cost per min-lot round-trip | ~$0.008 | **stale — pre-#506 booking, half the rate and one leg** |
| Full run cost | ~$0.08–0.20 | **stale — see below** |

**Re-measured 2026-09-08 against the venue's own charges** (probe:
`python/experiments/venue_probes/probe_kraken_charged_vs_booked.py`), twice — before and after
#506:

| 20-phase run | booked | venue charged | account moved | ratio |
|---|---|---|---|---|
| before #506 | $0.0841 | $0.3761 | — (not recorded) | **4.47 x** |
| after #506 | **$0.1820** | **$0.3542** | **ZUSD −$0.3584** | **1.95 x** |

The first factor was two independent causes multiplying: the exit leg was never booked (#506,
fixed) and the declared rates are half the account's real tier (#337, open — measured taker
0.8000 % / maker 0.4000 %). With the leg count corrected the remaining factor is the rate
alone, and it is almost exactly 2.

The account figure reconciles to the cent: charged $0.35424 plus the run's gross P&L of
−$0.00416 is the $0.3584 the quote balance moved. **A min-lot round trip costs about $0.07 at
the venue**, not the $0.008 the pre-#506 anchor claimed.

`max_session_cost_usd` was **re-chosen to 1.00** from this measurement (both field-study
profiles). It is checked against OUR booking, so it moves with #337: at the old 0.5 a post-#337
run would sit at 71 % of the ceiling and one re-armed limit phase could self-abort a release
gate with no defect present. At 1.00 a normal run sits at 35 % and the brake still trips at
2.8x a normal run.

---

## Release Policy

The Field Study certificate is **mandatory for every MINOR (X.Y) release**. For PATCH
(X.Y.Z) releases it is required only if the live execution stack changed since the last
green certificate, or the certificate is expired (`valid_until`, 90 days). See the
Release Checklist.
