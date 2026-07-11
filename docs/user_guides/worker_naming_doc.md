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

**Workers (INDICATOR):** `CORE/rsi`, `CORE/bollinger`, `CORE/ma_trend`, `CORE/macd`, `CORE/obv`, `CORE/heavy_rsi`

**Workers (SIGNAL):** `CORE/llm_sentiment` (#141 — pre-collected sentiment lookup, see *Worker Types* below)

**Decision Logics:** `CORE/simple_consensus`, `CORE/aggressive_trend`, `CORE/cautious_macd`, `CORE/trend_channel_reference`, `CORE/hybrid_sentiment_reference`

Backtesting variants: `CORE/backtesting/backtesting_deterministic`, `CORE/backtesting/backtesting_margin_stress`, `CORE/backtesting/backtesting_multi_position`

---

## Worker Types — INDICATOR vs SIGNAL

Every worker inherits the lean `AbstractWorker` contract through one of two type-specialized bases:

- **INDICATOR** (`AbstractIndicatorWorker`) — synchronous computation from bars/ticks (RSI,
  Bollinger, MACD, …). Declares `periods`, a compute basis (#420), and recomputes on bar updates.
- **SIGNAL** (`AbstractSignalWorker`, #141) — looks up **pre-collected external data** by timestamp
  instead of computing from bars. No warmup, no timeframes, no compute basis. The first SIGNAL
  worker is `CORE/llm_sentiment`, reading archived LLM sentiment.

A SIGNAL worker resolves, per tick, the most recent snapshot with `collected_msc ≤ tick` (the
no-look-ahead key) via an injected `SignalDataProvider`, and refreshes on two triggers: the tick
crosses into a new snapshot window, OR the served result's staleness flips (`_evaluate_stale` —
the one staleness definition per worker; without the flip trigger a feed dying mid-session would
stay fresh-flagged forever). The provider is built from the prepared signal series in the data
package and injected by the framework (sim subprocess / live boot) — never constructed by the
worker. Both types return a `WorkerResult`, so the decision logic sees no difference.

The signal archive is resolved through the first-class `data_sentiment_type` data source
(import → index → parquet; sim scenario field / AutoTrader `sentiment_source` block) — see the
signal data source doc. The worker's `data_path` param remains a dev-only override.

### Signal-outage contract (mandatory when consuming a SIGNAL worker)

Feed status is **envelope, not payload**: every `WorkerResult` carries `is_stale` as a
framework-stamped field (`result.is_stale` — the SIGNAL base sets it after `_build_result`;
workers never declare or set it themselves). It is delivered with EVERY result regardless of
#425 subscription narrowing — staleness-blind consumption is designed away, not validated.
Backtests on complete archives simply never see it flip; deliberate outage drills (stale-tail
profiles / gapped archives) are how the reaction is exercised.

**Type-level contract params:** every SIGNAL worker inherits `max_staleness_minutes` (default 30 —
drives the base's age-based `_evaluate_stale`, so age staleness works out of the box) and
`data_path` (dev override) — merged by the base into `get_parameter_schema()` over the worker's
`_get_domain_parameter_schema()`. The schema getter stays the single visible config surface
(validation, defaults, tooling unchanged); concrete workers declare only their domain params.

On top of that, a decision logic whose `get_required_workers()` includes a SIGNAL worker MUST
override `on_signal_stale(worker_name, source)` — enforced at startup in BOTH pipelines
(orchestrator validation; a strategy consuming a SIGNAL worker cannot even start without it).
The hook is edge-triggered by the orchestrator once per fresh→stale flip (a session that
starts stale fires on the first result). Fallback, go flat, HALT, or deliberately ignore:
even "ignore" is a written line. Recovery is visible via `result.is_stale` (no separate hook).

Reference implementation: `CORE/hybrid_sentiment_reference.on_signal_stale` — warns to the
session channel (error pot) and emits an event-tape entry, while its fusion degrades to
pure-indicator mode (`_read_sentiment` reads the envelope).

### Market-data staleness contract (mandatory for EVERY decision logic, #436)

The session-level sibling: when the TICK STREAM itself goes blind, EVERY decision logic must
have programmed its reaction — `on_market_data_stale(status)` is a **mandatory override for
all decision logics** (startup-validated in both pipelines; an explicit `pass` is a conscious,
written answer). It is dispatched by the LIVE loop's heartbeat evaluation
(`execution.market_data_stale_after_s`, default 300 s) — never in sim (replay gaps are data),
unless a planned `stale_data_stress` window drives it deterministically. The OrderGuard
additionally blocks NEW entries while stale (framework floor). Instruments, escalation ladder,
and the outage decision tree: **read
[Live Outage Handling](live_outage_handling_guide.md)** before writing the override.

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

## Compute Basis — LIVE vs BAR_CLOSE (#420)

Every worker **must** declare its `compute_basis` (mandatory `get_default_compute_basis()`,
like `get_required_activity_metric()`). It is a single binary axis — the worker's **data
subscription** — that decides both *what* the worker computes on and *when* it recomputes:

| Basis | Computes on | Recompute | Character |
|---|---|---|---|
| **`LIVE`** | completed history **+ the forming bar** (`tick.mid`) | **every tick** | drifts intra-bar; reacts to events *within* a bar |
| **`BAR_CLOSE`** | **completed bars only** | only when a required timeframe **closes** (cached in between) | stable, cheap; the institutional bar-indicator model (nautilus / LEAN / Backtrader) |

**`BAR_CLOSE` is not a free speedup — it changes behavior.** It freezes the indicator between
closes, so an intra-bar event that touches a level and reverts before the close is **invisible**
to it. It is correct only for a consumer that **reads on the bar-close grid** (a swing strategy,
a higher-timeframe gate). A tick-reactive consumer — anything reading a live value like Bollinger
`position`/`position_raw` from `tick.mid` — needs `LIVE`.

A run config opts a worker **instance** into a basis (sibling of `periods`, one value — not
per-timeframe; multi-timeframe needs separate instances):

```json
"h1_trend":    { "periods": { "H1": 50 },  "ma_type": "ema", "compute_basis": "bar_close" },
"m15_channel": { "periods": { "M15": 20 }, "deviation": 2.0 }
```

Here the H1 trend gate is a stable `bar_close` screen (~3× cheaper — it recomputes once per H1
close, not per tick), while `m15_channel` stays `LIVE` (default, omitted) because the strategy
reads its band `position` live.

- **Per-instance, not per-class.** The same CORE worker is `LIVE` in a tick-reactive strategy and
  `BAR_CLOSE` in a bar-grid one — the switch lives in the run config.
- **Default is `LIVE`** for all CORE indicators — existing scenario sets run **bit-identical**
  (no re-baseline). `BAR_CLOSE` is a conscious opt-in.
- **Telemetry.** Because a `BAR_CLOSE` worker computes far less than once per tick, the run
  report's **WORKER DETAILS** shows the basis, the **compute / tick ratio**, and the **ticks
  idle** since the last compute (e.g. `bar_close 200/49196 computes (0%, 148 idle)`) — so a
  bar-cadence worker is not misread as "barely ran". The per-compute `Avg` ms is the real worker
  cost (cadence-independent); a `BAR_CLOSE` run is therefore not comparable to a `LIVE` baseline.

---

## Accessing Bar Data — `effective_bars()` and the compute window

A worker never reaches into `bar_history` directly. It calls the base helper, which applies the
`compute_basis` policy (append the forming bar under `LIVE`, exclude it under `BAR_CLOSE`) and,
optionally, bounds the depth to the last `count` completed bars:

```python
# window-bounded: take only the last period + 1 bars (the slope needs one extra)
bars = self.effective_bars(timeframe, bar_history, current_bars, count=period + 1)
```

**Pass `count` = the exact depth your `compute()` reads.** `bar_history` is a rolling
`deque(maxlen=bar_max_history)` (1000 by default) shared across all workers, so without `count`
every compute materializes the whole history just to use its last `period` bars. With `count`
the per-compute cost is **O(count)** instead of **O(bar_max_history)** — and stays constant if the
operator raises `bar_max_history`. The result is bit-identical for a worker that only reads its
tail (an SMA / RSI band over the last `period` bars); it is **not** correct to bound a worker whose
output is path-dependent over the whole history — a cumulative indicator (OBV) or a recursive EMA
(MACD) reads `len(bars)` and must keep the full (timeframe-filtered) history, so it omits `count`.

**Two different "how many bars" concerns — do not conflate them:**

| Declaration | Question it answers | Who consumes it |
|---|---|---|
| `get_warmup_requirements()` | how much history to **pre-load** so the worker is warm at tick 0 | data preparation / startup (coarse — "enough") |
| the `count` window | how much of the available history the worker **reads this compute** | the worker itself, at the point it knows |

The framework filters the **coarse** axis for you — the orchestrator hands each worker only the
**timeframes** it requires (`get_required_timeframes()`). The **fine** axis — depth — stays with the
worker via `count`, on purpose: only the worker knows its exact per-compute need, and it can differ
from the warmup figure (a slope buffer of `+1`, or a guard that wants `len(bars)`). Keeping depth in
the worker is the more flexible, more transparent choice; the small cost is that each worker states
its own window.

---

## Computing Only What's Consumed — `get_required_workers()`

Every decision logic MUST declare its workers via `get_required_workers()` — the single, mandatory
wiring of instance name → `WorkerRequirement` (the worker type plus the output signals the logic
reads). Signals are the worker's output-schema keys; an unknown one is caught at pre-flight:

```python
def get_required_workers(self) -> Dict[str, WorkerRequirement]:
    return {
        'rsi_fast': WorkerRequirement.of('CORE/rsi', 'rsi_value'),
        'bollinger_main': WorkerRequirement.of('CORE/bollinger', 'position'),
    }
```

- **Mandatory, no silent default.** The method is abstract — there is no implicit compute-all.
  "Read every output of this worker" is stated explicitly with `WorkerRequirement.all(type)` (the
  `SUBSCRIBE_ALL` sentinel); a narrow read-set with `WorkerRequirement.of(type, *signals)`.
- **⚠️ `SUBSCRIBE_ALL` is safe but NOT free — a latent Performance-GAU.** With `.all()` a rich worker
  recomputes *every* optional output *every* tick, even the ones nobody reads (e.g. Bollinger's
  `slope` = a second moving average). On a hot decision that is silent per-tick waste. It was exactly
  this — an unread Bollinger slope computed every tick — that caused the ~14% V1.4 throughput
  regression. **For a hot decision on a rich worker, declare the exact signals with `.of()`.**
- **A worker skips only its *optional* outputs.** The always-on core (e.g. a Bollinger's bands +
  position) is computed unconditionally; the worker gates the rest via `self.wants_output(key)`.
  Bollinger's `slope` costs a second moving average — it is skipped unless a consumer declares it.
- **Validated up front.** Each declared signal must exist on the worker's output schema. A typo or
  dead signal is caught at batch pre-flight (the scenario is excluded, the batch continues) and at
  orchestrator construction in both pipelines — never as a `KeyError` deep inside the run. (Reading an
  output you forgot to declare still surfaces as a `KeyError`, since it was never computed.)

This is the framework-side answer to the same "compute only what's needed" principle as the bar
window above: the consumer declares the *outputs* it reads, the worker the *bars* it reads.

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

### ❌ Unknown parameter
```
ValueError: 'MacdWorker': Unknown parameter 'fast_periodd' — not in the component schema.
```
A config key that is not in `get_parameter_schema()` (and is not a structural/reserved key like
`periods`, `recompute`, `include_current_bar`) is rejected at pre-flight — a typo no longer slips
through and gets silently ignored.
**Fix:** Correct the key to a schema parameter, or add it to `get_parameter_schema()` if it is a real
parameter. Use a `_`-prefixed key (e.g. `_comment`) for notes — those are always allowed.
