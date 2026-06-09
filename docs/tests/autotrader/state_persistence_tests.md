# Algo State Persistence Tests Documentation

## Overview

This suite validates restart-safe algo memory (#354, Category B): the `AlgoStateStore`
(atomic JSON persistence keyed by bot identity, hybrid save cadence, corrupt + staleness
policies), the snapshot serializability pre-flight check, and its centralized integration into
the batch `RequirementsCollector`.

**Location:** `tests/autotrader/state_persistence/`

All tests run offline. The store is decoupled from the decision logic — it persists plain
dicts — so the store tests use a tmp directory and crafted files directly; the pre-flight test
uses a minimal decision-logic stub; the collector test registers test-double logics through the
factory's public `register_logic()`. The full live lifecycle (restore before first decision,
save on shutdown) is exercised by a real AutoTrader run, not by this unit suite.

---

## Test Structure

```
tests/autotrader/state_persistence/
├── conftest.py                  ← StubDecisionLogic, RecordingLogger, store_config (tmp dir)
├── test_algo_state_store.py     ← persistence, cadence, corrupt + staleness, weekend-aware
├── test_state_preflight.py      ← snapshot serializability + opt-in bypass
└── test_collector_preflight.py  ← centralized batch pre-flight: ValidationResult + caching + mixed
```

## What Is Covered

**Store (`test_algo_state_store.py`):**
- Round-trip — `save` then `load` returns the same snapshot plus a populated `RestoreContext`.
- Empty snapshot writes no file (silent bypass, no carcass).
- Atomic write — no `.tmp` lingers; the result is valid JSON with the schema envelope.
- Cadence — `is_due` fires on the tick threshold and resets after a save.
- Corrupt policy — `warn_reset` returns `None` + warns; `fail` raises `StatePersistenceError`.
- Stale policy — a backdated file (>`max_age_trading_days`) under `warn_reset` returns `None` +
  warns; under `halt` raises; a within-age file loads.
- Identity mismatch — a file from a different profile/symbol is ignored, not treated as corrupt.
- Weekend-aware staleness — `weekend_aware=True` counts a Fri→Mon span as 1 trading day;
  `weekend_aware=False` (crypto) counts 3 calendar days (the store's `datetime.now` is frozen to a
  known Monday for determinism).

**Pre-flight (`test_state_preflight.py`):**
- Opt-out is a no-op — an algo that does not opt in is never checked.
- A serializable snapshot passes.
- A non-serializable value raises, naming the offending key and the logic.

**Collector integration (`test_collector_preflight.py`):** the centralized batch pre-flight in
`RequirementsCollector._state_snapshot_preflight`, with test-double logics registered via
`register_logic()`.
- Clean opt-in logic → passes (returns None).
- Broken opt-in logic → flagged (error names the offending key) → scenario would be excluded.
- Opt-out logic with a broken snapshot → ignored.
- No `decision_logic_type` → no-op.
- Cached per distinct `(decision_logic_type, config)` — the logic is instantiated once across
  repeated scenarios (single-logic set checked once).
- Mixed set — clean passes and broken is flagged independently, both retained as distinct cache
  entries.

## Running

```
🧩 Pytest: Algo State Persistence (All)   # launch.json
pytest tests/autotrader/state_persistence/ -v
```

The suite is auto-marked `autotrader` + `unit` by path (`tests/conftest.py`) — no marks in the
test files.
