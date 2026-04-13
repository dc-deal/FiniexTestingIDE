# Worker Naming & Requirements System

## Overview

This document explains how the framework loads workers and decision logics, and how they declare dependencies on each other.

**TL;DR**: Point to a `.py` file. The factory finds the one class that inherits from `AbstractWorker` or `AbstractDecisionLogic`. No naming conventions required for user files.

---

## Reference System

Components are referenced by type strings in scenario configs and in `get_required_worker_instances()`.

| Format | Example | Resolves to |
|--------|---------|-------------|
| `CORE/name` | `CORE/rsi` | Framework worker/logic in `python/framework/workers/core/` or `python/framework/decision_logic/core/` |
| Relative path | `user_algos/my_algo/my_range_worker.py` | Relative to **project root** (from scenario config) |
| Relative path | `my_range_worker.py` | Relative to the **decision logic file** (from `get_required_worker_instances()`) |
| Absolute path | `/home/user/algos/my_worker.py` | Used as-is |

**Detection rule:** If the string starts with `CORE/` → framework magic. Anything else → file path.

---

## CORE Workers & Decision Logics

Pre-registered at factory startup. Always available.

**Workers:** `CORE/rsi`, `CORE/envelope`, `CORE/macd`, `CORE/obv`, `CORE/heavy_rsi`

**Decision Logics:** `CORE/simple_consensus`, `CORE/aggressive_trend`, `CORE/cautious_macd`

Backtesting variants: `CORE/backtesting/backtesting_deterministic`, `CORE/backtesting/backtesting_margin_stress`, `CORE/backtesting/backtesting_multi_position`

---

## User Algorithm Files

Place your algo in `user_algos/` — one subdirectory per strategy:

```
user_algos/
└── my_algo/
    ├── my_strategy.py       ← decision logic
    ├── my_range_worker.py   ← worker
    └── my_algo_eurusd.json  ← scenario config
```

**One rule:** each `.py` file must contain exactly **one class** inheriting from `AbstractWorker` or `AbstractDecisionLogic`. The class and file can have any name.

If zero or more than one matching class is found, the factory raises a `ValueError` with a clear message.

Helper classes (not inheriting from any abstract base) in the same file are fine and ignored by the loader.

---

## The Contract Model

### 1. Decision Logic declares required workers

```python
# user_algos/my_algo/my_strategy.py

class MyStrategy(AbstractDecisionLogic):

    def get_required_worker_instances(self) -> Dict[str, str]:
        # Paths relative to THIS file's directory
        return {
            'range_detector': 'my_range_worker.py'
        }
```

For CORE workers, use the `CORE/name` shorthand:

```python
    def get_required_worker_instances(self) -> Dict[str, str]:
        return {
            'rsi_fast': 'CORE/rsi',
            'rsi_slow': 'CORE/rsi',
        }
```

### 2. Scenario config must match

```json
{
  "decision_logic_type": "user_algos/my_algo/my_strategy.py",
  "worker_instances": {
    "range_detector": "user_algos/my_algo/my_range_worker.py"
  },
  "workers": {
    "range_detector": {
      "periods": { "M15": 1, "D1": 15 },
      "atr_period": 14
    }
  }
}
```

All paths in scenario JSON are **relative to the project root** (or absolute).

**Validation rules:**
- All instance names from `get_required_worker_instances()` must exist in `worker_instances`
- Referenced files must resolve to the same physical file (path-normalized comparison)
- Multiple instances of the same type are allowed (e.g., two RSI workers with different parameters)

---

## AwarenessChannel — Tell the Operator What Your Algo Thinks

Your decision logic can narrate its current reasoning via `notify_awareness()`.
This appears as a single ephemeral status line in the live display — no logs,
no batch summary, purely visual. Optional and zero-cost when not used.

```python
from python.framework.types.decision_logic_types import AwarenessLevel

class MyDecision(AbstractDecisionLogic):
    def compute(self, tick, worker_results):
        rsi = worker_results['rsi_fast'].get_signal('rsi_value')
        if rsi > 40 and rsi < 60:
            self.notify_awareness(
                f"RSI neutral ({rsi:.1f}), waiting",
                AwarenessLevel.INFO,
                'neutral_zone'
            )
        # ... rest of compute logic
```

**Levels:** `INFO` (dim, normal narration), `NOTICE` (yellow, filter blocks),
`ALERT` (red, unusual conditions).

**Rules:**
- Call in `compute()`, not in `_execute_decision_impl()` — execution-layer
  events (rejections, guard blocks) go through OrderGuard
- Single slot: only the last call per tick is displayed
- `reason_key` is optional but helps identify narration patterns
- **Narrate every terminal path.** The channel is single-slot and
  last-write-wins, so if a path (e.g. a successful BUY return) skips
  `notify_awareness()`, the display keeps showing the last narration
  from a *previous* tick — typically the FLAT/"no signal" message —
  even though the current decision is different. Rule of thumb: each
  `return Decision(...)` in `compute()` should be preceded by exactly
  one `notify_awareness()` call describing that path's state
  ("BUY mode", "SELL blocked — OBV bearish", "No consensus", etc.).
  Look at the CORE decision logics (`simple_consensus`, `aggressive_trend`,
  `cautious_macd`) for reference patterns.

### Strategy Events — Surface Interesting Moments

For *moments in time* that should linger in the display (crossovers, order
submissions, break-even triggers), use `emit_event()` instead of
`logger.info()`. It writes the same INFO log line *and* pushes the event
into a ring buffer shown in the AutoTrader live display.

```python
self.emit_event(
    f"MACD cross-UP hist={histogram:.4f}",
    AwarenessLevel.INFO,
    'macd_cross_up',
)
```

**Rules:**
- Can be called from both `compute()` and `_execute_decision_impl()`
- Same `AwarenessLevel` enum as `notify_awareness()` — controls display color
- Log level is always `INFO` regardless of `AwarenessLevel`
- Ring buffer size is configurable via `monitoring.event_tape_size` (default: 5)
- In sim mode the buffer is active and events are logged, but NOT rendered
  in the backtesting display (too fast for human consumption)

---

## Display Labels in Parameter Schemas

Both `InputParamDef` (worker/logic config parameters) and `OutputParamDef` (computed outputs) support two optional fields for live dashboard visibility:

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `display` | `bool` | `False` | Include this parameter in the ALGO STATE panel |
| `display_label` | `str` | `''` | Short label for the dashboard (e.g., `'rsi_b'` instead of `'rsi_buy_threshold'`) |

If `display=True` but no `display_label` is set, the raw parameter key is shown.

**Input params** (`get_parameter_schema`) with `display=True` appear as a `Params:` line in the ALGO STATE panel — showing the active config thresholds at a glance (e.g., `rsi_os=30 rsi_ob=70 min_conf=0.50`).

**Output params** (`get_output_schema`) with `display=True` appear in the worker output rows — shortened labels keep the dashboard compact (e.g., `rsi` instead of `rsi_value`, `up`/`lo` instead of `upper`/`lower`).

Labels are resolved once at startup and cached (frozen `DisplayLabelCache`) — no per-tick schema reads.

---

## Common Issues

### ❌ File not found
```
ValueError: Worker file not found: '/app/user_algos/my_algo/my_range_worker.py'
```
**Fix:** Check the path. Paths in JSON are relative to project root. Paths in `get_required_worker_instances()` are relative to the decision logic file.

### ❌ Zero or multiple matching classes
```
ValueError: Expected exactly 1 AbstractWorker subclass in '.../my_worker.py', found 2: [...]
```
**Fix:** Keep exactly one class per file that inherits from `AbstractWorker` or `AbstractDecisionLogic`.

### ❌ Type mismatch
```
ValueError: Type mismatch for 'range_detector': DecisionLogic requires '...',
            but config has '...'. Type override not allowed!
```
**Fix:** Ensure the path in `worker_instances` resolves to the same file as the path declared in `get_required_worker_instances()`.

### ❌ Missing instance name
```
ValueError: Missing 'rsi_fast' in worker_instances. DecisionLogic requires this instance.
```
**Fix:** Add the instance name (with exact spelling) to `worker_instances` in your scenario config.

### ❌ Missing required parameters
**Fix:** Check the worker's `get_parameter_schema()` for parameters with `default=REQUIRED` and provide them in `workers.<instance_name>`.
