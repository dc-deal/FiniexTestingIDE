# Parameter Optimization Tests

Validates the Parameter Optimization system (#390): grid expansion, parameter override, the
run-results ledger, ranking, sensitivity, and grid validation — plus the #419 mount-reuse sweep. The
#390 units are pure / config-only (fast, data-independent); the #419 `test_sweep_mount_reuse.py` runs a
**real** warm + cold sweep over the mini set (data-dependent, kraken_spot BTCUSD) to prove warm == cold.

**Location:** `tests/simulation/optimization/`
**Marks:** `simulation`, `unit`
**Fixtures:** `tests/fixtures/optimization/` (`btcusd_mini_set.json` base set + `btcusd_mini_grid.json`
spec — read config-only, no tick data required) · factory fixtures in the suite `conftest.py` build the
real `RunSummary` / `RunProvenance` types.

System doc: [Parameter Optimization System](../../architecture/parameter_optimization_system.md).

---

## Test Files

| File | What it proves |
|---|---|
| `test_grid_expander.py` | Cartesian product size, every combination unique, deterministic + sorted order, single-parameter + empty grid |
| `test_parameter_override.py` | `set_by_path` (existing + intermediate creation), `apply_overrides` writes into each scenario, base config untouched (deep-copy isolation), scenario-set-name tagging |
| `test_run_results_ledger.py` | Append→read round-trip (real `RunSummary`), one fragment per run, same-second / distinct-set no overwrite, filter by `sweep_id`, empty-ledger read, JSON round-trip, **typed `read_rows`** (parsed + nullable), **error rows** (explicit error + no-currencies → `status='error'`, no false KPIs), **schema-evolution-safe read** (old fragment without a column still reads), **sweep objective + direction persisted** (report defaults to them) |
| `test_optimization_analysis.py` | Ranking (maximize / minimize / deterministic / unknown-objective raise), typed rows, one-factor sensitivity (influence + per-level means), **error rows excluded** from ranking + sensitivity, **`summarize_sweeps`** (per-sweep grouping: start/duration, run + ok/error counts, algo, objective; non-sweep runs ignored) |
| `test_sweep_grid_validator.py` | Valid grid passes; **unknown param + out-of-range value pass** (structural-only — existence/range moved to the run's Phase 0); bad path prefix, wrong decision/worker path length, empty value list all raise (structural fail-fast) |
| `test_sweep_mount_reuse.py` (#419) | **warm == cold** (a real mount-reused sweep yields ledger results identical to the cold reload path — off-switch toggled); **data-level abort** (an empty base mount records no runs); **OOM-signature detection** (`_has_subprocess_oom` on `BrokenProcessPool`) |
| `test_optimization_config_loader.py` | Spec fields parsed, `sweep_name` defaults to file stem, missing spec raises, unknown key rejected (`extra='forbid'`) |

---

## Why unit-only

A full sweep runs N real batches and depends on imported tick data — slow and environment-specific,
so it is not in the automated suite. The pure stages above (generator, override, ledger, analyzer,
validator) carry the logic and are fully covered. The integration path (runner → batches → ledger →
report) is verified by running the `🎛 Optimization: Cautious MACD Grid` launch entry, then
`optimization_cli.py report <sweep_id>`.

The **error path** is likewise covered as units (error-flagged rows + ranking exclusion + the
structural/runtime validation split). The full out-of-range end-to-end (a real combination that fails
at setup → error row → excluded → warned in the report) is verified manually with a 2-combo sweep
(one valid, one out-of-range value), kept out of the suite to stay fast + data-independent.
