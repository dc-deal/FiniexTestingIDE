# Stress Test System

Config-driven stress test injection for trading simulation scenarios.

## Overview

Stress tests inject controlled disruptions into the simulation to verify how trading algorithms handle adverse conditions. All stress tests use **seeded randomness** for deterministic, reproducible results.

## Configuration

Stress tests are configured via `stress_test_config` in scenario JSON files. The config supports **2-level cascade** (global → scenario override).

### JSON Format

```json
{
  "global": {
    "stress_test_config": {
      "reject_open_order": {
        "enabled": false,
        "seed": 999,
        "probability": 0.3
      }
    }
  },
  "scenarios": [
    {
      "name": "scenario_01",
      "stress_test_config": {
        "reject_open_order": {
          "enabled": true,
          "probability": 0.5
        }
      }
    }
  ]
}
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `enabled` | bool | `false` | Activate this stress test |
| `seed` | int | `42` | Random seed for deterministic sequence |
| `probability` | float | `0.0` | Trigger probability (0.0 = never, 1.0 = always) |

### Cascade Behavior

- Global config sets defaults for all scenarios
- Scenario-level config overrides global (deep merge)
- Omitted fields inherit from global
- Example: global sets `seed: 999`, scenario overrides only `probability: 0.5` → seed stays 999

## Available Stress Tests

### `reject_open_order`

Rejects open orders with seeded probability at fill time. Rejected orders appear in order history with `RejectionReason.BROKER_ERROR` and `[STRESS TEST]` prefix.

**Injection point:** `TradeSimulator._process_pending_orders()` Phase 1 (latency queue drain)

**Behavior:**
- Each order that exits the latency queue is evaluated independently
- `probability: 0.3` → ~30% of orders rejected (seeded, deterministic)
- `probability: 1.0` → all orders rejected (useful for testing rejection handling)
- `probability: 0.0` → no rejections (default, same as disabled)
- Rejections flow through the standard reporting pipeline (trade history, executive summary)

### `stale_data_stress` (#436)

Planned, deterministic stale windows on the sim time axis — drills BOTH staleness
contracts ([outage handling guide](user_guides/live_outage_handling_guide.md))
without a live outage. Not probability-based: events fire at exact timestamps.

```json
"stress_test_config": {
  "stale_data_stress": {
    "enabled": true,
    "events": [
      { "label": "sentiment feed dies 60min",
        "data_source": "crypto_sentiment",
        "stale_start_date": "2026-04-27T06:10:00+00:00",
        "stale_end_date":   "2026-04-27T07:10:00+00:00" },
      { "label": "market data freeze",
        "data_source": "kraken_spot",
        "stale_start_date": "2026-04-27T06:15:00+00:00",
        "stale_end_date":   "2026-04-27T06:25:00+00:00" }
    ]
  }
}
```

**Events block DATA SOURCES — never bars or single workers.** An outage hits a
feed, so every consumer of that source sees the same gap. `data_source` names a
source the scenario binds — its `data_sentiment_type` or its `data_broker_type`
— and the source kind decides the injection plane:
- **Signal source** (`data_source` == the scenario's `data_sentiment_type`) —
  **data-plane carve**: the window is physically carved out of the refined
  signal series at preparation time (`StaleDataSlicer`), for ALL SIGNAL workers
  subscribed to that source. Lookups inside the window resolve as-of the last
  pre-window snapshot, so the REAL #434 chain fires: snapshot age grows →
  `is_stale` flips once `max_staleness_minutes` is exceeded → `on_signal_stale`
  dispatches. The window must therefore be LONGER than the worker's staleness
  threshold for the flip to happen inside it (per-worker flip times stay real:
  a 10-min worker flips before a 30-min worker on the same dead source).
- **Tick source** (`data_source` == the scenario's `data_broker_type`) —
  **status-plane injection**: entering the window sets `MarketDataStatus`
  stale, warns to the scenario pot, and edge-dispatches `on_market_data_stale`;
  leaving restores fresh with a from–to episode line. **Ticks keep flowing** by
  design: a dead FEED does not freeze the MARKET — carving ticks would also
  freeze simulated broker-side SL/TP fills, and a replay tick gap is
  indistinguishable from data. The OrderGuard entry block (`STALE_MARKET_DATA`)
  is ACTIVE inside the window, deterministically.

**Validation:** a `data_source` the scenario does not bind → config error
(scenario excluded at preparation, batch continues, §33). Missing
`data_source` / inverted window → config error. A window without (partial)
overlap with the scenario's data range → warning `data deviation` (the event
can never fire).

**Injection points:** `StaleDataSlicer` (series carve at data preparation,
`SharedDataPreparator`) · `StaleDataStressDriver` (per-tick state machine,
`process_tick_loop`). Demo scenario: `EURGBP_stale_market_13` in the EURGBP
stress set; probe logic: `CORE/backtesting/backtesting_outage_probe`.

## Architecture

```
Scenario JSON
    └── stress_test_config section
         └── ScenarioConfigLoader (parse + cascade merge)
              └── SingleScenario.stress_test_config (Dict)
                   └── ProcessScenarioConfig.stress_test_config (StressTestConfig)
                        └── TradeSimulatorFactory
                             └── TradeSimulator.__init__()
                                  └── StressTestRejection (uses SeededProbabilityFilter)
```

### Key Units

| Unit | Path | Role |
|------|------|------|
| `StressTestConfig` | `framework/types/trading_env_types/stress_test_types.py` | Config dataclasses |
| `StressTestRejection` | `framework/stress_test/stress_test_rejection.py` | Rejection logic |
| `StaleDataSlicer` | `framework/stress_test/stale_data_slicer.py` | Signal-source window carve (data plane, at preparation) |
| `StaleDataStressDriver` | `framework/stress_test/stale_data_stress_driver.py` | Tick-source window state machine (status plane) |
| `SeededProbabilityFilter` | `framework/utils/seeded_generators/seeded_probability_filter.py` | Reusable probability filter |
| `SeededDelayGenerator` | `framework/utils/seeded_generators/seeded_delay_generator.py` | Reusable delay generator |
| `ScenarioCascade` | `scenario/scenario_cascade.py` | Config merge (2-level) |

## Extending with New Stress Tests

1. Add config dataclass to `stress_test_types.py` (e.g., `StressTestTimeoutConfig`)
2. Add field to `StressTestConfig` container
3. Update `StressTestConfig.from_dict()` and `has_any_enabled()`
4. Create stress test module in `framework/stress_test/` (e.g., `stress_test_timeout.py`)
5. Wire into the appropriate injection point (TradeSimulator, OrderLatencySimulator, etc.)

No changes needed in config loading pipeline — cascade handles nested dicts automatically.

## Reporting Integration

Active stress tests are prominently displayed in batch reports to prevent confusion about intentional errors.

**BatchSummary (top banner):** Red warning banner at the very top of the report output, before any results. Shows active stress test types with parameters.

**SimExecutiveSummary (detailed warning):** Grouped by config signature — scenarios sharing the same stress test config are listed together. Scenarios with overridden configs appear as separate groups.

Both renderers derive stress test status from `SingleScenario.stress_test_config` via `StressTestConfig.from_dict()`. New stress test types are picked up automatically once added to `StressTestConfig.has_any_enabled()`.

Key files: `framework/batch/batch_report_coordinator.py`, `framework/reporting/console/sim_executive_summary.py`

## Determinism

Same `seed` + same order sequence = identical rejection pattern across runs. This is guaranteed by `SeededProbabilityFilter` which wraps `random.Random(seed)`.
