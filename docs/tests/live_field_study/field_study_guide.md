# Live Field Study — Operator Guide (#332)

The Live Field Study is the **live acceptance gate**: an operator-driven, deterministic
phase sequence that drives the full live pipeline against real Kraken Spot (real money,
min-lot), records everything as analysis-ready JSONL, and produces a **PASS/FAIL
acceptance certificate**. It is the live equivalent of the plan-driven
`backtesting_margin_stress` decision logic and the production-readiness gate before a
release tag.

It is **operator-driven by design** — there is no pytest equivalent for the live run.
The exhaustive branch coverage lives in the mock tests; the Field Study proves the
realism subset (real timing, fills, fees, slippage, broker_ref) that mocks cannot.

> **Cost:** ~$0.08–0.20 per full run on Kraken ETHUSD at min-lot.

---

## Components

| Piece | Location |
|---|---|
| Decision logic | `python/framework/decision_logic/core/live_field_study/live_field_study.py` |
| Phase state machine | `.../live_field_study/field_study_phase_machine.py` |
| JSONL recorder | `python/framework/reporting/field_study_recorder.py` |
| Certificate analyzer | `python/framework/reporting/field_study_certificate.py` |
| Certificate CLI | `python/cli/field_study_certificate_cli.py` |
| Profiles | `configs/autotrader_profiles/field_study/kraken_spot_{ethusd,btcusd}_field_study.json` |
| Certificates (committed) | `tests/live_field_study/reports/` |

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
3. `dry_run = false` acknowledged (the profile runs live).
4. Recent **benchmark** + **live-adapter** certificates green.
5. `lot_size` in the profile matches the symbol's `volume_min`.

---

## Operator Workflow

1. **Launch** via launch.json → `🧪 AutoTrader: Field Study (Kraken Spot ETHUSD)`
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
| 14 | `multi_concurrent_limits` | 3× LIMIT | LONG | per-order throttle + in-flight isolation | all resting | far from market — submitted one per tick |
| 15 | `multi_cancel_all` | cancel all | — | multi-cancel correctness | all cancelled | — |
| 16 | `partial_close_test` | MARKET → 50% → rest | LONG | partial-close path end-to-end live | half, then flat | multi-step; lots-polling detects the partial |
| 17 | `idle_heartbeat_test` | IDLE | — | display pulse + heartbeat drain during a quiet period | pulse frame | no orders — wall-clock wait only |
| 18 | `force_close_all` | force-close | — | kill-switch / safety cleanup | account flat | cancels resting + closes positions |
| 19 | `final_summary` | session end | — | clean exit via `request_session_end` (#348) | session ends | no operator Ctrl+C needed |

**Cross-cutting behaviors:**
- **Safety integration** — submits route through the standard BUY/SELL decision action, so the safety circuit breaker can suppress new entries (override → FLAT); closes/cancels are never suppressed.
- **Budget / session guard** — the run self-aborts (cancel + close-all + graceful end) if realized cost breaches `max_session_cost_usd` or the wall-clock exceeds `session_timeout_s`.
- **Step mode** — `halt_after_phase: <phase_id>` ends the session cleanly after a named phase (for incremental, partial-cost dry runs).

## JSONL Schema

One JSON object per line, append-only, flushed per event (crash-safe, tail-able live).

**Line 1 — header:**
```json
{"record_kind":"header","schema_version":"1.0","started_utc":"...","profile":"...","symbol":"ETHUSD","release_target":"dev","phases":["market_long_open", ...]}
```

**Every event — stable core keys** (`ts_utc`, `seq`, `plane`, `event_type`, `phase`,
`phase_index`) plus per-event fields (`order_id`, `broker_ref`, `side`, `lots`, `price`,
`status`, `detected_via`) and typed sub-blocks (`slippage` #340, `reconcile` #151,
`api_perf` #351, `extra`). None fields are omitted.

- `plane` = `bot` (bot-observed via #348) or `broker_truth` (pulled from the broker).
  The two planes join on `phase` + `order_id`.
- `event_type` includes: `phase_start`, `order_filled`, `order_rejected`,
  `order_cancelled`, `partial_close`, `phase_result`, `broker_snapshot`,
  `reconcile_alert`, `api_perf`, `session_end`.

---

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
- every phase reached a non-failing outcome (`pass` / `expected_rejection` / `skipped`)
- no phase is missing a result (a missing result means the run aborted mid-sequence)
- **no resting orders at session end** (last broker-truth snapshot); balances restored ~to the start minus fees — the account holds base by design, so order-book flatness (not a zero base balance) is the gate

**Informational (not pass-gating):** realized cost, slippage distribution, detected-via
mix, reconciliation alert count.

Validate a committed certificate (CI-friendly, no live run):
```bash
pytest tests/live_field_study/test_field_study_certificate.py -v
```

---

## Sample Analysis Queries

Per-phase outcomes (`jq`):
```bash
jq -r 'select(.event_type=="phase_result") | "\(.phase)\t\(.status)"' field_study.jsonl
```

Total fees (`jq`):
```bash
jq '[.. | .commission? // empty] | add' field_study.jsonl
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
| Cost per min-lot round-trip | ~$0.008 | budget anchor |
| Full run cost | ~$0.08–0.20 | Kraken ETHUSD min-lot |

---

## Release Policy

The Field Study certificate is **mandatory for every MINOR (X.Y) release**. For PATCH
(X.Y.Z) releases it is required only if the live execution stack changed since the last
green certificate, or the certificate is expired (`valid_until`, 90 days). See the
Release Checklist.
