# Performance Tracking — Two-Layer Model

**Two independent tracking layers, two independent switches.** Both can be toggled off completely for production-lean runs (Field Study, latency-sensitive live profiles).

---

## Layers

### Layer A — Per-Component Tracker

**Where:** `WorkerOrchestrator.__init__` creates a `WorkerPerformanceTracker` per worker and one `DecisionLogicPerformanceTracker` for the decision logic. Hot-path `.record()` calls happen inside `WorkerOrchestrator` (per worker, per tick) and inside `AbstractDecisionLogic.execute_decision`.

**Output sections (consume Layer A):**
- 📊 PERFORMANCE DETAILS (PER SCENARIO) — per-worker call counts, avg / min / max / total
- 📊 AGGREGATED SUMMARY (ALL SCENARIOS) — aggregated worker + decision timing
- ⚠️ BOTTLENECK ANALYSIS (Worst Performers) — slowest scenario + slowest worker
- 🔍 WORKER DECISION BREAKDOWN — worker / decision / coordination split (hybrid, see below)
- 🔥 OVERHEAD ANALYSIS — derived from breakdown data

**Cost per tick:** N × `.record()` for N workers + 1 × `.record()` for decision. Scales linearly with worker count.

### Layer B — Tick-Loop Profiler

**Where:** `process_tick_loop.py` body — operation-level timers bracketed by `time.perf_counter()` between every loop step (`trade_simulator`, `bar_rendering`, `bar_history`, `worker_decision`, `order_execution`, `live_update`, plus `total_per_tick` and `inter_tick_intervals_ms`).

**Output sections (consume Layer B):**
- ⚡ PROFILING ANALYSIS (per scenario)
- ⚡ AGGREGATED PROFILING (ALL SCENARIOS)
- 🔥 BOTTLENECK ANALYSIS (Performance Optimization Targets) — operation-level

**Cost per tick:** 12 × `time.perf_counter()` + 12 × dict-updates. Constant regardless of worker count.

**Note:** AutoTrader has no Layer B equivalent. Its `autotrader_tick_loop.py` carries no operation-level timers — only Layer A applies in the live pipeline.

---

## Section ↔ Layer Map

| Section | Layer A | Layer B |
|---|---|---|
| 📊 PERFORMANCE DETAILS (PER SCENARIO) | ✅ | — |
| 📊 AGGREGATED SUMMARY (ALL SCENARIOS) | ✅ | — |
| ⚠️ BOTTLENECK ANALYSIS (Worst Performers) | ✅ | — |
| ⚡ PROFILING ANALYSIS (per scenario / aggregated) | — | ✅ |
| 🔥 BOTTLENECK ANALYSIS (Performance Optimization Targets) | — | ✅ |
| 🔥 OVERHEAD ANALYSIS | ✅ | — |
| 🔍 WORKER DECISION BREAKDOWN | ✅ | ✅ (hybrid) |

### Why the Hybrid Section needs both

🔍 WORKER DECISION BREAKDOWN combines top-line operation timing (Layer B) with per-worker decomposition (Layer A) and computes `Coordination Overhead = Total − Worker − Decision`. Without Layer A's split, the section would either show only a total (no breakdown) or compute a misleading "100% overhead" — so it is suppressed when either layer is off.

---

## Defaults

| Switch | Pipeline | Default | Reasoning |
|---|---|---|---|
| `tick_loop_profiling` | Backtesting | `true` | Cheap, broad diagnostic value — answers "where does the time go?" |
| `worker_decision_tracking` | Backtesting | `false` | More expensive (scales linearly with worker count), narrower diagnostic value. Industry-standard for production tooling: opt-in |
| `worker_decision_tracking` | AutoTrader | `true` | The live display's WORKER PERFORMANCE panel is a core operator UX element — tracker data must be available by default. Per-tick overhead is dominated by network latency anyway |

**Opt-in profiles** (Backtesting):
- Scenario sets that verify tracker output (`baseline`, `margin_validation`, `multi_position`) and the macro benchmark explicitly set both switches to `true` in their global `execution_config.performance_tracking` block

**Opt-out profiles** (AutoTrader):
- The Field Study profile and similar latency-sensitive acceptance runs set `worker_decision_tracking: false` in their profile's `execution.performance_tracking` block

---

## Graceful Degradation

| `tick_loop_profiling` | `worker_decision_tracking` | Sections shown | Executive Summary `Tracking:` line |
|---|---|---|---|
| `true` | `true` | All | _(none — default case, no friction)_ |
| `true` | `false` | Layer-B sections only | `⚠️ Worker tracking OFF (per-worker / decision breakdowns unavailable)` |
| `false` | `true` | Layer-A sections only | `⚠️ Tick-loop profiling OFF (operation hotspot analysis unavailable)` |
| `false` | `false` | None | `⚠️ All performance tracking OFF (no per-component or operation-level diagnostics)` |

The hybrid 🔍 WORKER DECISION BREAKDOWN is suppressed if **either** layer is off (see above).

---

## Tick-Loop Implementation — Why No Context Managers

A natural temptation when looking at the `process_tick_loop.py` profiling brackets is to refactor them into a context manager:

```python
with profiler.measure('trade_simulator'):
    trade_simulator.on_tick(tick)
```

This is **deliberately not done**. The tick loop is the hottest code path in the framework. Every nanosecond of overhead is multiplied by N ticks × N operations per tick.

Measured overhead per call (microbenchmark, CPython 3.x):

| Pattern | ns / call (enabled) | ns / call (disabled) | Per 100k ticks × 6 ops |
|---|---|---|---|
| Naive `if profiling_enabled:` inline | ~260 | ~10 | 156 ms / 6 ms |
| Class-based context manager (pre-allocated) | ~380 | ~230 | 228 ms / 138 ms |
| `@contextmanager` decorator | ~700 | ~650 | 420 ms / 390 ms |

The `with`-block introduces frame setup, `__enter__`/`__exit__` dispatch, and (with `@contextmanager`) generator advancement. Even the cheapest class-based variant is 1.5× the cost of inline conditionals **at minimum** — and when disabled, where the framework should be at its leanest, context managers are roughly 23× more expensive than inline conditionals.

We pay the visual cost of inline `if`-checks to keep the tick loop's overhead as close to zero as physically achievable. The pattern is intentionally repetitive — six structurally identical blocks, one per operation — because uniform code is easy to scan even when verbose. Wrappers, helpers, decorators, and context managers all add measurable cost without changing what the code does.

If you find yourself wanting to "clean up" the profiling brackets in `process_tick_loop.py`, **don't**. The current shape is the result of an explicit performance-first decision. The benchmark suite's throughput tolerance (±20%) cannot absorb a 3× overhead inflation, and the field study profiles depend on the disabled path being effectively free.

```python
# This is the intended shape — do not refactor:
if profiling_enabled: t = time.perf_counter()
trade_simulator.on_tick(tick)
if profiling_enabled:
    profile_times['trade_simulator'] += (time.perf_counter() - t) * 1000
    profile_counts['trade_simulator'] += 1
```

---

## Config Pathways

**Backtesting (3-level cascade — `app_config` → scenario set `global` → `scenarios[i]`):**

```json
"execution_config": {
  "performance_tracking": {
    "tick_loop_profiling": true,
    "worker_decision_tracking": false
  }
}
```

Lives in [`configs/app_config.json::backtesting.execution.default_scenario_execution_config`](../../configs/app_config.json), can be overridden per scenario set in the `global` block, can be overridden per scenario in `scenarios[i].execution_config`.

**AutoTrader (2-level cascade — `app_config.autotrader` → profile):**

```json
"execution": {
  "parallel_workers": false,
  "bar_max_history": 1000,
  "performance_tracking": {
    "worker_decision_tracking": true
  }
}
```

Lives in [`configs/app_config.json::autotrader.execution`](../../configs/app_config.json), can be overridden per profile in `configs/autotrader_profiles/*/profile.execution`.

See [config_cascade_guide.md](../config_cascade_guide.md) for the full cascade documentation.

---

## Related Files

| File | Role |
|---|---|
| [`python/framework/workers/worker_orchestrator.py`](../../python/framework/workers/worker_orchestrator.py) | Layer A tracker creation, gated by `worker_decision_tracking` |
| [`python/framework/process/process_tick_loop.py`](../../python/framework/process/process_tick_loop.py) | Layer B operation profiling, gated by `tick_loop_profiling` |
| [`python/framework/reporting/console/performance_summary.py`](../../python/framework/reporting/console/performance_summary.py) | Layer-A sections, suppressed via `_layer_a_has_data()` |
| [`python/framework/reporting/console/profiling_summary.py`](../../python/framework/reporting/console/profiling_summary.py) | Layer-B sections, suppressed via `_layer_b_has_data()` |
| [`python/framework/reporting/console/worker_decision_breakdown_summary.py`](../../python/framework/reporting/console/worker_decision_breakdown_summary.py) | Hybrid section, suppressed when either layer is off |
| [`python/framework/reporting/console/executive_summary.py`](../../python/framework/reporting/console/executive_summary.py) | `Tracking:` status line in the EXECUTION RESULTS block |
| [`python/framework/types/config_types/performance_tracking_config_types.py`](../../python/framework/types/config_types/performance_tracking_config_types.py) | Pydantic models with `extra='forbid'` |
