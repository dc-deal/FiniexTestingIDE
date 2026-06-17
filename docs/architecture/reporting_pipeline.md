# Reporting Pipeline — One Result Model for Console / File / API (#391)

## Why

Run statistics used to be derived **and** formatted inside the console print step, so the API
would have to re-derive them and the two pipelines (simulation, AutoTrader) drifted apart. The
reporting pipeline separates the three concerns so every consumer renders **identical** data
from one model, with the derivation off the hot loop.

```
CAPTURE  (source-specific, raw)         DERIVE  (shared, pure)            PRESENT  (thin renderers)
  sim:  List[TradeRecord]  ──┐                                              ┌─► console (text)
        per scenario        ├─► postprocessor → ReportModel ───────────────┼─► CSV (table)
  live: List[TradeRecord]  ──┘   (off the hot loop)                         └─► API → frontend (JSON)
        the session
```

The migrated slices are **trade-history**, **order-history**, **portfolio**, and
**execution-stats**. The `RunUnit` abstraction (#391 Phase 2) now lets sim + live share the
*same* extraction for every section — see *Pipeline in detail* below.

> **Report pipeline ≠ live streaming export.** This pipeline is about the *report* — the
> derived artifact written at run end (and later snapshotted, #392). It is **not** the live
> telemetry stream (`process/process_live_export.py`, the AutoTrader `display_stats`), which
> pushes per-tick data into a queue for the live console view. The two never share code:
> one is a coherent, post-derived report; the other is a fast, lossy live feed.

## The pieces

| Layer | Unit | Role |
|---|---|---|
| Model | `framework/types/api/report_types.py` | the canonical, Pydantic, serializable models (the same models the API serves and the console/CSV render): `TradeHistoryReport`, `OrderHistoryReport`, `PortfolioReport`, `ExecutionStatsReport` |
| Run units | `framework/reporting/run_reports/run_unit.py` — `RunUnit` (+ `run_units_from_batch` / `run_units_from_session`) | the **unified per-unit source** (#391 Phase 2): the run extracted once into units (sim: N scenarios; live: 1 session), each carrying `name` · `symbol` · the raw trade / order / portfolio / execution sources. Every builder maps from these — no per-section extraction, no flat variants |
| Postprocessor | `framework/reporting/run_reports/{trade_history,order_history,portfolio,execution_stats}_report_builder.py` | **pure** derivation: `RunUnit`s → report. One `build_*_report(units, …)` per section. The shared filter (trade / order) lives here |
| Aggregators | `framework/reporting/run_reports/report_aggregators.py` | the **measures** over the report rows — one pure `aggregate_*(rows)` per section (trade analytics per currency, execution totals, portfolio per-currency roll-up). Ratios recomputed from summed components (mirrors the console `PortfolioAggregator`); the seam a future `RunSummary` composes from (#390) |
| IO | `framework/reporting/run_reports/{trade_history,order_history,portfolio,execution_stats}_report_io.py` | write the artifact(s); read back + filter (the API path) |
| Store | `framework/reporting/run_reports/report_store.py` — `ReportStore` | resolves a run's persisted artifacts under the logs tree (the API's read-only source) — `get_trade_history` / `get_order_history` / `get_portfolio` / `get_execution_stats` |
| Persist (sim) | `framework/batch/batch_report_coordinator.py` — `BatchReportCoordinator.generate_and_log()` | consumes the finished `BatchExecutionSummary`, derives + writes the artifacts + renders the console |
| Persist (live) | `framework/autotrader/reporting/autotrader_report_coordinator.py` — `AutotraderReportCoordinator.generate_and_log()` | the live mirror: consumes the finished `AutoTraderResult`, writes the same artifacts + renders the post-session console |
| API | `python/api/endpoints/reports_router.py` | `GET /api/v1/reports/runs/{run_id}/{trade-history,order-history,portfolio,execution-stats}` with section-specific filters |

The `framework/reporting/run_reports/` subfolder holds **only** the unified-report-pipeline units
(builders + io + store); the other `framework/reporting/*` files are unrelated reporting utilities
(diagnostics CSV, event-stream CSV, field-study, …).

## Pipeline in detail (with RunUnit)

The flow has four stages. **CAPTURE** is the only source-specific part; from the `RunUnit`
list onward everything is shared, so sim and live produce identical reports by construction.

```
CAPTURE  (source-specific, raw)
   sim:  BatchExecutionSummary                 live:  AutoTraderResult
         (N scenario results)                         (1 session)
              │                                            │
              │  run_units_from_batch                      │  run_units_from_session
              │  (symbol ← index-synced scenario)          │  (name/symbol ← profile)
              ▼                                            ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │  List[RunUnit]   — the unified per-unit source (#391 Phase 2)      │
   │     each unit:  name · symbol                                      │
   │                 trade_history · order_history                      │
   │                 portfolio_stats · execution_stats                 │
   └─────────────────────────────────────────────────────────────────┘
        │                 │                  │                  │
DERIVE  │  pure, off the hot loop — one builder per section, maps ONE unit
        ▼                 ▼                  ▼                  ▼
  build_trade_      build_order_       build_portfolio_   build_execution_
  history_report    history_report     report_from_*      stats_report
  (units, filters)  (units, filters)   (units, aggs)      (units)
        │                 │                  │                  │
        ▼                 ▼                  ▼                  ▼
  TradeHistory      OrderHistory       PortfolioReport    ExecutionStats
  Report            Report             (units + per-      Report
  (+ analytics)                         currency aggs)    (units + totals)
        └─────────────────┴─────────┬────────┴──────────────────┘
                                     │  the canonical models
PRESENT  (thin renderers — the SAME models on every surface)
                                     ├─► console      (BatchSummary / post-session)
                                     ├─► file log     (captured stdout, ANSI-stripped)
                                     ├─► CSV          (flat per-unit tables)
                                     └─► JSON artifact ─► ReportStore ─► API (reports_router)
```

- **CAPTURE → RunUnit:** the two `run_units_from_*` extractors are the *only* place that knows
  `BatchExecutionSummary` from `AutoTraderResult`. They resolve the per-unit identity once — the
  sim symbol comes from the index-synced `SingleScenario` (`ProcessResult` carries none); a
  scenario without a tick-loop result is skipped.
- **DERIVE:** each builder maps a single unit's source to rows; the **array model** keeps the
  units (sim: N, live: 1). Trade / order rows are tagged with their unit name (grouping);
  portfolio + execution carry the unit explicitly. The aggregates (portfolio per-currency
  roll-up, execution totals, trade analytics) are the shared **`report_aggregators`** — one pure
  `aggregate_*(rows)` per section (facts → measures), recomputing ratios from summed components,
  never re-deriving per surface. The cross-section run-wide KPI roll-up (`RunSummary`, #390
  prework) composes these once and is the seam every consumer reads (sweep objective, dashboard
  headline, live snapshot).
- **PRESENT:** every surface renders the *same* model — the file log is the captured console
  stdout (ANSI-stripped), CSV is the flat per-unit table, the API serves the persisted JSON via
  `ReportStore`. Adding a surface = adding a renderer over the model, never a re-derivation.

## Report sections — domain & migration status

Every section eventually flows through the pipeline so a frontend can render it — including
the per-pipeline ones. **Domain** says whether a section's data is shared (both pipelines) or
specific to one. **Status** tracks what is already on the model.

**Model** = a section has a derived model + JSON/CSV/API (#391/#389). **Console (#393)** = the
console + file-log render *from* that model (vs. their own inline derivation).

| Section | Underlying data | Domain | Model (JSON/CSV/API) | Console (#393) |
|---|---|---|---|---|
| Trade History (+ MAE/MFE/R analytics, #389) | `List[TradeRecord]` | unified | ✅ | ✅ renders from model (full audit table + #330 executions) |
| Order History | `List[OrderResult]` | unified | ✅ | ✅ (rejections, via the trade summary) |
| Portfolio / Headline | `PortfolioStats` (+ currency roll-up) | unified | ✅ | ⏳ deferred — console still derives inline (needs a PortfolioReport full projection: execution/cost/equity/pending/balances) |
| Execution Stats (order counts + SL/TP) | `ExecutionStats` | unified | ✅ | ⏳ deferred — counts still rendered inline in `portfolio_summary` |
| Warnings / Errors | §35 error pot | unified | ⏳ planned | — |
| Worker / Decision Stats | `WorkerPerformanceStats` / `DecisionLogicStats` | unified | ⏳ planned | — |
| Profiling / Warmup / Block-Splitting | profiling, coordination, warmup phases | **sim-only** | console-only (migrates later) | n/a |
| Shutdown / Emergency / Session | `shutdown_mode`, `emergency_reason`, session timing | **autotrader-only** | console-only (migrates later) | operational view stays; #389 analytics line model-sourced |

The **array model** is the unifier: a run is a list of units (sim: N scenarios; live: 1
session). Where a section carries per-unit meaning (portfolio breakdown) the model keeps the
units; for flat record lists (trades, orders) every row carries its `symbol` and the list is
filtered, not grouped. The generic `RunResult`/`RunUnit` abstraction that deduplicates the
per-source extraction follows once a third/fourth section justifies it (see *Phasing*).

## Both pipelines write the same artifacts

The shared inputs are the executor's `get_trade_history()` / `get_order_history()` and the
portfolio stats — the same objects both pipelines already produce. Each pipeline builds the
reports and persists them into its run directory:

- **Simulation** — `BatchReportCoordinator.generate_and_log()` aggregates records across scenarios
  and writes `trade_history.{json,csv}`, `order_history.{json,csv}`, `portfolio.json`, and
  `execution_stats.{json,csv}` at the run dir root (next to `events/`). The portfolio aggregate reuses the existing `PortfolioAggregator`
  (single source for the total), injected into the builder so the pipeline stays in its layer.
- **AutoTrader** — `autotrader_main._collect_results()` builds the `AutoTraderResult`, then
  `AutotraderReportCoordinator.generate_and_log()` writes the same artifacts at session end (the
  single session = one portfolio unit, which is its own currency aggregate). The two coordinators
  are the symmetric per-pipeline persist units — same model, same artifacts, one per pipeline.

The `ReportStore` resolves runs at `<logs_root>/{scenario_sets,autotrader}/<owner>/<run_id>/`, so
the API serves either pipeline's run by `run_id`.

## Consumers — same data everywhere

- **API** — serves the Pydantic models (→ JSON), filters applied server-side so the frontend
  renders rather than derives.
- **CSV** — the flat tables (`trade_history.csv`, `order_history.csv`) have the exact columns of
  their models. Portfolio is JSON-only — units + per-currency aggregates are two sections, not one
  flat table.
- **Console** — the API/CSV trade table is the lean *trade list*. The console's existing
  `trade_history_summary` per-scenario table is a richer **P&L-verification** view (more columns).
  With the trade-analytics column set now settled (#389 done), migrating it onto the model is
  **#393**'s job. The richer per-pipeline console boxes stay console-specific until their sections
  migrate (see the taxonomy table).

## Tests

- `tests/framework/reporting/` — the postprocessors + IO + store, with hand-built fixtures (no run
  required): mapping (incl. None-safe / rejected orders), the filter paths, CSV mirror, the
  portfolio array model (units + per-currency aggregates), artifact resolution.
- `tests/framework/api/test_reports_endpoint.py` — the endpoints via TestClient against fixture
  artifacts (happy path, filtering, 404, invalid-input) for all three sections.

## Phasing (#391)

1. **Trade / order / portfolio slices (done)** — models + postprocessors + IO/store + API
   endpoints + persist in both pipelines.
2. **`RunResult` split (done)** — the per-unit extraction is unified in
   `run_reports/run_unit.py` (`RunUnit` + `run_units_from_batch` / `run_units_from_session`):
   the run is extracted into units once (symbol resolved from the index-synced scenario for
   sim), and every section builder maps from the shared units — no per-source duplication, no
   flat builder variants. The aggregate handling (portfolio per-currency roll-up) is the next
   target (the aggregator layer).
3. **Trade analytics (#389, done)** — MAE/MFE **tracked** on the Position each tick (runtime,
   shared layer) + `initial_risk` stamped at close; R-multiple / expectancy **derived** in the
   postprocessor. Surfaced as per-row columns (`mae_*`/`mfe_*`/`r_multiple`) + a `TradeAnalytics`
   aggregate on `TradeHistoryReport`. Console display follows with #393. Pips are a forex-convention
   approximation (`10^-(digits-1)`); exact per-symbol `pip_size` is #167.
4. **Live on-demand snapshot (#392)** — bounded in-memory window + flush, so a months-long session
   can render the report at any time (between-ticks consistent read).
5. The remaining report sections (execution stats, warnings, worker/decision, then the per-pipeline
   ones) migrate to the model; the visual channel (#379) consumes the API.
6. **Console / file renderers from the model (#393, in progress)** — **trade-history** (the sim
   audit table incl. #330 execution sub-lines + the #389 analytics block) + **order rejections**
   now render *from* the model; the **AutoTrader** post-session summary gains a model-sourced #389
   analytics line. **Portfolio** console migration is **deferred** (needs a PortfolioReport full
   projection: execution / cost / equity / pending / balances) — it stays on the inline path. The
   file-logs follow automatically (captured stdout).
