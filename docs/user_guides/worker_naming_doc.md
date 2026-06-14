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

**Workers:** `CORE/rsi`, `CORE/bollinger`, `CORE/ma_trend`, `CORE/macd`, `CORE/obv`, `CORE/heavy_rsi`

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

## Mandatory Worker Classmethod — Market Compatibility

Every concrete worker **must** override `get_required_activity_metric()` on `AbstractWorker`. It declares which market activity metric the worker needs, so the framework can reject incompatible scenarios before any subprocess starts.

```python
from typing import Optional

class MyWorker(AbstractWorker):

    @classmethod
    def get_required_activity_metric(cls) -> Optional[str]:
        # Options:
        # - None          → price-based only (RSI, Bollinger, MACD, range/session workers)
        # - 'volume'      → real trade volume (crypto only)
        # - 'tick_count'  → tick arrival density (forex only)
        return None
```

**Why it is mandatory.** The parent class raises `NotImplementedError` with an actionable message if you forget. The framework validates at pre-flight time (Phase 2 — Availability) that the scenario's broker provides the declared metric, using `primary_activity_metric` from `configs/market_config.json` as the single source of truth. Incompatible scenarios are marked invalid and skipped; the remaining scenarios continue running. See [`docs/architecture/market_capabilities.md`](../architecture/market_capabilities.md) for the full flow and rationale.

---

## Worker Authoring — Determinism & Normalization

Two rules every worker must follow:

**1. A worker is a pure function of its inputs.** `compute()` may read only `tick`,
`bar_history`, and `current_bars`. NEVER read wall-clock time (`datetime.now()` /
`time.time()` — use the injected clock, see project rule §9), and NEVER read external
mutable state at runtime: caches, run artifacts, or the volatility / market-analysis
**profiles**. Those profiles are **setup / scenario-generation information only** (the
`discoveries` CLIs). Coupling a worker to them breaks reproducibility — new data
recomputes the profile, so identical historical bars would produce different outputs — and
injects look-ahead. Compute any volatility reference you need **locally, from your own
window**.

**2. Normalize through `Normalizer`.** If your worker expresses a price-space quantity
relative to a volatility or range reference (band position, slope-in-volatility-units,
relative width), route it through `Normalizer` (`python/framework/utils/trading_math/normalizer.py`) —
the single audited path that keeps such values cross-instrument comparable. It does NOT
apply to bounded oscillators (RSI) or raw-difference indicators (MACD), which have no
normalization step. See [`docs/architecture/normalization_system.md`](../architecture/normalization_system.md).

**Strategy-owned diagnostics.** A decision logic that wants structured per-run diagnostics
(signal funnels, near-miss analysis) declares a CSV via
`self.diagnostics_csv(name, columns)` and appends rows during the run. The framework owns the
file logistics — it flushes the CSV into a `diagnostics/` subfolder of the run directory at
run end, in both pipelines. See [`docs/architecture/diagnostics_csv_sink.md`](../architecture/diagnostics_csv_sink.md).

**Component metadata.** Every worker and decision logic should declare `get_metadata()` →
`ComponentMetadata` (version, doc_link, and — for decision logics — recommended_markets /
recommended_instruments). The framework logs the version at run start and emits a soft
market-fit warning if the run's market/instrument is outside the recommended set. See
[`docs/architecture/component_metadata.md`](../architecture/component_metadata.md).

---

## Recompute Cadence — When a Worker Recomputes

By default a worker recomputes on **every tick** (`RecomputeCadence.PER_TICK`). For a
bar-derived indicator (Bollinger bands, a moving-average trend) the value only changes when
a **bar closes** — recomputing it on every intra-bar tick repeats the same result hundreds
of times. A run config can opt a worker instance into bar-close-only recompute:

```json
"tunnel": { "periods": { "M15": 20 }, "deviation": 2.0, "recompute": "bar_close" }
```

With `"recompute": "bar_close"` the orchestrator recomputes the worker only when one of its
required timeframes closes a bar; the cached result is served on the ticks in between (the
bar-close transition is surfaced by the bar renderer as a typed `BarRenderState`).

- **Per-instance, not per-class.** The same CORE worker can be `bar_close` in a bar-close
  strategy and `per_tick` in a tick-reactive one — the switch lives in the run config.
- **Determinism — know your sampling grid.** Only opt in when the consumer reads the worker
  **on its bar-close grid**. A strategy that acts on M15 closes and reads an M15 Bollinger
  worker is safe (it samples exactly where the worker recomputes). A consumer that reads the
  worker's value *between* its closes — or a worker that reflects `tick.mid` live (intra-bar
  `position` / `position_raw`) — must stay `per_tick`, otherwise it sees a frozen value.
- **Default stays `per_tick`** for all CORE workers — existing scenario sets are unchanged.

---

## Accessing Framework Capabilities — Injected vs Imported

There are two ways your worker or decision logic reaches framework functionality, and one
rule for telling them apart:

- **Injected collaborators** — anything carrying runtime state or a framework-wired binding:
  the trading API, the logger, validated params, the event hooks. These arrive on `self`,
  provided by the framework after validation — `self.trading_api` (set via
  `set_trading_api()`), `self.logger`, `self.params`. You never construct or import them:
  they must be the instance the framework wired for this run (the simulation vs live
  executor is chosen there, not by you).
- **Stateless utilities** — pure helpers with no instance state and no wiring: `Normalizer`,
  `time_utils`, `MarketCalendar`. You **import them where you use them**. The import line at
  the top of the file keeps the dependency explicit and visible — *explicit is better than
  implicit*. They are discovered through these docs and the CORE reference implementations,
  not through the base class.

**Rule of thumb:** does it carry runtime state or need framework wiring? → it lives on
`self` (injected). Is it pure / stateless? → import it. The base class stays a lean contract
(the methods you override + the injected collaborators), not a catch-all facade for every
utility — so what a component actually depends on stays readable at the top of its file.

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
- Call in `compute_tick()`, not in `_execute_decision_impl()` — execution-layer
  events (rejections, guard blocks) go through OrderGuard
- Single slot: only the last call per tick is displayed
- `reason_key` is optional but helps identify narration patterns
- **Narrate every terminal path.** The channel is single-slot and
  last-write-wins, so if a path (e.g. a successful BUY return) skips
  `notify_awareness()`, the display keeps showing the last narration
  from a *previous* tick — typically the FLAT/"no signal" message —
  even though the current decision is different. Rule of thumb: each
  `return Decision(...)` in `compute_tick()` should be preceded by exactly
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
- Can be called from both `compute_tick()` and `_execute_decision_impl()`
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
