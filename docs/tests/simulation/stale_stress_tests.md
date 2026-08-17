# Stale-Data Stress Tests (#436, #433)

## Purpose

Proves the planned stale-window stress rule (`stress_test_config.stale_data_stress`) drives
BOTH staleness contracts deterministically in the backtesting pipeline — and that neither
contract fires without a window (no false positives). The probe decision logic
(`CORE/backtesting/backtesting_outage_probe`) records every hook firing into the
cross-process `BacktestingMetadata.received_events` channel (the #348 event-probe pattern).

## Test Structure

```
tests/simulation/stale_stress/
├── test_stale_data_stress.py   ← 8 tests over one 5-scenario batch run (#436 contracts)
└── test_signal_resolution.py   ← 19 tests over one 6-scenario batch run (#433 counters)
```

Fixture set: `tests/fixtures/scenario_sets/stale_stress/stale_stress_probe.json` (§34) —
BTCUSD kraken_spot ticks (2026-04-27, dense) + the `crypto_sentiment` archive (10-min
snapshot cadence → a 60-min feed cut guarantees the 30-min staleness flip inside the window).
Events block DATA SOURCES the scenario binds (`data_source` = `data_broker_type` for the
tick source, `data_sentiment_type` for the signal source).

| Scenario | Stress | Asserts |
|----------|--------|---------|
| `BTCUSD_market_stress` | `kraken_spot` window 06:15→06:25 | `on_market_data_stale` fired exactly once (status-plane, edge); the probe's deliberate entry rejected by the OrderGuard (`stale_entry_rejected`); signal side untouched (cadence < threshold) |
| `BTCUSD_signal_stress` | `crypto_sentiment` window 06:10→07:10 | The carved series ages the resolved snapshot → the REAL #434 chain flips → `on_signal_stale` fired exactly once; `market_data_stale` NEVER fires (the live-only proof: no window → no dispatch in sim) |
| `BTCUSD_no_stress_control` | none | Zero staleness events — normal replay gaps produce no false positives |
| `BTCUSD_overlap_warning` | `kraken_spot` window disjoint from the data range | Overlap-guard warning `data deviation` in the scenario buffer; zero events; scenario still succeeds |
| `BTCUSD_unknown_source` | `nonexistent_feed` window | Scenario excluded at data preparation with a `ValidationError` naming the unknown source (§33 — the batch continues) |

## Signal resolution counters (#433 Part C)

The second suite asks a different question of the same machinery: not *did the contract fire*
but **what did the strategy actually decide on** — the per-tick `fresh` / `stale` / `blind`
counters every SIGNAL worker captures.

Fixture set: `tests/fixtures/scenario_sets/signal_resolution/signal_resolution_cases.json` (§34) —
the gap-free synthetic archive `crypto_sentiment_mock` (10-min cadence, zero gaps), so every
anomaly in a case is provably the carved one and never an archive artifact.

| Scenario | Stress | Asserts |
|----------|--------|---------|
| `clean` | none | every tick fresh — the control both other cases are compared against |
| `restart_short_gap` | 10-min carve (one snapshot lost) | **zero stale ticks**: the resulting 20-min hole stays under the 30-min threshold. Counter-identical to `clean` — an archive gap is not a stale run |
| `outage_2h` | 2-h carve, recovers inside the window | stale majority, fresh again after recovery, never blind (something always resolved) |
| `stale_tail` | carve reaching past the window | stale to the last tick, still never blind |
| `blind_head` | carve covering everything before the window | `blind > 0` then fresh — the only case that produces blind ticks |
| `tight_threshold` | **no carve**, `max_staleness_minutes: 5` | half the run stale from the parameter alone. Same data as `clean`, so it proves the counters measure data × parameter |

Structural invariant asserted for every case: `fresh + stale + blind == ticks_processed` — one
count per tick, no double count, no miss.

## Running

```bash
# Full suite
pytest tests/simulation/stale_stress/ -v

# Operator inspection with full logs + scenario summary
# launch.json: 🧪 Simulation: Stale-Data Stress (Probe)
# launch.json: 🧪 Simulation: Signal Resolution (Stress-Carved)
```

**Runtime:** ~9 seconds (two shared batch runs, `scope='module'`).

**Related docs:** [Stress Test System](../../stress_test.md) ·
[Signal Data Source](../../data_pipeline/signal_data_source.md) (source vs. decision basis) ·
[Live Outage Handling](../../user_guides/live_outage_handling_guide.md) ·
loop-side unit tests in [Loop Cadence Tests](../autotrader/loop_cadence_tests.md).
