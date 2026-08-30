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

### Worked example — the warning and the facts are two different records (#451)

A stress-tested run produces both, from two different sources, and neither replaces the other:

- **The warning** `STRESS TEST ACTIVE` (`PostRunValidator._check_stress_test`) is the **judgment**
  — *"this run contains intentional errors, do not read its results as clean"*. It is derived from
  the **configuration**, so it states the operator's *intent*: which windows were planned, on which
  source, for which span.
- **The facts** are the [feed-stability section](reporting_pipeline.md) — the episodes the run
  actually **experienced**, derived from the observed status/resolution changes. It never judges;
  it states from–to, duration, counts, and whether an episode was `live-real` or `stress-injected`.

The two legitimately disagree: a planned 45-minute window that reaches past the scenario end is
experienced as 15 minutes without recovery. Keeping intent and experience in separate records is
what makes that visible instead of averaging it away.

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
| **Tier 1 — major warnings** | advisory but important: debug-mode, stress-test, data-version, tick-budget (P5 / granularity / too-high), the account-currency / margin advisories, post-run profiling verdicts (overhead, bottleneck) | **validators** → `ValidationResult.warnings` (per-scenario), the **batch-level** validation channel (run-scoped, e.g. debug-mode), and the **session** channel on the live side | surfaced in the report |
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
  (`BatchExecutionSummary.batch_validation_result`). `SessionPostRunValidator` is its live
  counterpart, writing into `AutoTraderResult.session_validation_result`. The report builder then
  only reads — it never decides.

## AutoTrader (live) — the same four channels

A live session has **no multi-scenario validation phase**, and startup/preflight validation still
**aborts** rather than warns (one session, nothing to exclude). What it does have is a *post-run*
validation channel, mirroring the batch one:

- **Errors** → `AutoTraderResult.error_messages` (session ERROR buffer) + `emergency_reason` (the villain).
- **Tier 2** → `AutoTraderResult.warning_messages` (session WARNING buffer).
- **Tier 1** → `AutoTraderResult.session_validation_result`, filled by `SessionPostRunValidator`
  before the report coordinator runs — the same place the sim runs `PostRunValidator`.
- **Outcome** → `shutdown_mode` (+ `emergency_reason`), re-graded by `get_outcome()` (below).

The asymmetry is closed (#372): a normal session with pot errors is no longer graded as a clean run.
Both pipelines now answer with the same `RunOutcome`, and the process exit code is that answer.

### What the live validator checks, and what it deliberately does not

Only two of the sim's thirteen post-run checks can be answered by a single session, and they are
**shared, not copied** — `validators/shared_advisory_checks.py` holds the formula, each validator
supplies its own inputs and routes the findings into its own channel:

| Check | Live | Why |
|---|---|---|
| `stress_test` | ✅ | an active stress config is a Tier-1 warning in *both* pipelines — a stressed live session must not look clean |
| `slow_component` | ✅ | `worker_statistics` / `decision_statistics` are the same types live |
| overhead · bottleneck · parallel-penalty | — | need `profiling_data` / `coordination_statistics`, which a session does not collect |
| the three tick-budget checks | — | live has no budget; clipping is observed, not configured |
| multi-currency · time-divergence | — | one session, one currency, one span |
| data-version · robustness · debug-mode | — | tick index, walk-forward and the batch serial mode are sim-only |

**Observed feed outages are not a check.** They are facts and belong to the feed-stability
section — the same intent/experience split the worked example above describes. A validator over
them would collapse the two records the split exists to keep apart.

The shared functions carry a `unit_label`, because the message names its units: the sim writes
`Scenarios (3): …`, a session writes `Session (1): …`. Without it the live warning would use the
sim's word.

## The run outcome — the same question both pipelines answer

`RunOutcome` (`framework/types/run_outcome_types.py`) is the one classification, derived by each
pipeline from its **own** result object. The CLIs map it to an exit code; they never derive it.

| `RunOutcome` | Exit | Simulation — `BatchExecutionSummary.get_outcome()` | AutoTrader — `AutoTraderResult.get_outcome()` |
|---|---|---|---|
| `SUCCESS` | `0` | every `ProcessResult.success` true | normal shutdown, no pot errors — **or** an operator Ctrl+C with none |
| `CRASHED` | `1` | no summary produced at all (startup abort), or an uncaught exception at the CLI | an uncaught exception at the CLI |
| `FAILED` | `2` | any unit failed for a reason other than logged errors | `shutdown_mode == 'emergency'` that the operator did not initiate |
| `FINISHED_WITH_ERRORS` | `3` | units failed and **every** one of them is the `LoggedErrors` kind | normal shutdown (or operator Ctrl+C) with a non-empty error pot |

Two properties worth stating, because both were bugs waiting to happen:

- **The sim already graded its pot** — `process_main` sets `success=False` with
  `error_type='LoggedErrors'` for a scenario that logged errors without crashing. So `FAILED` and
  `FINISHED_WITH_ERRORS` are distinguished by *why* units failed, not by a second channel.
- **An operator Ctrl+C is not a failed run, but a safety-triggered emergency is.** Both arrive as
  `shutdown_mode='emergency'`, and `emergency_reason` does not separate them — the #348 session-end
  escalation raises an emergency with no reason attached. `AutoTraderResult.operator_interrupted`
  is therefore explicit: only the SIGINT handler sets it. Inferring it from a missing reason would
  have let a safety-initiated shutdown report success.

## The Tier-2 pot — what is captured, and what it claims

The pot is the log channel: observations nobody adjudicated. Two rules govern it.

**Capture is independent of display.** `LogLevel.WARNING` and `LogLevel.ERROR` are buffered
regardless of the CONSOLE threshold (`AbstractLogger._process_log`), because they are report
input. Before that rule, raising the console log level silently removed warnings from the run
report — a display setting deciding what the report was allowed to see. Everything below those
two levels still follows the console setting.

**A pot row claims no assertion.** Tier-2 rows leave `check` and `domain` empty, and that is
the honest answer: no assertion decided a log line, and it belongs to no validator area. The
channel is already named — by the tier itself.

`WarningTier` is an Enum for that reason: it is the ORIGIN question, answered once.

```python
class WarningTier(StrEnum):
    VALIDATOR_PRODUCED = 'major'    # a check decided it, so check/domain are filled
    LOGGER_PRODUCED = 'minor'       # the log pot: an observation nobody adjudicated
```

The member names say what the value means; `major` / `minor` read like a severity and are not
one. The VALUES stay as they are because they are the wire contract the API and FiniexViewer
already consume — renaming them would be a consumer-facing change, renaming the members is not.

The general rule this follows, so it does not drift again: **a closed set is an Enum, an open
set is a string.** `tier` (2 channels) and `domain` (10 areas) are closed. `check` is open —
every new assertion adds an id, and an Enum would have to be extended by whoever adds one, which
is exactly the step that gets forgotten. `scope` is open too (any scenario or profile name).

The pot's messages are unrendered — the buffer carries `LogRecord`s, so nothing has to be
stripped and no terminal escape code can reach the artifact. See
[Batch Data Flow](batch_data_flow.md).

## The finding is the unit — `ValidationFinding`

Every validator produces `ValidationFinding` (`framework/types/validation_types.py`), and
`ValidationResult` is a typed collection of them. A finding carries its own `severity`
(ERROR rejects the unit, WARNING advises), the `check` that decided it, the `domain` it belongs
to, and the `scope` it concerns.

`ValidationResult.is_valid` / `.errors` / `.warnings` are **views over `findings`**, not stored
state. A stored flag can disagree with the list it summarizes; a derived one cannot. The §33
execution gate (`SingleScenario.is_valid()`) reads the derived flag, so this is what decides
whether a scenario runs.

Two properties matter for anyone adding a validator:

- **Severity belongs to the finding, not the container.** One result can therefore carry a
  rejection *and* an advisory about the same subject, and both survive to the report. Before the
  findings shape, an advisory sharing a container with an error was silently discarded.
- **`check` is an open set, `domain` is a closed one.** `check` is a free string — every new
  assertion brings a new id, exactly like `FeedCheck.name`. `domain` is `ValidationDomain`, an
  Enum, because a free string would let `'profiling'` / `'Profiling'` / `'perf'` coexist and make
  filtering worthless.

`severity` and `tier` are orthogonal and must not be conflated: severity is the finding's own,
tier says which channel it came from (validator → Tier 1, log pot → Tier 2).

## The model

The unified section is `WarningsErrorsReport` (`framework/types/api/report_types.py`), derived once and
rendered to console / file / API identically:

- `warnings: list[WarningRow]` — `tier` ('major' | 'minor'), `scope` ('run' | unit name), `message`,
  plus the origin: `check` (the assertion's stable id) and `domain` (its area). Both are empty on a
  Tier-2 row and on artifacts written before the origin existed — the log pot is an observation
  nobody attributed to a check, and a missing origin renders as the bare scope.
- `errors: list[UnitErrorRow]` — per unit with any error: `error_type` / `error_message`,
  `validation_errors`, `logged_errors`, `traceback`.
- `outcome: WarningsErrorsOutcome` — `run_outcome` (the canonical grading, below) plus
  `failed_count` / `failed_unit_names` / `first_failure_*` (sim) and `emergency_reason` /
  `shutdown_mode` / `operator_interrupted` (live). The Executive headline reads this — it does
  not re-scan. `operator_interrupted` rides along for the reason given above: `shutdown_mode`
  alone cannot separate a Ctrl+C from a crash, so a surface that carried only the mode would
  show 'emergency' beside a run graded `SUCCESS` and have no way to explain it. The live-only
  fields are `''` / `False` on a sim run — that means *not applicable*, never *unknown*.

`run_outcome` is stamped once at DERIVE from the pipeline's own result object
(`BatchExecutionSummary.get_outcome()` / `AutoTraderResult.get_outcome()`), so the grading a
supervisor reads as the exit code is the same one the artifact, the store and the API carry. A
surface that needs the verdict reads this field; none of them re-derives it from the counts.

One deliberate exception remains: the run-results ledger (`run_provenance_builder._run_status`)
error-flags only a **total** failure — a partial run keeps its usable data for ranking. That is a
policy over the outcome, not a second grading, and it still needs `failed_count` / `total_units` to
express it.
