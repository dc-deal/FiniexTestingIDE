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
│   ├── tick_clipping/     unit — bar rendering ordering guard (#293 regression)
│   └── benchmark/         benchmark — throughput regression (excluded from runner)
│
├── autotrader/
│   ├── integration/       integration — mock session, trade lifecycle (full pipeline)
│   ├── live_executor/     unit — LiveTradeExecutor, LiveOrderTracker
│   ├── order_guard/       unit — OrderGuard scenarios and unit cases
│   └── safety/            unit — circuit breaker (margin + spot)
│
├── parity/                parity — simulation vs. AutoTrader identical output (#294)
│
├── framework/
│   ├── bar_rendering/     unit — BarRenderingController consistency
│   ├── worker_tests/      unit — worker computation, parameter schema, factory
│   ├── market_compatibility/ unit — market activity metric, validator
│   ├── tick_parquet_reader/  unit — parquet reader normalization
│   ├── user_namespace/    unit — USER worker/decision discovery
│   └── api/               unit — REST API endpoints
│
├── data/
│   ├── import_pipeline/   unit + integration — tick import, duplicate detection
│   ├── data_integration/  integration — volume integrity
│   ├── inter_tick_interval/   unit — interval stats
│   ├── scenario_generator/    unit — block generation
│   └── tick_processing_budget/ unit — budget filtering
│
└── live_adapters/         live-api — real broker API validation (excluded from runner)
    ├── test_kraken_adapter_order_lifecycle.py   Phase 1: AddOrder dry-run (validate=true)
    └── reports/           release receipt JSON files (committed per release)
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
