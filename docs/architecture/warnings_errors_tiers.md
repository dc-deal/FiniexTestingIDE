# Warnings & Errors — Tier Taxonomy

How FiniexTestingIDE classifies and routes warnings and errors across both pipelines. This is a
**cross-cutting** taxonomy: validators *produce* the structured truth, the reporting pipeline only
*reads and renders* it. It is the contract behind the unified "Warnings & Errors" report section
(see `reporting_pipeline.md`).

## The principle — no decisions in reports

The reporting pipeline is **CAPTURE → DERIVE → PRESENT**:

- **DERIVE** is pure, reproducible **calculation** (sums, ratios, percentiles). Deterministic, no
  tunable verdict.
- **PRESENT** formats and renders — including display-only choices (color, ordering, truncation).
- A **decision** — "does this warrant a warning? is this critical? should this be optimized?" — is a
  **judgment**, not a calculation. It belongs in a **validator**, never in a report builder, an
  aggregator, or a presenter.

**The test:** if a threshold changes only *how* something looks (color, order) it is presentation; if
it changes *whether* a warning or verdict fires, it is a decision → it must be a validator. A report
unit that computes `avg_ms > p5?` or `overhead > 50%?` and emits a notice is misplaced decision logic.

## The two channels (sim)

Errors split into two channels at run time (this mirrors the error model in the architecture rules):

- **The "villain"** — an uncaught exception that crashes a scenario subprocess → `ProcessResult`
  carries `error_type` / `error_message` / `traceback`.
- **The "error pot"** — errors *logged* during the run (no crash) accumulate in the scenario-logger
  buffer (`ProcessResult.scenario_logger_buffer`). A scenario can finish without a crash but with pot
  errors → `FINISHED_WITH_ERROR`.

## The tiers

| Tier | What | Producer (source of truth) | Importance |
|---|---|---|---|
| **Errors** | every error matters | `ValidationResult.errors` (validation/preparation failures, `is_valid=False`) **+** the `ProcessResult` villain (`error_type`/`message`/`traceback`) **+** the log ERROR pot (`scenario_logger_buffer`) | always surfaced |
| **Tier 1 — major warnings** | advisory but important: debug-mode, stress-test, data-version, tick-budget (P5 / granularity / too-high), the account-currency / margin advisories, post-run profiling verdicts (overhead, bottleneck) | **validators** → `ValidationResult.warnings` (per-scenario) and the **batch-level** validation channel (run-scoped, e.g. debug-mode) | surfaced in the report |
| **Tier 2 — minor warnings** | anything at WARNING level floating in the log | the log WARNING pot (`scenario_logger_buffer`) | summarized ("N in log — see scenario logs"), ignorable |

`ValidationResult` (`framework/types/validation_types.py`) is the **single structured producer** for
errors and Tier-1 warnings — it already carries `errors`, `warnings`, and `is_valid`. The log pots are
the secondary, unstructured channel.

### Pre-run vs. post-run validators

- **Pre-run validators** (orchestrator Phase 0–5) catch blocking config/data **errors**
  (`is_valid=False`) — a bad scenario is excluded, the batch continues.
- **Post-run validators** produce the advisory **Tier-1 warnings** that can only be known *after*
  execution (tick-budget needs profiling/clipping; overhead/bottleneck need the timing breakdown).
  `PostRunValidator` runs once after the batch, appends `ValidationResult.warnings` per scenario, and
  writes batch-global notices (debug-mode) into the **batch-level** validation channel
  (`BatchExecutionSummary.batch_validation_result`). The report builder then only reads — it never
  decides.

## AutoTrader (live) — the asymmetry

A live session has **no multi-scenario validation phase**; startup/preflight validation **aborts**
(one session, nothing to exclude). So the live half maps:

- **Errors** → `AutoTraderResult.error_messages` (session ERROR buffer) + `emergency_reason` (the villain).
- **Tier 2** → `AutoTraderResult.warning_messages` (session WARNING buffer).
- **Tier 1 / validation** → effectively empty (preflight aborts instead of warning).
- **Outcome** → `shutdown_mode` (+ `emergency_reason`).

Known asymmetry: the AutoTrader has no `FINISHED_WITH_ERROR` equivalent — a normal run with pot errors
stays `shutdown_mode='normal'`; the errors are listed but the outcome is not re-graded.

## The model

The unified section is `WarningsErrorsReport` (`framework/types/api/report_types.py`), derived once and
rendered to console / file / API identically:

- `warnings: list[WarningRow]` — `tier` ('major' | 'minor'), `scope` ('run' | unit name), `message`.
- `errors: list[UnitErrorRow]` — per unit with any error: `error_type` / `error_message`,
  `validation_errors`, `logged_errors`, `traceback`.
- `outcome: WarningsErrorsOutcome` — `failed_count` / `failed_unit_names` / `first_failure_*` (sim) and
  `emergency_reason` / `shutdown_mode` (live). The Executive headline reads this — it does not re-scan.
