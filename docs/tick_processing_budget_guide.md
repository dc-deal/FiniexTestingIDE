# Tick Processing Budget — Deterministic Clipping Simulation

---

## Overview

In live trading, ticks that arrive while the algorithm is still processing the previous tick are **lost** (clipped). The backtesting simulation processes every tick sequentially — optimistically biased because no ticks are ever skipped.

The **Tick Processing Budget** bridges this gap by deterministically **flagging** ticks as clipped, simulating the clipping behavior of live trading. Flagged ticks still flow through the tick loop — the broker path (`trade_simulator.on_tick()`) sees every tick, while the algo path (workers, decision logic) skips clipped ticks.

**Key property:** Flag-based approach. All ticks enter the subprocess and tick loop. The `is_clipped` flag on each tick controls whether the algo path processes it. This ensures the broker simulation (pending order fills, SL/TP triggers, limit/stop monitoring) operates on the full market data stream — identical to a real broker.

---

## How It Works

### Virtual Clock Algorithm

```
budget_ms = configured tick_processing_budget_ms
virtual_clock = 0

for each tick (ordered by collected_msc):
    if tick.collected_msc >= virtual_clock:
        FLAG tick as is_clipped = False (algo processes it)
        virtual_clock = tick.collected_msc + budget_ms
    else:
        FLAG tick as is_clipped = True (algo skips it, broker still sees it)
```

- **O(n) single pass** — no sorting, no lookback
- Deterministic: same budget + same data = same result every time
- Uses `collected_msc` (device-side collection timestamp, available since V1.3.0)
- All ticks returned — flags control processing, not removal

### Example

Budget = 2.0ms, ticks at: 1000, 1001, 1002, 1003, 1005, 1008

```
Tick 1000ms: collected_msc(1000) >= virtual_clock(0)    → is_clipped=False, clock = 1002
Tick 1001ms: collected_msc(1001) <  virtual_clock(1002)  → is_clipped=True
Tick 1002ms: collected_msc(1002) >= virtual_clock(1002)  → is_clipped=False, clock = 1004
Tick 1003ms: collected_msc(1003) <  virtual_clock(1004)  → is_clipped=True
Tick 1005ms: collected_msc(1005) >= virtual_clock(1004)  → is_clipped=False, clock = 1007
Tick 1008ms: collected_msc(1008) >= virtual_clock(1007)  → is_clipped=False, clock = 1010
```

Result: 6 ticks total, 4 algo (non-clipped), 2 clipped (33.3%). All 6 ticks enter the tick loop.

---

## Configuration

### Setting the Budget

The budget is configured via `execution_config.tick_processing_budget_ms` and cascades through the standard 3-level hierarchy (app → global → scenario):

**App-level default** (`configs/app_config.json`):
```json
"default_scenario_execution_config": {
    "tick_processing_budget_ms": 0.0
}
```

**Scenario Set — global** (applies to all scenarios):
```json
{
    "global": {
        "execution_config": {
            "tick_processing_budget_ms": 1.5
        }
    }
}
```

**Scenario Set — per scenario** (overrides global):
```json
{
    "scenarios": [{
        "execution_config": {
            "tick_processing_budget_ms": 2.5
        }
    }]
}
```

### Default: Disabled

`tick_processing_budget_ms: 0.0` (or absent) = no flagging, no clipping stats, no report section. All ticks processed by both broker and algo paths.

---

## Data Granularity Limitation

`collected_msc` is stored as **integer milliseconds** (int64). The minimum time difference between consecutive ticks is 1ms.

**Consequence:** A budget below 1.0ms has no effect — every tick passes the virtual clock gate because the next tick is at least 1ms later.

The system warns about this:
- In **Profiling Analysis**: yellow warning when budget < 1.0ms and 0 ticks clipped
- In **Warnings & Notices**: `"Tick processing budget (Xms) below data granularity"`

### Data Source Specifics

| Source | `collected_msc` Origin | Minimum Spacing |
|--------|----------------------|-----------------|
| MT5 (V1.3.0+) | Device clock at collection time | Typically 100ms+ (interpolated within seconds) |
| Kraken (V1.3.0+) | Synthesized from trade fills | 1ms (synthetic, real cadence unknown) |
| Pre-V1.3.0 data | Not available (`collected_msc = 0`) | Filtering skipped, warning logged |

---

## Report Output

When the budget is active, the following sections appear in the batch summary:

### Per-Scenario (Profiling Analysis)

```
Ticks: 3,362 / 5,000 (clipped: 1,638 = 32.8%)  |  Budget: 1.5ms  |  Avg/Tick: 0.839ms
```

### Aggregated (Profiling Analysis)

```
✂️  Tick Processing Budget Active:
   Budget: 1.5ms  |  Scenarios: 3
   Total: 10,117 / 15,000 ticks kept  |  Clipped: 4,883 (32.6%)
```

### Budget Recommendation

Always shown when budget is active or when processing exceeds P5 tick interval:

```
💡 Tick Processing Budget Recommendation:
   P95 processing time: 1.392ms
   Suggested budget: 1.531ms (P95 + 10% safety margin)
```

The "how to set" hint is only shown when no budget is currently configured.

### Warnings

| Condition | Warning Location | Message |
|-----------|-----------------|---------|
| Budget < 1.0ms, 0 clipped | Profiling + Warnings & Notices | Below data granularity |
| Budget > 2× P95 processing | Profiling + Warnings & Notices | Ticks clipped unnecessarily |
| Avg processing > P5 interval | Profiling + Warnings & Notices | Risk of clipping in live |
| Pre-V1.3.0 data | Log output | Filtering skipped |

---

## Hardware Dependency

The tick processing budget simulates **your target hardware's processing speed**. The recommended budget is derived from measured P95 processing time on the current machine.

**Important:** If you develop on a fast machine but deploy to slower hardware, the recommended budget from your dev machine will be too optimistic. Set the budget based on the target environment's measured processing time, not the dev machine.

---

## Architecture

```
SharedDataPreparator (Main Process, Phase 1)
    │
    ├── _filter_ticks_for_scenario()
    │       └── _apply_tick_budget()  ← flagging happens here
    │               └── returns (flagged_ticks with is_clipped, ClippingStats)
    │
    └── prepare_scenario_packages()
            └── returns (packages, clipping_stats_map)
                    │
                    ▼
        Subprocess (pickle transport, all ticks)
                    │
                    ▼
            execute_tick_loop()
                    │
                    for tick in ticks:
                    │   ├── trade_simulator.on_tick(tick)   ← BROKER PATH (all ticks)
                    │   │
                    │   ├── if tick.is_clipped: continue    ← CLIPPING GATE
                    │   │
                    │   └── bar_rendering, workers,         ← ALGO PATH (non-clipped only)
                    │       decision, live_export
                    │
                    ▼
          BatchExecutionSummary
                    │
                    ├── clipping_stats_map → ProfilingSummary, WarningsSummary
                    └── profiling_data.ticks_total → ExecutiveSummary (dual tick count)
```

- Flagging in main process → all ticks cross pickle boundary (with `is_clipped` flag)
- Tick loop splits into broker path (all ticks) and algo path (non-clipped only)
- Broker simulation sees full market data — pending order fills, SL/TP triggers operate correctly
- ClippingStats flows through main process (same pattern as `broker_scenario_map`)

---

## Tick Loop Split — Log Evidence

When budget is active, the scenario log shows the broker/algo path separation per tick. Example from a USDJPY scenario (budget 1.5ms, LONG trade opened at algo-tick 10):

```
✅ BROKER+ALGO tick #0   bid=149.78  open_positions=0   ← both paths
✅ BROKER+ALGO tick #4   bid=149.79  open_positions=0   ← both paths
🔒 BROKER-ONLY tick #5   bid=149.78  open_positions=0   ← clipped, only broker sees it
🔒 BROKER-ONLY tick #9   bid=149.78  open_positions=0   ← clipped, only broker sees it
🎯 Trade signal at tick 10: LONG 0.01 lots               ← algo opens trade
✅ BROKER+ALGO tick #12  bid=149.78  open_positions=1   ← position open, both paths
🔒 BROKER-ONLY tick #14  bid=149.79  open_positions=1   ← broker monitors position on clipped tick
🔒 BROKER-ONLY tick #19  bid=149.79  open_positions=1   ← price update, SL/TP check — algo blind
🔒 BROKER-ONLY tick #24  bid=149.79  open_positions=1   ← broker still watching
✅ BROKER+ALGO tick #144 bid=149.78  open_positions=0   ← trade closed
```

Ticks #14, #19, #24 are clipped — the algo never sees them, but the broker updates bid/ask and checks SL/TP triggers on these ticks. This matches real broker behavior where the broker processes all market data regardless of client processing speed.

Confirmed by profiling call counts:

| Operation | Calls | Meaning |
|-----------|-------|---------|
| `trade_simulator` | 5,000 | All ticks (broker path) |
| `worker_decision` | 3,950 | Algo ticks only (non-clipped) |

Executive summary displays both: `Ticks Processed: 5,000 total (3,950 algo)`

---

## Related

- [Config Cascade](config_cascade_guide.md) — how `tick_processing_budget_ms` inherits through the 3-level hierarchy
- [Process Execution](process_execution_guide.md) — subprocess architecture and profiling system
- [Batch Data Flow](architecture/batch_data_flow.md) — serialization boundaries
