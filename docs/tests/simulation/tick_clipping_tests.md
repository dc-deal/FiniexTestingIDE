# Tick Clipping — Bar Rendering Regression Tests

## Overview

Regression suite for the bar rendering / clipping gate ordering in the simulation tick loop. Ensures that bars aggregate **all** ticks regardless of the `is_clipped` flag set by the tick processing budget.

**Context:** The tick processing budget flags ticks as `is_clipped=True` to simulate "algo was too slow to react". Clipped ticks skip the algo path (workers, decision logic) but must still reach bar rendering — otherwise OHLC, volume and tick_count are silently wrong. Bars represent market data, not algo input.

**Location:** `tests/simulation/tick_clipping/`

**Approach:** Lightweight integration test against `execute_tick_loop`. Uses a real `BarRenderingController` with mocked trade executor, worker orchestrator, and decision logic — isolates the tick loop ordering from the rest of the simulation pipeline.

---

## Tests

### `test_volume_aggregation_includes_clipped_ticks`

Feeds 5 synthetic ticks (mix of clipped + non-clipped) into the tick loop and verifies:
- `bar.volume` == sum of **all** tick volumes (not only non-clipped)
- `bar.tick_count` == total tick count (not only non-clipped)
- Broker path (`trade_simulator.on_tick`) called for every tick
- Algo path (`worker_coordinator.process_tick`) called only for non-clipped ticks

### `test_ohlc_reflects_extrema_from_clipped_ticks`

Constructs a tick sequence where the extreme high price is on a **clipped** tick. Verifies that `bar.high` captures that price — i.e. bar rendering must not skip clipped ticks.

---

## Why This Test Exists

Before the fix, `process_tick_loop.py` placed the clipping gate **before** bar rendering:

```
for tick in ticks:
    trade_simulator.on_tick(tick)      # all ticks
    if tick.is_clipped: continue       # ← gate
    bar_rendering_controller...        # ← only non-clipped (WRONG)
```

This caused every volume-aware worker (OBV, VWAP, volume-weighted indicators) to read systematically biased data. The fix aligns the simulation loop with the AutoTrader loop, which always processed all ticks through bar rendering.

The tests guard against regression of the ordering.

---

## Running

```
pytest tests/simulation/tick_clipping/ -v
```

Or via VS Code: `🧩 Pytest: Tick Clipping Bar Regression (All)`.
