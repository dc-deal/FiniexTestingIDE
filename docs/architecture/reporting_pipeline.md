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

The first slices are the **trade-history**, **order-history**, and **portfolio** sections.
The full report model and the `RunResult` abstraction that lets sim + live share *all*
sections follow (see *Phasing*).

> **Report pipeline ≠ live streaming export.** This pipeline is about the *report* — the
> derived artifact written at run end (and later snapshotted, #392). It is **not** the live
> telemetry stream (`process/process_live_export.py`, the AutoTrader `display_stats`), which
> pushes per-tick data into a queue for the live console view. The two never share code:
> one is a coherent, post-derived report; the other is a fast, lossy live feed.

## The pieces

| Layer | Unit | Role |
|---|---|---|
| Model | `framework/types/api/report_types.py` | the canonical, Pydantic, serializable models (the same models the API serves and the console/CSV render): `TradeHistoryReport`, `OrderHistoryReport`, `PortfolioReport` |
| Postprocessor | `framework/reporting/run_reports/{trade_history,order_history,portfolio}_report_builder.py` | **pure** derivation: records → report + the shared filter. Trade/order builders are flat record lists; the portfolio builder has two source variants (`*_from_batch` / `*_from_session`) feeding the array model |
| IO + extract | `framework/reporting/run_reports/{trade_history,order_history,portfolio}_report_io.py` | extract the shared records from a sim batch or a live session; write the artifact(s); read + filter |
| Store | `framework/reporting/run_reports/report_store.py` — `ReportStore` | resolves a run's persisted artifacts under the logs tree (the API's read-only source) — `get_trade_history` / `get_order_history` / `get_portfolio` |
| Persist (sim) | `framework/batch/batch_report_coordinator.py` — `BatchReportCoordinator.generate_and_log()` | consumes the finished `BatchExecutionSummary`, derives + writes the artifacts + renders the console |
| Persist (live) | `framework/autotrader/reporting/autotrader_report_coordinator.py` — `AutotraderReportCoordinator.generate_and_log()` | the live mirror: consumes the finished `AutoTraderResult`, writes the same artifacts + renders the post-session console |
| API | `python/api/endpoints/reports_router.py` | `GET /api/v1/reports/runs/{run_id}/{trade-history,order-history,portfolio}` with section-specific filters |

The `framework/reporting/run_reports/` subfolder holds **only** the unified-report-pipeline units
(builders + io + store); the other `framework/reporting/*` files are unrelated reporting utilities
(diagnostics CSV, event-stream CSV, field-study, …).

## Report sections — domain & migration status

Every section eventually flows through the pipeline so a frontend can render it — including
the per-pipeline ones. **Domain** says whether a section's data is shared (both pipelines) or
specific to one. **Status** tracks what is already on the model.

| Section | Underlying data | Domain | Status |
|---|---|---|---|
| Trade History | `List[TradeRecord]` | unified | ✅ migrated |
| Order History | `List[OrderResult]` | unified | ✅ migrated |
| Portfolio / Headline | `PortfolioStats` (+ currency roll-up) | unified | ✅ migrated |
| Execution Stats | `ExecutionStats` | unified | ⏳ planned |
| Warnings / Errors | §35 error pot | unified | ⏳ planned |
| Worker / Decision Stats | `WorkerPerformanceStats` / `DecisionLogicStats` | unified | ⏳ planned |
| Profiling / Warmup / Block-Splitting | profiling, coordination, warmup phases | **sim-only** | console-only (migrates later) |
| Shutdown / Emergency / Session | `shutdown_mode`, `emergency_reason`, session timing | **autotrader-only** | console-only (migrates later) |

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
  and writes `trade_history.{json,csv}`, `order_history.{json,csv}`, and `portfolio.json` at the run
  dir root (next to `events/`). The portfolio aggregate reuses the existing `PortfolioAggregator`
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
  `trade_history_summary` per-scenario table is a richer **P&L-verification** view (more columns);
  migrating it onto the model is deferred until the model's canonical column set settles with the
  trade-analytics work (MAE/MFE + R-multiple, #389). The richer per-pipeline console boxes stay
  console-specific until their sections migrate (see the taxonomy table).

## Tests

- `tests/framework/reporting/` — the postprocessors + IO + store, with hand-built fixtures (no run
  required): mapping (incl. None-safe / rejected orders), the filter paths, CSV mirror, the
  portfolio array model (units + per-currency aggregates), artifact resolution.
- `tests/framework/api/test_reports_endpoint.py` — the endpoints via TestClient against fixture
  artifacts (happy path, filtering, 404, invalid-input) for all three sections.

## Phasing (#391)

1. **Trade / order / portfolio slices (done)** — models + postprocessors + IO/store + API
   endpoints + persist in both pipelines.
2. **`RunResult` split** — abstract the run result (sim = N scenario units + extras; live = 1
   session unit + extras) into a generic per-unit currency, deduplicating the per-source
   extraction once more sections justify it.
3. **Trade analytics (#389)** — MAE/MFE + R-multiple/expectancy as the model's analytics columns.
4. **Live on-demand snapshot (#392)** — bounded in-memory window + flush, so a months-long session
   can render the report at any time (between-ticks consistent read).
5. The remaining report sections (execution stats, warnings, worker/decision, then the per-pipeline
   ones) migrate to the model; the visual channel (#379) consumes the API.
6. **Console / file renderers from the model (#393)** — migrate the console summaries + their
   file-log copies (`scenario_summary.log` / `autotrader_summary.log`) to render *from* the model,
   collapsing today's parallel console-side derivation so #389 analytics reach console + file too.
