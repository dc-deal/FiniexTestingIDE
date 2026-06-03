# Loop Cadence Tests Documentation

## Overview

The loop-cadence test suite validates the live-loop timer model (#360): the canonical
clock is injected (no longer frozen to the last tick), the idle heartbeat re-polls active
orders, and the decision runs a side-effect-free ghost-pass between ticks.

**Location:** `tests/autotrader/loop_cadence/`

All tests run offline (no network): the executor tests use `MockOrderExecution` /
`MockBrokerAdapter`, the orchestrator test uses a stub decision logic, and the cadence test
drives the pure `FieldStudyPhaseMachine` directly. The full integration (loop + executor +
ghost-pass) is validated by a real Field Study run (#332) on an illiquid window.

---

## Test Structure

```
tests/autotrader/loop_cadence/
├── test_clock_injection.py     ← executor clock: inject / advance / decoupled from tick
├── test_heartbeat_cadence.py   ← Part A re-poll on heartbeat + Part C process_heartbeat
└── test_ghost_phase_cadence.py ← phase outcome vs observation cadence (the #15 blocker)
```

---

## What Each File Validates

| File | Focus |
|------|-------|
| `test_clock_injection.py` | `get_current_time()` raises before any injection; `set_current_time()` is returned verbatim; `on_tick` sets the clock from the tick timestamp; a heartbeat injection **advances** the clock past the last tick while `get_current_price` stays at the last-known tick (clock decoupled from price) |
| `test_heartbeat_cadence.py` | **Part A:** `heartbeat()` schedules the active-order status poll (`in_flight_query` flips True) — the fill/cancel-confirm query now fires during idle, not only on a real tick. **Part C:** `WorkerOrchestrator.process_heartbeat()` returns `None` for a non-opt-in logic (compute never called), and for an opt-in logic calls `compute(tick=None, …)` with the **cached** `_worker_results` (workers are not recomputed) |
| `test_ghost_phase_cadence.py` | The `multi_cancel` phase **PASSES** when the cancel resolution is observed on a ghost-pass within the budget, and **FAILS** when it is only checkable at a far-future tick after the budget burned — the cause→effect behind the field-study `cancel-all not confirmed` blocker |

---

## Key Mechanisms Tested

### Clock injection (Part B)
The executor no longer derives `get_current_time()` from `_current_tick` (which froze
between ticks and jumped by the full gap). `on_tick` sets the clock from the tick; the loop
injects wall-clock on the idle heartbeat via `set_current_time`. Phase/op timeouts therefore
track real elapsed time, and the last-known price (`get_current_price`) stays independent.

### Heartbeat re-poll + ghost-pass (Parts A + C)
`heartbeat()` adds `_process_active_orders()` so the broker fill/cancel-confirm query is
issued during idle. `process_heartbeat()` runs the decision ghost-pass only for logics that
opt in via `wants_heartbeat()`, forwarding the cached worker results with `tick=None` — the
orchestrator never calls `worker.compute(None)`.

### Observation cadence determines the phase outcome
The `FieldStudyPhaseMachine` is a pure function of the observations fed to it. Frequent
ghost observations (#360) surface the cancel/fill resolution promptly → PASS; a single
far-future tick observation with the budget already burned → FAIL. This pins the #360 fix
at the machine level without a broker.

---

## Fixtures

No shared fixtures. Executor tests instantiate `MockOrderExecution` directly; the
orchestrator test builds a `process_heartbeat`-ready orchestrator via `object.__new__` with
a stub decision logic; the cadence test constructs a one-phase `FieldStudyPhaseMachine` and
feeds `PhaseContext` observations.
