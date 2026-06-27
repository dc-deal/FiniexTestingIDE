# Test Taxonomy

All tests are classified by **pipeline domain** and **test type**. pytest marks are applied automatically by `tests/conftest.py` based on the file path — no marks needed in individual test files.

---

## Mark Reference

| Mark | Applied to | Run with |
|---|---|---|
| `simulation` | `tests/simulation/` | `pytest -m simulation` |
| `autotrader` | `tests/autotrader/` | `pytest -m autotrader` |
| `parity` | `tests/parity/` | `pytest -m parity` |
| `framework` | `tests/framework/` | `pytest -m framework` |
| `data` | `tests/data/` | `pytest -m data` |
| `benchmark` | `tests/simulation/benchmark/` | excluded from normal runner |
| `live_adapter` | `tests/live_adapters/` | excluded from normal runner — requires real account |
| `live_field_study` | `tests/live_field_study/` | excluded from normal runner — operator-driven live release gate (#332) |
| `integration` | any path containing `/integration/` | `pytest -m integration` |
| `unit` | order_guard, live_executor, safety, bar_rendering, workers, etc. | `pytest -m unit` |

---

## Test Matrix

```
                      unit   integration   parity   live-api   benchmark
simulation             ✓          ✓                               ✓
autotrader             ✓          ✓            ✓        ✓
framework              ✓
data                   ✓          ✓
live_adapters                                            ✓
```

**Axes:**
- **Horizontal (test type):** unit → isolated component; integration → full pipeline end-to-end; parity → sim vs. AT identical output; live-api → real broker contract (not in normal runner); benchmark → throughput regression
- **Vertical (pipeline domain):** which world(s) the test exercises

---

## Suite Directory Map

```
tests/
├── simulation/
│   ├── baseline/          integration — deterministic trade sequence, P&L, order flow
│   ├── margin_validation/ integration — margin check, rejection, zero-balance
│   ├── multi_position/    integration — concurrent positions, close sequences
│   ├── partial_close/     integration — partial close mechanics
│   ├── sltp_limit_validation/ integration — SL/TP + limit/stop order semantics
│   ├── spot_trading/      integration — spot sell sequences, dual-balance
│   ├── active_order_display/  integration — order display in scenario summary
│   ├── pending_stats/     integration — pending order statistics
│   ├── modify_lifecycle/  unit — async modify/cancel scheduling + resolution (#318)
│   ├── trade_emission/    unit — sim BrokerTrade emission via shared _fill_open_order (#326)
│   ├── tick_clipping/     unit — bar rendering ordering guard (#293 regression)
│   ├── order_precision/   unit — order price → digits normalization (#332)
│   ├── event_channel/     integration — decision event channel dual-world parity (#348)
│   ├── optimization/      unit — parameter optimization: grid expand, override, ledger, ranking, sensitivity, grid validation (#390)
│   ├── swap_cost/         unit — overnight swap accrual: debit/credit/triple, spot=0, determinism (#365)
│   ├── robustness/        unit — multi-window + IS/OOS validation: roles, distribution, WFE, constancy, verdict (#367)
│   ├── trend_channel_reference/ integration — didactic full-order-surface reference: LIMIT/STOP entries, SL/TP, trailing, partial, multi-position (#118)
│   └── benchmark/         benchmark — throughput regression (excluded from runner)
│
├── autotrader/
│   ├── integration/       integration — mock session, trade lifecycle, trade scenarios (full pipeline)
│   ├── live_executor/     unit — LiveTradeExecutor, LiveRequestProcessor, async submit/modify/cancel/trades_query/polling_cadence/drift_audit/decision_event_dispatcher (#319, #321, #318, #326, #320, #327, #348)
│   ├── loop_cadence/      unit — clock injection + heartbeat re-poll + decision ghost-pass cadence (#360)
│   ├── order_guard/       unit — OrderGuard scenarios and unit cases
│   ├── safety/            unit — circuit breaker (margin + spot)
│   ├── reconciliation/    unit — broker truth-pull + Reconciler ALERT_ONLY (#151)
│   ├── api_monitor/       unit — broker REST latency/error telemetry (#351)
│   ├── field_study_machine/  unit — Field Study phase state machine (#332)
│   └── kraken_adapter/    unit — Kraken private-call nonce monotonicity + lock (#332)
│
├── parity/                parity — simulation vs. AutoTrader identical output (#294, #318, #326, #360 sim ghost-pass)
│
├── framework/
│   ├── bar_rendering/     unit — BarRenderingController consistency
│   ├── batch_validations/ unit — ScenarioValidator, BrokerDataPreparator (Phase 0 batch pipeline)
│   ├── config/            unit — execution_config 3-level cascade (#137)
│   ├── worker_tests/      unit — worker computation, parameter schema, factory
│   ├── market_calendar/  unit — swap-rollover + DST calendar helpers + MarketClock awareness (#365)
│   ├── market_compatibility/ unit — market activity metric, validator
│   ├── tick_parquet_reader/  unit — parquet reader normalization
│   ├── user_namespace/    unit — USER worker/decision discovery
│   ├── api/               unit — REST API endpoints
│   ├── live_telemetry/    unit — live-telemetry frame serializer (frame_to_json, #400)
│   ├── field_study_recorder/ unit — Field Study JSONL recorder + certificate analyzer (#332)
│   ├── algo_clock/        unit — §9 wall-clock ban lint (decision logic/workers, CI plane)
│   └── algo_clock_validator/ unit — §9 runtime startup validator: AST scan of loaded algos (CORE + USER) + batch pre-flight (#359)
│
├── data/
│   ├── import_pipeline/   unit + integration — tick import, duplicate detection
│   ├── data_integration/  integration — volume integrity
│   ├── inter_tick_interval/   unit — interval stats
│   ├── scenario_generator/    unit — block generation
│   └── tick_processing_budget/ unit — budget filtering
│
├── live_adapters/         live-api — real broker API validation (excluded from runner)
│   ├── test_kraken_adapter_order_lifecycle_dry.py    AddOrder dry-run (validate=true), no funds
│   ├── test_kraken_adapter_order_lifecycle_live.py   Full lifecycle, real orders, no fills
│   ├── test_kraken_adapter_order_lifecycle_fill.py   Fill validation, real MARKET execution
│   └── reports/           release receipt JSON files (committed per release)
│
└── live_field_study/      acceptance — operator-driven live release gate (excluded from runner) (#332)
    ├── test_field_study_certificate.py  CI-friendly committed-certificate validation
    └── reports/           PASS/FAIL acceptance certificates (committed per release)
```

---

## Parity Suite — Category Notes

Parity tests (`tests/parity/`) are the only tests that exercise **both** pipelines simultaneously. They prove that simulation and AutoTrader produce identical output given identical input. See [bar_parity_tests.md](parity/bar_parity_tests.md) for the full matrix.

Parity tests complement shared code — they are not a substitute. See `docs/architecture/simulation_vs_live_flow.md` for the architectural rationale.

---

## Adding New Tests

When adding a new test suite:

1. Place it under the correct pipeline domain directory (`tests/simulation/`, `tests/autotrader/`, etc.)
2. The root `tests/conftest.py` marks it automatically — no action needed
3. If a new top-level category is added (rare), update `tests/conftest.py` and this document
4. Add an entry to this matrix table and the suite directory map above
