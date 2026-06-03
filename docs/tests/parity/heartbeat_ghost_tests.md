# Heartbeat Ghost-Pass Parity Tests Documentation

## Overview

Validates the simulation-side decision ghost-pass (#360 Stage 2): the sim pipeline drives
ghost-passes in the simulated-time gap between two replayed data ticks, so an opt-in algo
(`wants_heartbeat()`) reacts between ticks at the same relative point as live.

**Location:** `tests/parity/test_heartbeat_ghost_parity.py`

Fully deterministic — no broker, no network. Three layers: the latency-resolution mechanism,
the driver cadence + gates, and the end-to-end loop wiring.

---

## What It Validates

| Class | Focus |
|-------|-------|
| `TestLatencyResolutionByMsc` | `OrderLatencySimulator.process_up_to_msc(msc)` resolves a queued order exactly when its `broker_fill_msc` is reached and removes it (the mechanism that lets a fill landing in a gap be resolved on a ghost-pass) |
| `TestSimHeartbeatDriver` | `_run_sim_heartbeats` fires one ghost-pass per `heartbeat_interval_ms` within a sub-threshold gap (9 passes for a 10 s gap at 1 s), fires **none** across a gap over `inter_tick_gap_threshold_s` (#208 weekend-gap correctness gate), and short-circuits when the algo requests session end |
| `TestSimGhostPassLoop` | End-to-end via `execute_tick_loop` with a real `TradeSimulator`: an opt-in decision fires the expected ghost-passes between two ticks; a **non-opt-in** decision fires **none** (hard gate); an over-threshold gap fires none |

---

## Key Mechanisms Tested

### Hard gate
The sim heartbeat path is completely absent unless `decision_logic.wants_heartbeat()` is True —
all current algos return False, so the loop is byte-for-byte unchanged for them (zero overhead,
zero behavior change). The test proves both the opt-in (fires) and non-opt-in (silent) paths.

### Weekend-gap correctness gate (#208)
No ghost-passes are synthesized across a gap longer than `inter_tick_gap_threshold_s` (default
300 s). Across a data/weekend gap the market data says nothing; firing 1/s heartbeats there would
fabricate activity (and explode the pass count). This is a correctness boundary, not a feature cap.

### Parity intent
The sim ghost-pass mirrors the live one (clock injection + cached worker results +
`execute_decision(tick=None)`), so a time-driven algo reacts at the same relative point in both
pipelines — the foundation the #294 matrix extends for a heartbeat algo.

---

## Fixtures

No shared fixtures. The latency test instantiates `OrderLatencySimulator` directly; the driver
test calls `_run_sim_heartbeats` with `MagicMock` collaborators; the loop test wires a real
`TradeSimulator` + a `MagicMock` orchestrator/decision into `execute_tick_loop`.
