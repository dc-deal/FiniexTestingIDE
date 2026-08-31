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
| Model | `framework/types/api/report_types.py` | the canonical, Pydantic, serializable models (the same models the API serves and the console/CSV render). **Every artifact model inherits `RunScopedReport`, whose one required field is `run_id`** (#475): a body that does not name its run cannot be checked against the run that was asked for — measured, two sweep combinations produce byte-identical portfolio bodies, so a consumer receiving the wrong one has nothing to notice it by. The route is not proof; the payload is. Inheriting also puts `run_id` FIRST in every serialized artifact, and the three CSVs carry it as their first COLUMN, on every row, because a CSV is the format that gets exported and merged. Models: `TradeHistoryReport`, `OrderHistoryReport`, `PortfolioReport` (full per-unit projection), `ExecutionStatsReport`, `PendingOrdersReport`, `ScenarioDetailsReport`, `RunSummary` (cross-section KPIs), `WorkerDecisionReport` (per-unit worker + decision performance — incl. the #420 cadence telemetry: per-worker `compute_basis`, compute/tick ratio, last-compute idle), `ProfilingReport` (per-unit operation timing + inter-tick + clipping + run-level aggregate + warmup, sim-only) |
| Run units | `framework/reporting/builders/run_unit.py` — `RunUnit` (+ `run_units_from_batch` / `run_units_from_session`) | the **unified per-unit source** (#391 Phase 2): the run extracted once into units (sim: N scenarios; live: 1 session), each carrying `name` · `symbol` · the raw trade / order / portfolio / execution sources. Every builder maps from these — no per-section extraction, no flat variants |
| Postprocessor | `framework/reporting/builders/{trade_history,order_history,portfolio,execution_stats,pending_orders,worker_decision,scenario_details,profiling,broker,warnings_errors,aggregated_portfolio}_report_builder.py` | **pure** derivation: `RunUnit`s → report. One `build_*_report(run_id, units, …)` per section — `run_id` leads, because the model requires it. The shared filter (trade / order) lives here. `scenario_details` / `profiling` / `broker` / `warnings_errors` are the exceptions — not via `RunUnit`: they read the batch directly (failed scenarios carry no `RunUnit`; `warnings_errors` reads the validation channels + log pots — the verdicts are decided by validators upstream, never here). `aggregated_portfolio` rolls up the per-unit portfolio / execution / pending **rows** |
| Aggregators | `framework/reporting/builders/report_aggregators.py` | the **measures** over the report rows — one pure `aggregate_*(rows)` per section (trade analytics per currency incl. P&L totals, execution totals, the lean portfolio per-currency roll-up + the rich `aggregate_full_portfolio` for #397). Ratios recomputed from summed components (byte-identical to the retired console `PortfolioAggregator`) |
| Run summary | `framework/reporting/builders/run_summary_builder.py` — `build_run_summary()` | the **cross-section KPI** composer (#390 prework): joins the per-section aggregates (portfolio roll-up + trade analytics + execution totals) into one run-wide `RunSummary` (per-currency KPIs + global counts) — composes, never re-derives. The single object the sweep / API / console headline reads |
| Shared core | `framework/reporting/shared_report_coordinator.py` — `SharedReportCoordinator.derive_and_persist(run_id, units, io_dir, signal_scenario_map)` (+ `builders/unified_reports.py` — `UnifiedReports`) | the **units-derived DERIVE+PERSIST core both pipelines delegate to** (#403): builds + writes the 9 sections identical across sim + live (trade / order / portfolio / pending / execution-stats / run-summary / worker-decision / signal / feed-stability) and returns them as `UnifiedReports`, which each coordinator reuses for its own console + ledger. Its `record_run_artifacts(run_dir)` is called LAST by each pipeline — deliberately not inside `derive_and_persist`, because both pipelines write further artifacts of their own after it returns, so a list taken there would be short by exactly those |
| IO | `framework/reporting/io/{trade_history,order_history,portfolio,execution_stats,pending_orders,scenario_details,run_summary,run_meta,worker_decision,profiling,broker,warnings_errors,aggregated_portfolio,block_splitting}_report_io.py` | write the artifact(s); read back + filter (the API path) |
| Store | `framework/reporting/store/report_store.py` — `ReportStore` | resolves a run's artifacts through the **run index** (`run_index.py`), never by walking the tree: a run is looked up by id, and its directory is a column. The lookup is an EXACT match against the index, and that is the guard: the id arrives from a URL and was previously interpolated into a glob (`'*'` matched the first run). Membership in a table of known ids is strictly stronger than a shape check, which accepts anything well-formed. The depth-dependent search this used to need is gone with it — `get_trade_history` / `get_order_history` / `get_portfolio` / `get_execution_stats` / `get_pending_orders` / `get_scenario_details` / `get_run_summary` / `get_worker_decision` / `get_profiling` / `get_broker` / `get_signal` / `get_feed_stability` / `get_warnings_errors` / `get_aggregated_portfolio` |
| Ledger | `framework/reporting/store/run_results_ledger.py` — `RunResultsLedger` | the **cross-run** PERSIST sink (#390): appends one flat row per (run × currency) — the `RunSummary` KPIs + provenance (`param_hash`, git, component versions, config snapshot, sweep tagging) — to `data/run_results/` as one parquet fragment per run. Separate from the per-run API artifacts above; it is the substrate the Parameter Optimization system ranks over. Provenance via `store/run_provenance_builder.py` — `build_run_provenance` (sim) / `build_run_provenance_from_session` (live, #403 · 5.a); **both pipelines append**. See [Parameter Optimization System](parameter_optimization_system.md) |
| Console | `framework/reporting/console/run_console_renderer.py` — `RunConsoleRenderer` (+ the `*_summary` sub-presenters) | the **PRESENT** layer: `RunConsoleRenderer` owns the one canonical end-of-run section order both pipelines render through (#403 Phase 2). A `None` slot is skipped (render-if-present → live omits the sim-only sections); the per-currency AGGREGATE blocks render only for a multi-unit run (`unit_count > 1`); the closing block is pipeline-specific — `sim_executive_summary` (sim) / `live_session_summary` (live) |
| Persist (sim) | `framework/batch/batch_report_coordinator.py` — `BatchReportCoordinator.generate_and_log()` | consumes the finished `BatchExecutionSummary`; delegates the 8 shared sections to `SharedReportCoordinator`, derives + writes its sim-only sections, renders the console via `RunConsoleRenderer` (Executive Summary closing), and appends to the ledger |
| Persist (live) | `framework/autotrader/reporting/autotrader_report_coordinator.py` — `AutotraderReportCoordinator.generate_and_log()` | the live mirror: consumes the finished `AutoTraderResult`; same shared core, writes its live-specific sections, renders the **same** `RunConsoleRenderer` (the shared sections in sim order + the live Session Summary closing, #403 Phase 2), and appends to the ledger (5.a) |
| API | `python/api/endpoints/reports_router.py` | `GET /api/v1/reports/runs/{run_id}/{trade-history,order-history,portfolio,execution-stats,pending-orders,scenario-details,run-summary,worker-decision,profiling,broker,signal,feed-stability}` with section-specific filters |

The reporting home is organized by pipeline stage: `builders/` (DERIVE — the `build_*_report`
units + `report_aggregators` + `run_unit` + `run_summary_builder` + `unified_reports`), `io/`
(PERSIST — the per-section `*_report_io` writers), `store/` (the read-master `report_store` + the
cross-run `run_results_ledger` + `run_provenance_builder`), and `console/` (PRESENT); the
`shared_report_coordinator.py` at the top owns the shared DERIVE+PERSIST core (#403). The other
`framework/reporting/*` files are unrelated reporting utilities (diagnostics CSV, event-stream
CSV, field-study, …).

### Where a value is produced — source fields at the source, calculations in the builder

A field the process already owns — a broker-config fact, an event stamp, an authoritative
lookup — is stamped onto the record where it is known, resolved once from its owner. Everything
DERIVED from those fields is computed in the section's builder, off the run. The reason is cost,
not tidiness: work the run does not need must not run inside the run. The exception is a value
the process itself consumes (a decision or an execution path reads it) — that one is computed
where it is used, and the report treats it as a source field like any other. When in doubt, put
it in the builder: moving a calculation out of the run is always safe, moving one in is not.

Worked example (#265 + #391): a spot unit's estimated portfolio value needs the instrument's
base / quote split. The split is a broker-config FACT, so `PortfolioManager`'s stats carry
`base_currency` / `quote_currency`, stamped once per unit after the tick loop. The ESTIMATE over
those balances is a calculation, so it lives in `portfolio_report_builder`. The renderer prints
both and computes neither — and, critically, never splits the symbol string itself.

### Aggregates are their own stage, and they serve both outputs

A per-currency / per-worker roll-up is computed once in `report_aggregators`, lands on the model,
and is then available to the aggregated block AND to every single-unit render — a per-scenario
view may show the run's aggregate beside its own figures without recomputing it. A renderer that
builds its own aggregate is the defect this rule prevents.

### Undefined KPIs — `None`, never a sentinel

A KPI that has no value carries `None`, and the field comment says what `None` means. It is
never a stand-in number and never `float('inf')`: **infinity has no JSON representation**, and
Pydantic serializes it to `null` on write while the reader declares a plain `float` — so the
artifact cannot be read back. Measured 2026-08-29 on `profit_factor` (gross profit / gross loss,
undefined when a run has no losing trade): 5 of 1083 persisted runs were unreadable, and three
API routes answered 500 for them.

The rule applies to the whole chain, not just the model — the value is minted as `None` at the
producing site (`portfolio_manager`, `report_aggregators`), stays `None` through DERIVE, and the
PRESENT layer renders it as `n/a` / `∞ (no losses)`. One spelling end to end; a translation at a
boundary is how the two drift apart again. Same convention as `signal_fresh_ratio`, where `None`
means no SIGNAL worker was involved and deliberately not `1.0`.

### An ambiguous field ships with its discriminator

If a reader cannot tell two different states apart from a field's value, the field that
separates them belongs on the model beside it. The console may know the difference from an
object the API never sees — that is exactly how a surface ends up displaying a state nobody
is in. Measured 2026-08-29: `shutdown_mode` is `'emergency'` after an operator Ctrl+C *and*
after a crash, so a run graded `SUCCESS` shipped `'emergency'` with nothing to explain it;
`AutoTraderResult.operator_interrupted` had existed all along and simply never reached
`WarningsErrorsOutcome`. See [Warnings & Errors — Tier Taxonomy](warnings_errors_tiers.md).

A live-only field on a sim run is `''` / `False` and means **not applicable**, not *unknown* —
the same distinction the `None` rule above makes, one level down.

### Artifact encoding — UTF-8 always, the process locale never

Report artifacts are JSON, and JSON is UTF-8 by RFC 8259. Every `*_report_io` writer names
`encoding='utf-8'` explicitly, and every reader hands **bytes** to Pydantic
(`model_validate_json(path.read_bytes())`) so the decode follows the spec rather than the
platform. The CSV surfaces name their encoding for the same reason.

This is not theoretical tidiness: a run is written by one process and read back by another,
and the API server is the one component an operator may start outside the container. With a
locale-dependent read, an artifact written as UTF-8 and read on a Windows host decodes as
cp1252 — `—` becomes `â€"`, and `⚠️` **raises**, because UTF-8's `0x8F` has no cp1252 mapping
at all. One artifact class corrupts quietly, the other 500s. Guarded by
`tests/framework/reporting/test_report_io_encoding.py`, including a drift check that no IO
unit reintroduces the platform default.

## Section map — shared vs pipeline-specific (#403)

What each coordinator owns vs delegates. The DERIVE+PERSIST of the eight units-derived sections is
shared (`SharedReportCoordinator`); the end-of-run **console order** is shared too
(`RunConsoleRenderer`), with sim-only slots simply absent on the live side (render-if-present).

| Report section | Built from | Sim | Live | Outcome |
|---|---|:--:|:--:|---|
| Trade / order / portfolio / pending / execution-stats / run-summary / worker-decision / signal / feed-stability | `units` (+ the prepared signal map) | ✓ | ✓ | **SharedReportCoordinator** (9 sections → `UnifiedReports`) |
| Warnings/errors · Broker | batch / session (differs) | from_batch | from_session | build pipeline-side · **shared writer** |
| Run meta · scenario details · profiling · aggregated portfolio · block-splitting | batch | ✓ | — | **sim-only** |
| Run-results ledger append (#390) | run_summary + provenance | ✓ | ✓ | **both** (live via `build_run_provenance_from_session`, 5.a) |
| events.csv | trade/order history | per-scenario `events/` | single `events.csv` | pipeline-side (different shape) |
| Diagnostics CSV (#376) | decision sinks | during run | flush at end | pipeline-side |
| End-of-run console order | report models | rich | enriched (sim order) | **RunConsoleRenderer** (shared order; per-currency aggregates **+ the cross-scenario bottleneck analysis** gated on `unit_count > 1`; the **Warnings & Errors** section is the shared `WarningsSummary` in both, always rendered — clean zero-state when none) |
| Closing block | run-level | Executive Summary | Live Session Summary | pipeline-specific slot (last) |

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
| Portfolio — per-scenario | unified | ✅ | ✅ (linear, boxes removed) | — `max_dd_pct` and the spot dual-balance estimate are derived in the builder; the renderer's `symbol[-3:]` currency split was replaced by the broker-config split stamped at capture (#265) |
| Portfolio — aggregated (by currency) | sim | ✅ (`AggregatedPortfolioReport`) | ✅ from the model (byte-identical; `PortfolioAggregator` retired) | — |
| Pending Orders / Active | unified (sim-populated) | ✅ | ✅ | — |
| Execution Stats — per-scenario | unified | ✅ | ✅ | — |
| Execution — aggregated ORDER EXECUTION | sim | ✅ (in `AggregatedPortfolioReport`) | ✅ from the model | — (folded into the portfolio aggregate, #397) |
| Scenario Details | **sim-only** | ✅ | ✅ (linear, incl. failed + `account_currency` hint) | — |
| Run Summary (#390) | unified | ✅ | ✅ executive headline | — |
| Worker / Decision (#398/#399) | unified | ✅ (`WorkerDecisionReport`) | ✅ — `performance_summary` (worker details / aggregated / bottleneck) + the breakdown read the model (overhead Total from the profiling model, #399); the duplicate per-scenario worker list was removed. Per-worker cadence (`compute_ratio_pct`, `ticks_idle`) and `parallel_avg_saved_per_tick_ms` are derived in the builder | **still console-only:** the AGGREGATED STATS block and the bottleneck scan (slowest scenario / worst worker / worst decision logic) build their aggregates and pick their winners inside the renderer — a section migration, not a move |
| Profiling — operations + inter-tick + clipping (#399) | **sim-only** | ✅ (`ProfilingReport`) | ✅ from the model | — |
| Warmup phases (#399) | **sim-only** | ✅ (in `ProfilingReport`) | ✅ from the model (`ProfilingSummary.render_warmup`; `warmup_phase_summary` retired) | — |
| Block-Splitting Disposition | **sim-only** (Profile Run) | ⏳ | ✅ inline | **separate follow-up** — generation-quality metric (`generator_profiles` + `block_boundary_report`), not runtime profiling |
| Robustness Validation (#367) | **sim-only** (robustness mode) | ✅ (`RobustnessReport`) | ✅ from the model (`RobustnessSummary`; ROBUST/⚠/OVERFIT display class only) | — verdict is a decision in `PostRunValidator` (`_check_robustness`, gated on the block-splitting disposition); reuses `build_run_summary` per window |
| Broker Configuration | unified | ✅ (`BrokerReport`) | ✅ from the model (sim full table · live compact line) | — |
| Signal Configuration (#433) | unified | ✅ (`SignalReport`) | ✅ from the model (`SignalSummary`) | — Part C (fresh/stale/blind per tick) + Part A (the section) done; Part D moved to its own section below (#451) |
| Feed Stability (#451) | unified | ✅ (`FeedStabilityReport`) | ✅ from the model (`FeedStabilitySummary`) | — the disturbance episodes of **both** staleness domains (tick stream #436 + every SIGNAL source #434) in one per-source table, plus the tick-domain fresh/stale counters and the `RunSummary` totals behind the executive line. Rendered only when the run saw an episode |
| Warnings & Errors | unified | ✅ (`WarningsErrorsReport`) | ✅ from the model — tiered (errors / Tier-1 major / Tier-2 minor); executive failed-scenario headline reads the model outcome; warnings lifted into validators (`PostRunValidator`), the orchestrator keeps only a thin global-log line (#395). Each Tier-1 row carries its ORIGIN (`check` / `domain`) from the `ValidationFinding` that produced it. The ERROR pot reaches the model as `LogEntryRow`s (level / both times / scope / message), mapped by one shared helper for both pipelines — a reduction to the message here would put those fields out of reach of every surface behind DERIVE | **both pipelines render the shared `WarningsSummary`** (always shown — clean zero-state when none, #403 Phase 2); live messages get the logger prefix stripped in the builder |
| Executive — detailed portfolio-performance block | **sim-only** | ✅ (`AggregatedPortfolioReport`) | ✅ from the model (margin / spot / mixed preserved, byte-identical) | — (#397); the profit factor is read from the model instead of recomputed with a divergent formula, and the order execution rate is carried as `execution_rate_pct` |
| Shutdown / Emergency / Session | **autotrader-only** | ✅ | ✅ `LiveSessionSummary` | the live closing block of the unified `RunConsoleRenderer` (#403 Phase 2): session stats + warnings/errors (session buffers, §35) + output locations; #389 analytics line model-sourced |
| **Final:** directory consolidation | — | — | — | ✅ **#396 DONE** — `batch_reporting/` folded into `framework/reporting/` by stage: `run_reports/` (DERIVE) · `io/` (PERSIST) · `console/` (PRESENT) |
| Shared coordinator + folder split | — | — | — | ✅ **#403 DONE** — the units-derived DERIVE+PERSIST core extracted into `SharedReportCoordinator` (both pipelines delegate, returning `UnifiedReports`); the home re-split into `builders/` (DERIVE) · `io/` (PERSIST writers) · `store/` (read-master + cross-run ledger + provenance). Live ledger append wired (5.a) |

The **array model** is the unifier: a run is a list of units (sim: N scenarios; live: 1
session). Where a section carries per-unit meaning (portfolio breakdown) the model keeps the
units; for flat record lists (trades, orders) every row carries its `symbol` and the list is
filtered, not grouped. The generic `RunUnit` abstraction that deduplicates the per-source
extraction is **implemented** (`builders/run_unit.py`); every builder maps from it.

## Reporting is OPTIONAL in simulation and MANDATORY live — on purpose

The two pipelines differ in **who decides that a report is written**, and the difference is a
decision, not an oversight:

```
SIM    orchestrator.run()                          → the CALLER then chooses to build
                                                     BatchReportCoordinator, or not
LIVE   autotrader_main.run() calls                 → no way past it
       self._generate_reports(result) internally
```

**A backtest can be repeated; its report may be optional.** The simulation test path relies on
exactly this — it stops after `orchestrator.run()` and writes no artifacts, which is how ~35 of
the tree's runs legitimately carry none.

**A live session cannot be repeated. Its report is the only record that real money moved.** If
the call sat with the caller, "forgotten" would be a reachable state, and the price of reaching
it is a real-money session with no record. So the live pipeline does not offer the choice.

Making the two symmetric would mean either lifting the live call out — which introduces exactly
that footgun — or making the sim call mandatory, which the test path cannot afford. The
asymmetry is the correct shape, and it is recorded here so it reads as a decision.

**What the two DO share** is that the decision is now *declared*: `RunHeader.reporting` is
`expected` or `none`, written at run start by whoever knows. Read it together with the index's
`artifacts` list:

```
reporting=none      + artifacts=[]   →  as commissioned          (a consumer greys it out)
reporting=expected  + artifacts=[]   →  still running, or DIED before reporting
reporting=expected  + artifacts=18   →  complete
```

Without the pair, an empty artifact list means all three at once — and a crashed run is then
indistinguishable from an intentionally silent one.

**Retention is deliberately opposite between the two stores (#390, #482).** The run TREE is
finite and prunable — `run_index_cli.py prune` removes runs and rebuilds the index afterwards,
because the index is derived and follows the tree rather than being edited beside it. The
cross-run LEDGER (`data/run_results/`) keeps its row for every run that ever finished, including
runs whose directory is long gone: 430 fragments against 94 runs in the tree, measured. That gap
is the design, not a leak — nobody should later "fix" it.

The `reporting` field is what makes pruning safe, but it is the **guard** rather than the
selector: `reporting=expected` with no artifacts is a run that crashed before reporting, and no
flag may reach it. What the operator actually selects by is redundancy (`--keep-last`) and
directories that are not runs at all (`--orphans`).

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

**Cross-run ledger (#390).** Beyond the per-run artifacts, both coordinators append the run to the
**Run Results Ledger** (`data/run_results/`) via `RunResultsLedger.append()` — the same `RunSummary`
model plus provenance. This is a separate, accumulating store (one parquet fragment per run), the
substrate the Parameter Optimization system ranks over. Both pipelines append (#403 · 5.a): the live
`AutotraderReportCoordinator` writes its session row through the same sink via
`build_run_provenance_from_session`, whose `param_hash` is comparable to the backtest. See
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
  scenario-details rows, the broker configuration rows, artifact resolution, and the signal
  section (`test_signal_report.py`: archive plane + decision-basis counters + the weakest-channel
  aggregate, over a REAL analyzed `SignalCoverageReport`).
- `tests/framework/api/test_reports_endpoint.py` — the endpoints via TestClient against fixture
  artifacts (happy path, filtering, 404, invalid-input) across the migrated sections.

## Phasing (#391)

1. **Trade / order / portfolio slices (done)** — models + postprocessors + IO/store + API
   endpoints + persist in both pipelines.
2. **`RunResult` split (done)** — the per-unit extraction is unified in
   `builders/run_unit.py` (`RunUnit` + `run_units_from_batch` / `run_units_from_session`):
   the run is extracted into units once (symbol resolved from the index-synced scenario for
   sim), and every section builder maps from the shared units — no per-source duplication, no
   flat builder variants. The aggregate handling (portfolio per-currency roll-up) is the next
   target (the aggregator layer).
3. **Trade analytics (#389, done)** — MAE/MFE **tracked** on the Position each tick (runtime,
   shared layer) + `initial_risk` stamped at close; R-multiple / expectancy **derived** in the
   postprocessor. Surfaced as per-row columns (`mae_*`/`mfe_*`/`r_multiple`) + a `TradeAnalytics`
   aggregate on `TradeHistoryReport`. Console display follows with #393. The MAE/MFE distance is
   now **exact (#167, done)**: the authoritative per-symbol `pip_size` + report unit label
   (`price_unit`) are stamped on the `TradeRecord` at the source (position open), so the row's
   `mae_distance` / `mfe_distance` render in the correct unit (`pip` on Forex, `tick` on crypto) on
   every surface — the builder no longer approximates with `10^-(digits-1)`.
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
8. **Shared coordinator + folder split (#403, structural) — DONE.** The units-derived DERIVE+PERSIST
   sequence both coordinators copy-pasted (the 7 sections trade / order / portfolio / pending /
   execution-stats / run-summary / worker-decision) was extracted into one `SharedReportCoordinator.derive_and_persist(units, io_dir)`
   that returns a `UnifiedReports` DTO; each coordinator delegates the shared core and keeps only its
   pipeline-specific sections + console + ledger (composition, not a base class). The `framework/reporting/`
   home was re-split by stage: `run_reports/` → **`builders/`** (DERIVE), `io/` keeps the per-section
   writers, and a new **`store/`** holds the read-master `report_store` + the cross-run `run_results_ledger`
   + `run_provenance_builder`. Pure refactor — every `io/` artifact stays byte-identical (sim + live).
   The live pipeline now also appends to the run-results ledger (5.a): `build_run_provenance_from_session`
   mirrors the sim provenance, so a live session's `param_hash` is directly comparable to the backtest.
