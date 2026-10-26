# Loop Cadence Tests Documentation

## Overview

The loop-cadence test suite validates the AutoTrader loop's timer model (#360): the canonical
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
├── test_clock_injection.py         ← executor clock: inject / advance / decoupled from tick
├── test_heartbeat_cadence.py       ← Part A re-poll on heartbeat + Part C process_heartbeat
├── test_ghost_phase_cadence.py     ← phase outcome vs observation cadence (the #15 blocker)
├── test_market_data_staleness.py   ← #436/#444: loop evaluation, guard block, mandatory hook, planned windows
├── test_reconcile_handoff.py       ← #355 Phase 1: the loop hands attributions to the executor
├── test_sealed_day_booking.py      ← #476: a sealed day reaches the ledger, then the carry-over
├── test_session_log_rotator.py     ← #476: one session log file per trading day
└── test_session_log_retention.py   ← #357: the rotated files that are removed, and the ones that stay
```

---

## What Each File Validates

| File | Focus |
|------|-------|
| `test_clock_injection.py` | `get_current_time()` raises before any injection; `set_current_time()` is returned verbatim; `on_tick` sets the clock from the tick timestamp; a heartbeat injection **advances** the clock past the last tick while `get_current_price` stays at the last-known tick (clock decoupled from price) |
| `test_heartbeat_cadence.py` | **Part A:** `heartbeat()` schedules the active-order status poll (`in_flight_query` flips True) — the fill/cancel-confirm query now fires during idle, not only on a real tick. **Part C:** `WorkerOrchestrator.process_heartbeat()` returns `None` for a non-opt-in logic (compute never called), and for an opt-in logic calls `compute(tick=None, …)` with the **cached** `_worker_results` (workers are not recomputed) |
| `test_ghost_phase_cadence.py` | The `multi_cancel` phase **PASSES** when the cancel resolution is observed on a ghost-pass within the budget, and **FAILS** when it is only checkable at a far-future tick after the budget burned — the cause→effect behind the field-study `cancel-all not confirmed` blocker |
| `test_reconcile_handoff.py` | **#355 Phase 1, 5 tests:** the loop is the seam between the two halves of the client-key feature — the Reconciler DETECTS (read-only by contract), the executor WRITES. Asserts that what a cycle matched reaches `apply_order_attributions` verbatim, that a clean cycle hands over nothing at all (an empty call would make a non-event look like a repair), that a not-due cadence pulls nothing, that a session without a Reconciler is a no-op (every mock run takes that path), and that the tick counter reaches the Reconciler. Built via `__new__` with two collaborators — the method needs no queue, workers or decision logic |
| `test_market_data_staleness.py` | **#436 + #444 contract:** heartbeat evaluation flips ONCE per episode (edge), keeps the readable status growing (escalation input), stays inert when disabled/pre-tick; recovery resets the edge + logs the from–to span; the operator alert (#235) is one episode — stale once, fresh again only after the data stayed fresh for the quiet period — and a first tick that never comes is alerted once (the status stays a startup wait) and ended by that tick; reconnect deltas reach the pot, and the status after a recovery counts the outage that just ended (the tick source counts it at the recovering trade, #609). OrderGuard rejects new entries while stale (`STALE_MARKET_DATA`), respects the disable flag, cooldown regression intact. The mandatory `on_market_data_stale` override is startup-validated (missing → ValueError). `stale_data_stress` config parses/validates hard (`stale_data_source` required, window order, per-source filtering); the `StaleDataStressDriver` runs the full window lifecycle (entry edge, no re-fire, recovery span, skipped windows, `finish()`), and the overlap guard warns `data deviation` on disjoint windows. **#444 adds the AutoTrader loop's own half:** a planned window flips the status with no wall-clock trigger, ticks keep flowing inside it without ending it, the heartbeat evaluation is silent while it runs and works again once it is over, and the episode is recorded as INJECTED rather than as a venue fault. The shared `build_stale_stress_driver` is pinned separately: only this tick source's events reach the driver, a signal-only or disabled config builds nothing, no data range means no driver, and the overlap guard judges every plane rather than only the one being built |
| `test_sealed_day_booking.py` | **#476:** at each trading day's seal the loop hands every period sealed so far to its booker — once per seal, never for the session's end, which the report coordinator books — and writes the carry-over right after the ledger, so a session killed between the two books the same number again rather than leaving one unbooked; the session log buffer lets go of the sealed day's ordinary lines and keeps its warnings — its logger shows INFO as a session's does, and a precondition holds that the INFO line was buffered, because under the suite's own configuration no INFO line reaches the buffer and the test would pass with nothing let go of. A check that seals two days at once — a feed silent for a day — books them in one write and sends the operator the first day in full and the rest in one message. A boundary an idle heartbeat notices (a faked wall clock just after midnight) is sealed at midnight and booked before the session log rotates and before the heartbeat resolves anything. Built on the booking-day harness the parity test uses (`tests/shared/booking_day_harness.py`) |
| `test_session_log_rotator.py` | **#476:** the first pass names the file for the trading day the canonical clock stands in, a second pass on the same day opens no new file, a pass on the next trading day opens the next file and says so in the old one; nothing rotates before the clock is set, a session without a run directory writes no file, and a session whose log files are switched off opens no daily log (#621) |
| `test_session_log_retention.py` | **#357:** rotated logs older than the window go and the boundary day stays; the active file, a file dated after the current day and every file whose name the rule was not written for are never removed; `0` keeps everything; the file the rotator writes through the run's declaration is the one it removes, and every file it removes is the declared day log; a removal is reported with the names, nothing removed says nothing, and a file that cannot be removed reaches the error pot |

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

## Fixtures (conftest.py)

No shared fixtures. Executor tests instantiate `MockOrderExecution` directly; the
orchestrator test builds a `process_heartbeat`-ready orchestrator via `object.__new__` with
a stub decision logic; the cadence test constructs a one-phase `FieldStudyPhaseMachine` and
feeds `PhaseContext` observations.
