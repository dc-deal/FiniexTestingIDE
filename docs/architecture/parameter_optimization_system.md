# Parameter Optimization System

Grid search over a strategy's parameters: run a base scenario set across a **grid of parameter
values**, record one result per combination in a persistent ledger, and rank the combinations by a
configurable objective. This is the systematic alternative to hand-editing one config, running it,
and eyeballing the result.

It mirrors the institutional pattern — QuantConnect/LEAN *Optimization* (a separate spec referencing
the algo + a parameter range), Backtrader `optstrategy`, vectorbt parameter grids, NautilusTrader's
list-of-configs + external optimizer. All rank over one flat metrics object per run; here that object
is the `RunSummary` from the [reporting pipeline](reporting_pipeline.md).

> A sweep is **N batches** — the execution pipeline is unchanged (see
> [Process Execution Guide](../process_execution_guide.md)). Only the orchestration on top
> and the cross-run ledger are new. Today each batch reloads its ticks; the mountable prepare/execute
> seam (#417) lets a sweep prepare the data once and reuse it across all combinations (#419), since the
> data identity is constant across a grid that varies only `strategy_config`.

---

## The flow — producer → ledger → consumer

```
OptimizationRunner                RunResultsLedger                optimization_cli report
  expand grid → N combos    ──►   data/run_results/         ──►   rank + sensitivity
  run each as one batch           (1 parquet fragment/run)        "which combination wins?"
  (each self-records)             leading key: param_hash
```

Three decoupled stages — and the same split #31 (parameter discovery) builds on later:

1. **Combination generator** — `grid_expander.py` (Cartesian product, deterministic order). This is
   the pluggable seam: #32 adds random / Bayesian / genetic generators with the same
   `grid → List[combo]` contract.
2. **Runner** — `optimization_runner.py` runs each combination as a normal batch; every run appends
   its KPIs to the ledger. The runner collects nothing in memory.
3. **Analyzer** — `optimization_analysis.py` reads the ledger and computes ranking + sensitivity
   (pure calculation, no verdicts — an analyzer, not a validator).

---

## The sweep spec (`configs/sweeps/<name>.json`)

A spec references a base scenario set and declares the grid. Each grid entry is a **dotted path** to a
strategy parameter mapped to its candidate values:

- `decision_logic_config.<param>` → a decision-logic parameter
- `workers.<instance>.<param>` → a worker parameter (instance name from `worker_instances`)

Nested sub-parameters are addressable too (`decision_logic_config.<param>.<sub>…`,
`workers.<instance>.<param>.<sub>…`) — e.g. `workers.macd_main.periods.M5` sweeps one timeframe's period.
A nested path that changes a worker's bar lookback shifts the **warmup window** (and thus the data
identity), so those combinations reload instead of reusing the mount (#419 cold fallback).

```json
{
  "base_scenario_set": "cautious_macd_sandbox.json",
  "objective": "net_pnl",
  "maximize": true,
  "grid": {
    "decision_logic_config.sl_pips": [100, 150],
    "decision_logic_config.tp_pips": [200, 300],
    "workers.bollinger_main.deviation": [2, 3]
  }
}
```

| Field | Meaning |
|---|---|
| `base_scenario_set` | the scenario set the grid varies (resolved like any set: path → user algo dirs → `configs/scenario_sets/`) |
| `grid` | dotted path → list of candidate values; expands to the Cartesian product |
| `objective` | `RunSummary` currency KPI to rank by (default `expectancy`) |
| `objective_currency` | required only when a run produces more than one account currency |
| `maximize` | rank direction (set `false` e.g. for `max_drawdown`) |

**Validation — structural fail-fast, parameter errors per-combination:** before any batch runs,
`sweep_grid_validator.py` checks the grid **structurally only** — every dotted path must have a valid
shape (`decision_logic_config.<param>[.<sub>…]` or `workers.<instance>.<param>[.<sub>…]`) and a non-empty value list; a
malformed path or empty list is a spec typo affecting all combinations → abort early. Parameter
**existence / range / type** are NOT checked here: the grid only writes values into each combination's
`strategy_config`, and the run validates them in **Phase 0** (`ScenarioValidator.validate_scenario_parameters`,
against each component's `get_parameter_schema()` — type, range, required, AND unknown keys). An invalid
combination is marked invalid there, excluded from execution, and recorded as an **error-flagged ledger
row** that is excluded from the ranking (§33 = config error → per-scenario failure, not a whole-batch
abort). Other combinations keep running. (Strategy parameters are not Pydantic-typed by design — the
per-component schema is their type system; lifting the check into Phase 0 means every run — not just
sweeps — rejects a typo'd parameter instead of silently ignoring it.)

**How a combination is built:** the base config is loaded once; per combination it is deep-copied and
each grid value is written into every scenario's `strategy_config` (in-memory, no temp files; the base
is never mutated). The scenario set name is tagged per combination (`__<sweep_id>_c<idx>`) so each
combination gets a unique run directory.

---

## The Run Results Ledger (`data/run_results/`)

A persistent, accumulating store — **every** run appends to it (sim batch + live session, #403 · 5.a),
not only sweeps — so it doubles as a complete run history. It is a flat directory with **one parquet fragment per run**
(`<scenario_set>_<run_id>.parquet`); parquet is immutable, so one file per run is the lock-free append.
Read the whole directory back as one table.

- **Home:** `data/run_results/` (path from `app_config.json::paths.run_results`). The data layer, not
  `logs/` — the ledger survives log cleanup. Gitignored (generated runtime data).
- **No partition by config name** — all identity (`param_hash`, `sweep_id`, `scenario_set_name`,
  `decision_logic_type`, …) is **columns**, never folder structure. The leading key for ranking is
  the logical `param_hash`; filter by any column.
- **Row grain:** one per (run × account currency) = a `RunSummary` currency row + provenance. A
  **failed** run (every scenario failed — e.g. an out-of-range parameter combination) writes ONE
  `status='error'` row instead: provenance + sweep tag intact, KPIs zero, the `error` column carrying
  the reason. The run is recorded (never silently absent) but excluded from the ranking.
- **Read is schema-evolution safe:** fragments are read individually and unioned (then reindexed to the
  canonical columns), so adding a column later does not strip it from older fragments' siblings.

**Columns:** `param_hash` (leading) · `status` (`ok`/`error`) · `error` · `run_id` · `run_timestamp` ·
`sweep_id` · `sweep_params` · `scenario_set_name` · `git_commit` / `git_branch` / `git_dirty` ·
`decision_logic_type` · `decision_version` · `worker_versions` · `config_snapshot` (full resolved
strategy_config) · `symbols` · `data_broker_type` · `currency` · the `RunSummary` KPIs (`net_pnl`,
`expectancy`, `profit_factor`, `win_rate`, `max_drawdown`, trade / order counts …). Typed read:
`read_rows() -> List[RunResultRow]` (the JSON columns parsed; what the analysis + API consume).

**Provenance** (`run_provenance_builder.py`): `param_hash = generate_config_fingerprint(strategy_config)`
(decision + all workers + type strings — the leading key); git via `get_git_info()`; component versions
resolved from the type strings via the factories (`ComponentMetadata.version`, best-effort — never
crashes the report). `param_hash` covers strategy parameters ONLY — balances / latency / data window
are recorded as columns, not folded into the hash (so "same strategy, different balance" is not seen as
a different parameter set). The full config snapshot is preserved, so a row is self-contained even if
the run directory is later deleted.

---

## Ranking + sensitivity

`optimization_cli.py report <sweep_id>` reads the sweep's ledger rows and prints:

- **Best combinations** — ordered by the objective (stable tie-break by `run_id`, so two runs of the
  same grid + data give the same ranking → pairs with the determinism gate #368). Writes the ranked
  table to `logs/sweeps/<sweep_id>_ranked.csv`. **Error rows are excluded** (the exclusion lives once in
  the analysis `_scope`, so every consumer is safe — no failed run can "win" by doing nothing).
- **Errored combinations** — a warning block lists every `status='error'` combination + its reason
  (e.g. a parameter out of range), so the operator sees what needs fixing. Recorded, not evaluated.
- **Parameter sensitivity** — the **one-factor marginal effect**: per swept parameter, group rows by
  that parameter's level, take the mean objective per level, and report the spread (max − min) as the
  parameter's *influence*, ranked descending. It answers "which knob actually moves the result" — so
  you tune the parameter that matters and fix the noise at its default.

  *Honest limit:* this is OFAT — it ignores interactions and makes no statistical-significance claim.
  It is an indicator, not a verdict. #31 later swaps the spread for a variance / ANOVA importance over
  the same ledger rows (same data, same output shape).

---

## CLI

```bash
# run a sweep (resolves configs/sweeps/<name>.json or a path)
python python/cli/optimization_cli.py run cautious_macd_grid.json

# list every recorded sweep as a one-liner (start · duration · runs · algo · objective)
python python/cli/optimization_cli.py list

# rank + sensitivity for a finished sweep
python python/cli/optimization_cli.py report sweep_20260621_223000
python python/cli/optimization_cli.py report sweep_20260621_223000 --objective net_pnl --top 5
python python/cli/optimization_cli.py report <sweep_id> --objective max_drawdown --minimize
```

`report` **defaults to the sweep spec's own objective + direction** (recorded in the ledger as
`sweep_objective` / `sweep_maximize`), so `report <sweep_id>` ranks by what the spec declared.
`--objective` / `--minimize` override it; the ledger keeps every KPI, so the same sweep can be
re-ranked by any objective after the fact.

The report header also prints sweep-level provenance from the ledger columns: base config + symbols +
broker, the decision logic + worker versions (ComponentMetadata), the git commit, and the sweep span +
duration (derived from the per-run start timestamps).

Launch entries: `🎛 Optimization: Cautious MACD Grid` (run) and `🧩 Pytest: Parameter Optimization (All)`.

---

## Mount reuse + fail-fast abort (#419)

A sweep varies only `strategy_config`, so every combination shares the **same data identity** (broker ·
symbol · window · warmup · tick budget). The runner therefore **loads the data once** from the base set
(`BatchOrchestrator.build_mount()`) and reuses that `MountPackage` across every combination via
`run(mount=…)` (the #417 seam): each combination only preps its scenarios (broker_type — cheap), validates
its parameters, and executes against the shared data — skipping the ~97.5%-of-warmup reload. A combination
whose grid touches a **warmup-affecting** worker parameter (its data identity then differs) falls back to a
cold reload for that combination, logged.

**Fail-fast abort** — a data-level failure is invariant across every combination, so the sweep stops early
instead of repeating it N times:
- **Data-level (mount build):** if the base data cannot be loaded for any scenario (invalid window /
  missing data), the sweep aborts before any combination runs.
- **OOM villain:** if the *first executed* combination crashes because a worker subprocess was OOM-killed
  (`BrokenProcessPool`; #416's `SubprocessPoolMemoryError` once that lands), the sweep aborts the rest.
- **Strategy-level (per combination):** an out-of-range parameter marks only that combination invalid and
  is recorded as an error ledger row — the sweep keeps going (unchanged §33).

Off-switches (`app_config.json::backtesting.parameter_optimization`, both default **on**): `mount_reuse_enabled`
(off → today's cold per-combination path) and `villain_abort_enabled`.

---

## Scope (v0) and follow-ups

- **In:** grid search with single-load **data-mount reuse** across combinations (#419), the cross-run
  ledger (both pipelines — sim batch + live session, #403 · 5.a), objective ranking, one-factor sensitivity.
- **Out (follow-ups):** smarter search (random / Bayesian / genetic) = **#32** (new generators on the
  same seam); walk-forward / out-of-sample splitting = **#367**; variance / ANOVA parameter importance
  + worker-contribution = **#31**; composite / weighted objective; per-symbol ledger rows for regime analysis.

## Tests

`tests/simulation/optimization/` — grid expansion + determinism, dotted-path override + base
immutability, ledger append/read/filter (real `RunSummary` types), ranking + sensitivity on known
rows, grid-validator fail-fast, and the **mount-reuse sweep** (#419: a real warm sweep == the cold path,
data-level abort, OOM-signature detection). Suite doc: `docs/tests/simulation/parameter_optimization_tests.md`.
