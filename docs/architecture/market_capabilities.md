# Market Capabilities & Worker Compatibility

## Purpose

Workers depend on different kinds of market data. Some (RSI, Envelope, MACD) only need prices — they run on any broker. Others (OBV, VWAP) need real trade volume, which forex CFDs cannot provide in a meaningful way. This document describes how the framework declares each market's activity metric and how workers declare what they need, so incompatible combinations are rejected **before** a subprocess ever starts.

## The Single Source of Truth — `primary_activity_metric`

Every market type in `configs/market_config.json` declares exactly one primary activity metric — the unit of "market activity" that its brokers can deliver reliably:

```json
{
  "market_rules": {
    "forex": {
      "primary_activity_metric": "tick_count",
      "inter_tick_gap_threshold_s": 300
    },
    "crypto": {
      "primary_activity_metric": "volume",
      "inter_tick_gap_threshold_s": 600
    }
  }
}
```

| Market | Metric | Why |
|--------|--------|-----|
| `forex` | `tick_count` | OTC CFDs have no centralized volume. Brokers publish quote density (ticks per second) as the closest proxy for participation. |
| `crypto` | `volume` | Centralized exchanges report actual traded base-currency volume per trade. |

Additional metrics may be added later (e.g. `orderbook_depth`, `open_interest`). The metric is a plain string — the validator only needs an exact match.

Brokers inherit the metric from their market type (`brokers[].market_type`). `MarketConfigManager.get_primary_activity_metric_for_broker('mt5')` returns `'tick_count'`, `get_primary_activity_metric_for_broker('kraken_spot')` returns `'volume'`.

## Worker Declaration — `get_required_activity_metric()`

Every concrete worker **must** override a classmethod on `AbstractWorker`:

```python
@classmethod
def get_required_activity_metric(cls) -> Optional[str]:
    return 'volume'   # or 'tick_count', or None
```

Return values:

| Return | Meaning | Example |
|--------|---------|---------|
| `None` | Purely price-based — no activity-data dependency | `RsiWorker`, `EnvelopeWorker`, `MacdWorker`, `HeavyRsiWorker`, `BacktestingSampleWorker`, `TouchAndTurnRangeWorker` |
| `'volume'` | Requires real trade volume | `ObvWorker` |
| `'tick_count'` | Requires tick arrival density | (none yet — reserved for future forex-native workers) |

The method is declared on `AbstractWorker` with `raise NotImplementedError(...)` — missing overrides fail loudly with an actionable message. There is no silent default.

## Pre-Flight Validation Flow

Validation happens in **Phase 3 — Requirements Collection**, inside `RequirementsCollector.collect_and_validate()` as the first per-scenario step — before the warmup-requirements aggregator touches the scenario:

```
BatchOrchestrator.run()
  Phase 0: Config Validation
  Phase 1: Index & Coverage Setup
  Phase 2: Availability Validation      (date range, coverage report)
  Phase 3: Requirements Collection      ← RequirementsCollector owns this
    ├─ Step 1  Worker market compatibility   ← per scenario
    └─ Step 2  Warmup requirements aggregation
  Phase 4: Data Loading
  Phase 5: Quality Validation
  Phase 6: Execution
  Phase 7: Summary
```

`RequirementsCollector` owns a single `WorkerFactory` and `MarketConfigManager` for the whole batch run. The factory is also passed to `AggregateScenarioDataRequirements` so warmup calculation and compatibility validation share the exact same registry — no duplicated class resolution, no per-scenario factory instantiation.

Step 1 iterates `scenario.strategy_config.worker_instances`, resolves each worker class via `WorkerFactory._resolve_worker_class()`, calls `get_required_activity_metric()`, and compares it against the broker metric. On mismatch it builds a `MarketCompatibilityError` via the static `ScenarioDataValidator.validate_worker_market_compatibility()` and wraps the error list in a `ValidationResult(is_valid=False)` attached to the scenario. The scenario is then skipped in Step 2, so incompatible scenarios never enter the requirements map and never reach data loading.

**Skip-and-report, not fail-fast.** Failing scenarios are marked invalid; the batch continues with the remaining valid scenarios. The Executive Summary surfaces the rejected scenario and its error identically to an out-of-range `start_date`.

## Error Message Anatomy

```
Worker 'obv_main' (CORE/obv) requires activity metric 'volume',
but broker 'mt5' provides 'tick_count' (market: forex).
Remove this worker from the scenario, or switch to a broker
whose market provides 'volume'.
```

The message is built by `MarketCompatibilityError` with structured fields so it is both human-readable and programmatically inspectable (unit tests assert on individual fields, not the combined string).

## Adding a New Activity Metric

1. Add the metric string to the relevant `market_rules.<market>.primary_activity_metric` in `configs/market_config.json`.
2. Update this document's table with the new metric and rationale.
3. Workers that need it declare `return '<metric>'` in `get_required_activity_metric()`.
4. No validator changes required — it's a string-equality check.

## Adding a New Worker

1. Inherit from `AbstractWorker` and override `get_required_activity_metric()`. Omitting it is a runtime error, caught at pre-flight.
2. If the worker is price-based, return `None`.
3. If it depends on an activity metric, return the exact string declared in `market_config.json`.
4. No further wiring — the factory resolves the class, the validator handles the rest.

## Related

- `python/framework/workers/abstract_worker.py` — classmethod contract
- `python/framework/validators/scenario_data_validator.py` — static `validate_worker_market_compatibility()`
- `python/framework/batch/requirements_collector.py` — owns `WorkerFactory` + `MarketConfigManager`, runs Step 1 per scenario
- `python/framework/data_preparation/aggregate_scenario_data_requirements.py` — receives the shared factory via constructor
- `python/framework/exceptions/market_compatibility_error.py` — structured error
- `configs/market_config.json` — metric declarations
- `docs/user_guides/worker_naming_doc.md` — user-facing worker authoring guide
- `docs/tests/framework/market_compatibility_tests.md` — test suite documentation
