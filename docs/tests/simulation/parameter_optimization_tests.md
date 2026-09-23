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
| `test_run_results_ledger.py` | Append→read round-trip (real `RunSummary`), one fragment per run, same-second / distinct-set no overwrite, filter by `sweep_id`, empty-ledger read, JSON round-trip, **typed `read_rows`** (parsed + nullable), **error rows** (`status` is a flag on the row and the figures SURVIVE it; only a run with no currencies writes the figureless row), **the reduction map** (every column declares how it combines across rows — SUM / MAX / DERIVE / LAST / IDENTITY / UNION / SPAN — with the cumulative extrema pinned as never-summable and the rates as never-foldable), **the control totals** (`net_pnl == gross_profit - gross_loss` inside one row, and a rate re-derived from its components across rows where folding the rates gives a different answer), **schema-evolution-safe read** (old fragment without a column still reads), **sweep objective + direction persisted** (report defaults to them), **every declared column is a typed field** (a column `RunResultRow` does not declare is written and read by nobody — Pydantic drops the unknown key silently, which is how eight columns stood unreachable for months), **the deployment pair** (`deployment_id` + `profile_hash` survive the parquet round trip; a one-off row names no deployment rather than a placeholder), **the trial count** (a swept run records how many candidates it beat; an unswept one records 1, which is a statement, while a fragment predating the column answers None, which is not — the Deflated Sharpe Ratio needs the difference and nothing else can supply it afterwards), **a booking outliving its records** (`mark_records_pruned` stamps only the runs it was given, leaves the figures untouched, no-ops for a run that never booked, and rebuilds the index — because rewriting a fragment is exactly what makes the index stale) |
| `test_ledger_aggregation.py` | Combining rows by their DECLARED reductions — the first real caller of `COLUMN_REDUCTION`, which until then was read by nothing but a completeness test. One case per column class, and three where the obvious answer is wrong: the drawdown TRIO must come from the row that won the leader (reduced separately it pairs one period's trough with another's peak, the defect #497 removed one layer down), a rate is REBUILT from the summed components rather than averaged (folding the rates and folding the components give different numbers, which is why `gross_profit`/`gross_loss` are carried at all), and a STREAK answers None because a run of winners can cross a period boundary — 2 and 3 in adjacent periods can be a run of 5, so `max()` would answer 3 and be believed. Plus the IDENTITY check (finding 376): twenty-eight columns claim "must agree across the rows" and nothing verified it until something RELIED on the claim |
| `test_reduction_invariants.py` | Every numeric column, through its declared reduction, against invariants that follow from the CLASS — never the expected value per column, which would be a second copy of `COLUMN_REDUCTION`. Four: folding one row returns it; the order rows arrive in does not change the answer (skipped where order IS the content); a SELECTOR never invents a value that did not occur; and the sign survives. The honest limit is stated in the module: a property test proves the declaration is APPLIED correctly and cannot know whether it is the RIGHT one — `segment_max_equity` under `MAX_ABS` would satisfy every assertion here. Written after `Reduction.MAX` turned out to mean `max(key=abs)`, which a reader took at face value and 'fixed' |
| `test_ranked_csv_export.py` | The sweep's `ranked.csv`: a non-empty export WRITES rather than raising (the header came from `model_fields` and the rows from `model_dump()`, which differ by the computed `run_kind` — every non-empty sweep report raised at the last step, and no test had ever called the writer with a real row), the computed column reaches the file, and an empty ranking still leaves a readable header |
| `test_optimization_analysis.py` | **A sweep ranks CANDIDATES, not days** — since #537 a run writes one row per booking period, so sorting the rows as they arrive would put one combination's best DAY at the top and its worst below every day of a weaker one. `_scope` folds first; the tests pin that each candidate appears once, that its ranked figure is its TOTAL (10 − 4 + 6 = 12, not 10), and that `sensitivity` — which shares the same gate — measures the combination rather than a mean over days weighted by how long each run happened to last. Plus: Ranking (maximize / minimize / deterministic / unknown-objective raise, **an objective that can be undefined is rejected** while the always-measured KPIs stay rankable), typed rows, one-factor sensitivity (influence + per-level means), **error rows excluded** from ranking + sensitivity, **`summarize_sweeps`** (per-sweep grouping: start/duration, run + ok/error counts, algo, objective; non-sweep runs ignored) |
| `test_sweep_grid_validator.py` | Valid grid passes; **unknown param + out-of-range value pass** (structural-only — existence/range moved to the run's Phase 0); bad path prefix, wrong decision/worker path length, empty value list all raise (structural fail-fast) |
| `test_sweep_mount_reuse.py` (#419) | **warm == cold** (a real mount-reused sweep yields ledger results identical to the cold reload path — off-switch toggled); **data-level abort** (an empty base mount records no runs); **OOM-signature detection** (`_has_subprocess_oom` on `BrokenProcessPool`) |
| `test_optimization_config_loader.py` | Spec fields parsed, `sweep_name` defaults to file stem, missing spec raises, unknown key rejected (`extra='forbid'`) |
| `test_sweep_directory_shape.py` | **one directory per combination, and nothing else** — a `ScenarioSet` built only to produce the shared mount used to open a run directory as a side effect, leaving a run that never ran (logs, no artifacts, a row in the API index carrying `has_reports: false`). Both halves are pinned: the phantom is gone AND `mount_build.log` exists and carries the data-load record — the point was to MOVE sweep-level output to sweep level, not to lose it |

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

## What a run consumed (#518)

`test_run_provenance.py::TestWhatARunConsumed` and two cases in `test_run_results_ledger.py` pin
the record that says WHICH DATA a run was produced over. It lives in `RunProvenance` and the ledger
row, never in the run header — the header is written at the run's start, before anything is
mounted, and has no update path by design, so it cannot know.

| Test | What it pins |
|------|-------------|
| `test_a_live_session_says_it_read_a_stream_rather_than_leaving_it_blank` | `input_plane='stream'` and empty strings. The emptiness has to MEAN something, or it is the same bytes as a sim row whose recording broke |
| `test_a_sim_run_reports_the_distinct_values_and_the_counts` | both halves: the joined distinct values say WHAT, the counts say HOW MUCH, and neither is recoverable from the other |
| `test_only_production_AND_stamped_counts_as_stamped` | the count asks the shared admissibility rule rather than spelling it out a third time |
| `test_a_run_that_read_nothing_says_so_without_inventing_a_value` | an empty scenario list yields zeros, never a placeholder |
| `test_what_a_run_consumed_reaches_the_row` | the six columns survive into the ledger fragment |
| `test_a_failed_run_still_records_what_it_read` | an ERROR row carries them too — a run that failed over development data and one that failed over production data are different failures |
| `test_a_half_re_rendered_archive_answers_with_both_bases` | `price_bases` reports `order_driven,unknown` rather than collapsing to one value or borrowing the broker's declaration — the mixture is the condition the stamp exists to expose |
| `test_a_scenario_that_mounted_no_bars_records_no_basis` | empty, because the basis is stamped on BAR files alone and a tick archive carries none |
| `test_every_ledger_column_is_declared_on_the_typed_row` | `RunResultRow` covers `LEDGER_COLUMNS`. Pydantic drops an unknown key silently, so a column added to the table and forgotten on the model is written to disk and reaches no typed reader and no exported CSV — which is what had happened to the six #518 columns and to `r_win_count` / `r_loss_count` |

`RunLedgerIndex.LOGIC_VERSION` moved 3 → 4 and then 4 → 5. Unlike the rename that took it to 3,
both appends change no existing value, so ranking across the boundary stays valid; what an older
fragment cannot do is answer the question at all, and it reads back as None. The 4 → 5 step added
`price_bases` — its own column rather than part of `data_format_versions`, because the two come
from different archives and answer different questions.

