# Tick Processing Budget — Deterministic Clipping Simulation

---

## Overview

In live trading, ticks that arrive while the algorithm is still processing the previous tick are **lost** (clipped). The backtesting simulation processes every tick sequentially — optimistically biased because no ticks are ever skipped.

The **Tick Processing Budget** bridges this gap by deterministically filtering ticks before execution, simulating the clipping behavior of live trading. This produces realistic backtesting results that account for hardware-dependent tick loss.

**Key property:** Filtering happens in the main process during data preparation (Phase 1), before subprocess serialization. This means fewer ticks cross the pickle boundary — smaller packages, faster transfer, faster execution.

---

## How It Works

### Virtual Clock Algorithm

```
budget_ms = configured tick_processing_budget_ms
virtual_clock = 0

for each tick (ordered by collected_msc):
    if tick.collected_msc >= virtual_clock:
        KEEP tick
        virtual_clock = tick.collected_msc + budget_ms
    else:
        CLIP tick (arrived during processing)
```

- **O(n) single pass** — no sorting, no lookback
- Deterministic: same budget + same data = same result every time
- Uses `collected_msc` (device-side collection timestamp, available since V1.3.0)

### Example

Budget = 2.0ms, ticks at: 1000, 1001, 1002, 1003, 1005, 1008

```
Tick 1000ms: collected_msc(1000) >= virtual_clock(0)    → KEEP, clock = 1002
Tick 1001ms: collected_msc(1001) <  virtual_clock(1002)  → CLIP
Tick 1002ms: collected_msc(1002) >= virtual_clock(1002)  → KEEP, clock = 1004
Tick 1003ms: collected_msc(1003) <  virtual_clock(1004)  → CLIP
Tick 1005ms: collected_msc(1005) >= virtual_clock(1004)  → KEEP, clock = 1007
Tick 1008ms: collected_msc(1008) >= virtual_clock(1007)  → KEEP, clock = 1010
```

Result: 4/6 ticks kept, 2 clipped (33.3%)

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

`tick_processing_budget_ms: 0.0` (or absent) = no filtering, no clipping stats, no report section.

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
    │       └── _apply_tick_budget()  ← filtering happens here
    │               └── returns (filtered_ticks, ClippingStats)
    │
    └── prepare_scenario_packages()
            └── returns (packages, clipping_stats_map)
                    │
                    ▼
        DataPreparationCoordinator
                    │
                    ▼
            BatchOrchestrator
                    │
                    ▼
          BatchExecutionSummary.clipping_stats_map
                    │
                    ├── ProfilingSummary (per-scenario + aggregated)
                    └── WarningsSummary (global warnings)
```

- Filtering in main process → subprocess receives already-filtered ticks
- Tick loop unchanged — no runtime overhead in subprocess
- ClippingStats flows through main process only (same pattern as `broker_scenario_map`)

---

## Related

- [Config Cascade](config_cascade_guide.md) — how `tick_processing_budget_ms` inherits through the 3-level hierarchy
- [Process Execution](process_execution_guide.md) — subprocess architecture and profiling system
- [Batch Data Flow](architecture/batch_data_flow.md) — serialization boundaries
