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

The migrated slices are **trade-history**, **order-history**, **portfolio**, **execution-stats**,
**pending-orders**, **scenario-details**, and the cross-section **run-summary**. The `RunUnit`
abstraction (#391 Phase 2, done) lets sim + live share the *same* extraction for every section —
see *Pipeline in detail* below.

> **Report pipeline ≠ live streaming export.** This pipeline is about the *report* — the
> derived artifact written at run end (and later snapshotted, #392). It is **not** the live
> telemetry stream ([live_telemetry_architecture.md](live_telemetry_architecture.md)), which
> pushes per-tick data into a queue for the live console view. The two never share code:
> one is a coherent, post-derived report; the other is a fast, lossy live feed.

## The pieces

| Layer | Unit | Role |
|---|---|---|
| Model | `framework/types/api/report_types.py` | the canonical, Pydantic, serializable models (the same models the API serves and the console/CSV render): `TradeHistoryReport`, `OrderHistoryReport`, `PortfolioReport` (full per-unit projection), `ExecutionStatsReport`, `PendingOrdersReport`, `ScenarioDetailsReport`, `RunSummary` (cross-section KPIs), `WorkerDecisionReport` (per-unit worker + decision performance), `ProfilingReport` (per-unit operation timing + inter-tick + clipping + run-level aggregate + warmup, sim-only) |
| Run units | `framework/reporting/run_reports/run_unit.py` — `RunUnit` (+ `run_units_from_batch` / `run_units_from_session`) | the **unified per-unit source** (#391 Phase 2): the run extracted once into units (sim: N scenarios; live: 1 session), each carrying `name` · `symbol` · the raw trade / order / portfolio / execution sources. Every builder maps from these — no per-section extraction, no flat variants |
| Postprocessor | `framework/reporting/run_reports/{trade_history,order_history,portfolio,execution_stats,pending_orders,worker_decision,scenario_details,profiling,broker,warnings_errors,aggregated_portfolio}_report_builder.py` | **pure** derivation: `RunUnit`s → report. One `build_*_report(units, …)` per section. The shared filter (trade / order) lives here. `scenario_details` / `profiling` / `broker` / `warnings_errors` are the exceptions — not via `RunUnit`: they read the batch directly (failed scenarios carry no `RunUnit`; `warnings_errors` reads the validation channels + log pots — the verdicts are decided by validators upstream, never here). `aggregated_portfolio` rolls up the per-unit portfolio / execution / pending **rows** |
| Aggregators | `framework/reporting/run_reports/report_aggregators.py` | the **measures** over the report rows — one pure `aggregate_*(rows)` per section (trade analytics per currency incl. P&L totals, execution totals, the lean portfolio per-currency roll-up + the rich `aggregate_full_portfolio` for #397). Ratios recomputed from summed components (byte-identical to the retired console `PortfolioAggregator`) |
| Run summary | `framework/reporting/run_reports/run_summary_builder.py` — `build_run_summary()` | the **cross-section KPI** composer (#390 prework): joins the per-section aggregates (portfolio roll-up + trade analytics + execution totals) into one run-wide `RunSummary` (per-currency KPIs + global counts) — composes, never re-derives. The single object the sweep / API / console headline reads |
| IO | `framework/reporting/io/{trade_history,order_history,portfolio,execution_stats,pending_orders,scenario_details,run_summary,run_meta,worker_decision,profiling,broker,warnings_errors,aggregated_portfolio,block_splitting}_report_io.py` | write the artifact(s); read back + filter (the API path) |
| Store | `framework/reporting/io/report_store.py` — `ReportStore` | resolves a run's persisted artifacts under the logs tree (the API's read-only source) — `get_trade_history` / `get_order_history` / `get_portfolio` / `get_execution_stats` / `get_pending_orders` / `get_scenario_details` / `get_run_summary` / `get_worker_decision` / `get_profiling` / `get_broker` / `get_warnings_errors` / `get_aggregated_portfolio` |
| Ledger | `framework/reporting/io/run_results_ledger.py` — `RunResultsLedger` | the **cross-run** PERSIST sink (#390): appends one flat row per (run × currency) — the `RunSummary` KPIs + provenance (`param_hash`, git, component versions, config snapshot, sweep tagging) — to `data/run_results/` as one parquet fragment per run. Separate from the per-run API artifacts above; it is the substrate the Parameter Optimization system ranks over (provenance via `run_provenance_builder.py`). See [Parameter Optimization System](parameter_optimization_system.md) |
| Console | `framework/reporting/console/*_summary.py` (+ `executive_summary` / `execution_header_summary` / `block_splitting_disposition`) | the **PRESENT** sub-presenters, orchestrated by `BatchReportCoordinator` (the former `BatchSummary`, folded into the coordinator) |
| Persist (sim) | `framework/batch/batch_report_coordinator.py` — `BatchReportCoordinator.generate_and_log()` | consumes the finished `BatchExecutionSummary`, derives + writes the artifacts + renders the console |
| Persist (live) | `framework/autotrader/reporting/autotrader_report_coordinator.py` — `AutotraderReportCoordinator.generate_and_log()` | the live mirror: consumes the finished `AutoTraderResult`, writes the same artifacts + renders the post-session console |
| API | `python/api/endpoints/reports_router.py` | `GET /api/v1/reports/runs/{run_id}/{trade-history,order-history,portfolio,execution-stats,pending-orders,scenario-details,run-summary,worker-decision,profiling}` with section-specific filters |

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

### Where the aggregates + run summary are wired in (one place)

Both coordinators' `generate_and_log()` is the **single composition point** (the DERIVE stage):
it builds the `RunUnit`s once, calls each section builder (whose `aggregate_*` produces that
section's per-currency / total **measures**, e.g. `TradeAnalytics`, the portfolio roll-up,
`ExecutionStatsTotals`), and finally `build_run_summary(...)` composes those aggregates into the
run-wide `RunSummary`. The **same model objects** are then both (a) **injected into the console
renderers** (`BatchSummary` → the per-section presenters) and (b) **persisted as JSON** for the
API/`ReportStore`. So a console renderer **never re-computes a total it can read off the model** —
e.g. the per-currency trade P&L totals come straight from `TradeAnalytics`, not a row re-sum.

- **sim:** `framework/batch/batch_report_coordinator.py` — `BatchReportCoordinator.generate_and_log()`
- **live:** `framework/autotrader/reporting/autotrader_report_coordinator.py` — `AutotraderReportCoordinator.generate_and_log()`

Both granularities are model-served, by their own aggregate: the **per-currency** trade totals
live on `TradeAnalytics`, the **per-scenario** table footer on `TradeScenarioTotals` — so neither
the console nor a frontend re-sums. **Ordering** (sorting the rows) is the one thing that stays a
*presentation* concern in each renderer: the model carries the rows, not a fixed sort (the console
sorts chronologically; a frontend may sort differently).

## Report sections — domain & migration status

Every section eventually flows through the pipeline so a frontend can render it — including
the per-pipeline ones. **Domain** says whether a section's data is shared (both pipelines) or
specific to one. **Status** tracks what is already on the model.

**Model** = a section has a derived model + JSON/CSV/API (#391/#389). **Console (#393)** = the
console + file-log render *from* that model (vs. their own inline derivation). **Remaining** = the
open work to finish migrating the section (issue ref where one exists; ✅ = done).

| Section | Domain | Model | Console | Remaining work |
|---|---|---|---|---|
| Trade History (#389 analytics) | unified | ✅ | ✅ | offload the still-inline per-currency aggregates: trade-breakdown counts · duration · slippage distribution · rejection-by-reason |
| Order History | unified | ✅ | ✅ | — |
| Portfolio — per-scenario | unified | ✅ | ✅ (linear, boxes removed) | — |
| Portfolio — aggregated (by currency) | sim | ✅ (`AggregatedPortfolioReport`) | ✅ from the model (byte-identical; `PortfolioAggregator` retired) | — |
| Pending Orders / Active | unified (sim-populated) | ✅ | ✅ | — |
| Execution Stats — per-scenario | unified | ✅ | ✅ | — |
| Execution — aggregated ORDER EXECUTION | sim | ✅ (in `AggregatedPortfolioReport`) | ✅ from the model | — (folded into the portfolio aggregate, #397) |
| Scenario Details | **sim-only** | ✅ | ✅ (linear, incl. failed + `account_currency` hint) | — |
| Run Summary (#390) | unified | ✅ | ✅ executive headline | — |
| Worker / Decision (#398/#399) | unified | ✅ (`WorkerDecisionReport`) | ✅ — `performance_summary` (worker details / aggregated / bottleneck) + the breakdown both read the model (overhead Total from the profiling model, #399); the duplicate per-scenario worker list was removed | — |
| Profiling — operations + inter-tick + clipping (#399) | **sim-only** | ✅ (`ProfilingReport`) | ✅ from the model | — |
| Warmup phases (#399) | **sim-only** | ✅ (in `ProfilingReport`) | ✅ from the model (`ProfilingSummary.render_warmup`; `warmup_phase_summary` retired) | — |
| Block-Splitting Disposition | **sim-only** (Profile Run) | ⏳ | ✅ inline | **separate follow-up** — generation-quality metric (`generator_profiles` + `block_boundary_report`), not runtime profiling |
| Broker Configuration | unified | ✅ (`BrokerReport`) | ✅ from the model (sim full table · live compact line) | — |
| Warnings & Errors | unified | ✅ (`WarningsErrorsReport`) | ✅ from the model — tiered (errors / Tier-1 major / Tier-2 minor); executive failed-scenario headline reads the model outcome; warnings lifted into validators (`PostRunValidator`), the orchestrator keeps only a thin global-log line (#395) | live render still reads session buffers (same source) |
| Executive — detailed portfolio-performance block | **sim-only** | ✅ (`AggregatedPortfolioReport`) | ✅ from the model (margin / spot / mixed preserved, byte-identical) | — (#397) |
| Shutdown / Emergency / Session | **autotrader-only** | ⏳ | ✅ inline | migrates later (live post-session); #389 analytics line already model-sourced |
| **Final:** directory consolidation | — | — | — | ✅ **#396 DONE** — `batch_reporting/` folded into `framework/reporting/` by stage: `run_reports/` (DERIVE) · `io/` (PERSIST) · `console/` (PRESENT) |

The **array model** is the unifier: a run is a list of units (sim: N scenarios; live: 1
session). Where a section carries per-unit meaning (portfolio breakdown) the model keeps the
units; for flat record lists (trades, orders) every row carries its `symbol` and the list is
filtered, not grouped. The generic `RunUnit` abstraction that deduplicates the per-source
extraction is **implemented** (`run_reports/run_unit.py`); every builder maps from it.

## Both pipelines write the same artifacts

The shared inputs are the executor's `get_trade_history()` / `get_order_history()` and the
portfolio stats — the same objects both pipelines already produce. Each pipeline builds the
reports and persists them into its run directory:

- **Simulation** — `BatchReportCoordinator.generate_and_log()` aggregates records across scenarios
  and writes `trade_history.{json,csv}`, `order_history.{json,csv}`, `portfolio.json`, and
  `execution_stats.{json,csv}` at the run dir root (next to `events/`). The aggregated per-currency
  portfolio (`aggregated_portfolio.json`, #397) is rolled up from the per-unit rows — `PortfolioAggregator` retired.
- **AutoTrader** — `autotrader_main._collect_results()` builds the `AutoTraderResult`, then
  `AutotraderReportCoordinator.generate_and_log()` writes the same artifacts at session end (the
  single session = one portfolio unit, which is its own currency aggregate). The two coordinators
  are the symmetric per-pipeline persist units — same model, same artifacts, one per pipeline.

The `ReportStore` resolves runs at `<logs_root>/{scenario_sets,autotrader}/<owner>/<run_id>/`, so
the API serves either pipeline's run by `run_id`.

**Cross-run ledger (#390).** Beyond the per-run artifacts, `BatchReportCoordinator` also appends the
run to the **Run Results Ledger** (`data/run_results/`) via `RunResultsLedger.append()` — the same
`RunSummary` model plus provenance. This is a separate, accumulating store (one parquet fragment per
run), the substrate the Parameter Optimization system ranks over. v0 wires the sim pipeline only;
the live coordinator can append later through the same sink. See
[Parameter Optimization System](parameter_optimization_system.md).

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
  portfolio array model (units + per-currency aggregates), the run-summary composition, the
  scenario-details rows, the broker configuration rows, artifact resolution.
- `tests/framework/api/test_reports_endpoint.py` — the endpoints via TestClient against fixture
  artifacts (happy path, filtering, 404, invalid-input) across the migrated sections.

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
5. The remaining report sections (block-splitting / the executive's detailed portfolio block)
   migrate to the model; the visual channel (#379) consumes the API.
6. **Console / file renderers from the model (#393, in progress)** — **done:** **trade-history**
   (audit table + #330 execution sub-lines + #389 analytics block), **order rejections**,
   **portfolio** per-scenario (linear, boxes removed), **scenario-details** (linear, incl. failed
   scenarios), **pending-orders**, **execution-stats** per-scenario, the **AutoTrader**
   post-session #389 line, the **run-summary** headline opening the executive section, the
   **worker/decision** breakdown (fully model-fed — overhead Total from the profiling model, #399),
   **profiling** (operations + inter-tick + clipping, #399), **warmup** (folded into the profiling
   model, `warmup_phase_summary` retired, #399 3c), **performance** (worker/decision detail +
   aggregate + bottleneck now model-fed, the duplicate per-scenario worker list removed, #399 3d),
   and the **broker** configuration section (`BrokerReport`, both pipelines — sim renders the full
   table, the live post-session summary a compact broker/symbol line; `broker.json` written by both,
   the AutoTrader `broker_config` threaded in from the executor), and **warnings & errors**
   (`WarningsErrorsReport`, both pipelines — tiered; the inline warning checks were lifted into
   `PostRunValidator`, the executive failed-headline reads the model outcome, the orchestrator keeps
   a thin global-log line, #395). The decision **smells** in the already-migrated profiling /
   worker-breakdown renderers were eliminated too: the `is_high_overhead` (>50%) and the bottleneck
   `critical/optimize/review` **verdicts** moved into `PostRunValidator` advisories — the reports now
   show only the calculation (overhead %, bottleneck frequency) plus a display class (hot-path vs
   infra); `EXPECTED_OPERATIONS` consolidated to one source. **#397 done:** the cross-domain
   **portfolio aggregated** + ORDER EXECUTION block + the executive's **detailed** portfolio block
   render from `AggregatedPortfolioReport` (built from the per-unit rows, byte-identical, margin /
   spot / mixed preserved); `PortfolioAggregator` retired; the multi-currency + time-divergence
   notices moved to `PostRunValidator`. The cost breakdown now splits the **maker / taker** fee
   (spot): `CostBreakdown` / `PortfolioStats` / the portfolio rows carry `maker_fee` + `taker_fee`
   (`PortfolioManager._record_fee_cost` categorizes every fee — maker/taker by `is_maker`), and all
   five categories (spread · commission · swap · maker · taker) render together (zeros where n/a), so
   the spot fee that used to fold invisibly into `total_fees` is now itemized.
   Block-splitting disposition is also model-fed now (`BlockSplittingReport`, Profile Runs only).
   File-logs follow automatically (captured stdout).
7. **Directory consolidation (#396, final, structural) — DONE.** `framework/batch_reporting/` was
   folded into one `framework/reporting/` home organized by stage: `run_reports/` (DERIVE — builders
   + `report_aggregators` + `run_unit`) · `io/` (PERSIST — the `*_report_io` + `report_store`) ·
   `console/` (PRESENT — the `*_summary` presenters). The former `BatchSummary` orchestrator was
   dissolved into `BatchReportCoordinator` (sub-presenters stay; render orchestration in
   `generate_and_log`). Pure file-move + import refactor; the models stay in `types/api/report_types.py`.
   **On disk:** the persisted report artifacts (JSON + CSV) now live in the run's **`io/`** subfolder
   (`<run_dir>/io/`, the `IO_SUBDIR` constant in `report_store`) — both pipelines write there and
   `ReportStore` reads from there; `events/`, `scenario_logs/` and the run logs stay at the run-dir root.
