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
| `StressTestConfig` | `framework/types/stress_test_types.py` | Config dataclasses |
| `StressTestRejection` | `framework/stress_test/stress_test_rejection.py` | Rejection logic |
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

**ExecutiveSummary (detailed warning):** Grouped by config signature — scenarios sharing the same stress test config are listed together. Scenarios with overridden configs appear as separate groups.

Both renderers derive stress test status from `SingleScenario.stress_test_config` via `StressTestConfig.from_dict()`. New stress test types are picked up automatically once added to `StressTestConfig.has_any_enabled()`.

Key files: `framework/batch_reporting/batch_summary.py`, `framework/batch_reporting/executive_summary.py`

## Determinism

Same `seed` + same order sequence = identical rejection pattern across runs. This is guaranteed by `SeededProbabilityFilter` which wraps `random.Random(seed)`.
