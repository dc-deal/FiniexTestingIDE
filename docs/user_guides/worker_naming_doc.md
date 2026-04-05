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
