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
| `test_run_results_ledger.py` | Append→read round-trip (real `RunSummary`), one fragment per run, same-second / distinct-set no overwrite, filter by `sweep_id`, empty-ledger read, JSON round-trip, **typed `read_rows`** (parsed + nullable), **error rows** (`status` is a flag on the row and the figures SURVIVE it; only a run with no currencies writes the figureless row, its figure columns empty and never 0, and read back with no figure either — over every numeric figure column, and from an older error row that stored zeros, #646), **the reduction map** (every column declares how it combines across rows — SUM / MAX / DERIVE / LAST / IDENTITY / UNION / SPAN — with the cumulative extrema pinned as never-summable and the rates as never-foldable), **the control totals** (`net_pnl == gross_profit - gross_loss` inside one row, and a rate re-derived from its components across rows where folding the rates gives a different answer), **schema-evolution-safe read** (old fragment without a column still reads), **sweep objective + direction persisted** (report defaults to them), **every declared column is a typed field** (a column `RunResultRow` does not declare is written and read by nobody — Pydantic drops the unknown key silently, which is how eight columns stood unreachable for months), **the deployment pair** (`deployment_id` + `profile_hash` survive the parquet round trip; a one-off row names no deployment rather than a placeholder), **where the orders went** (`orders_to` comes back as the enum it was written as, and a run without one reads None), **the trial count** (a swept run records how many candidates it beat; an unswept one records 1, which is a statement, while a fragment predating the column answers None, which is not — the Deflated Sharpe Ratio needs the difference and nothing else can supply it afterwards), **a booking outliving its records** (`mark_records_pruned` stamps only the runs it was given, leaves the figures untouched, no-ops for a run that never booked, and rebuilds the index — because rewriting a fragment is exactly what makes the index stale), **a session's sealed days** (#476: `write_sealed_periods` writes what the start knows and leaves `RUN_END_COLUMNS` empty, each seal replaces the fragment whole, the session's last write replaces its sealed days with the complete set, and the declaration is complete in both directions — every column the last write fills and a seal leaves empty is declared run-end, and every declared one is such a column), **a day's own order counts** (#476: a period row carries the orders the period handled, a run read back over its days adds them up, and a period nothing counted leaves every order column empty rather than zero), **what an empty cell means** (every nullable figure declares whether its null is undefined or unmeasured, both ways — a figure being a column whose reduction's kind is not a description) |
| `test_ledger_aggregation.py` | Combining rows by their DECLARED reductions — the fold over the figure catalog (#557), whose reductions were once read by nothing but a completeness test. Its completeness tests hold the fold's TABLE both ways: every reduction that folds on its own has a function and no other has one, every member is declared by some figure, every DERIVE figure has exactly one derivation or weight, every weight is a summed count, and every companion follows a leader that selects one row — a fold written as branches once let a member without one empty its column unseen. One case per column class, and three where the obvious answer is wrong: the drawdown TRIO must come from the row that won the leader (reduced separately it pairs one period's trough with another's peak, the defect #497 removed one layer down), a rate is REBUILT from the summed components rather than averaged (folding the rates and folding the components give different numbers, which is why `gross_profit`/`gross_loss` are carried at all), and a STREAK answers None because a run of winners can cross a period boundary — 2 and 3 in adjacent periods can be a run of 5, so `max()` would answer 3 and be believed. The rows of runs that failed before they measured anything fold to no figure, never to the model's defaults (#646). Plus the IDENTITY check (finding 376): twenty-eight columns claim "must agree across the rows" and nothing verified it until something RELIED on the claim. And a STOCK over several accounts: `LAST` answers None when the rows belong to more than one unit — a backtest's scenarios each trade their own balance, so the latest reading of one of them is not the run's — while per unit it stays exact and a flow still adds up. Order counts a booking-period row never carried stay unmeasured (None) when folded, and `FIRST` takes the earliest row's own value — unknown when that row recorded none |
| `test_reduction_invariants.py` | Every numeric column, through its declared reduction, against invariants that follow from the CLASS — never the expected value per column, which would be a second copy of `COLUMN_REDUCTION`. Four: folding one row returns it; the order rows arrive in does not change the answer (skipped where order IS the content); a SELECTOR never invents a value that did not occur; and the sign survives. `FIRST` joins `LAST` among the order-sensitive selectors. The honest limit is stated in the module: a property test proves the declaration is APPLIED correctly and cannot know whether it is the RIGHT one — `period_max_equity` under `MAX_ABS` would satisfy every assertion here. Written after `Reduction.MAX` turned out to mean `max(key=abs)`, which a reader took at face value and 'fixed'. Beside them, every reduction names its kind of figure, and only `SUM` is a flow — what a total over several composites may take |
| `test_ranked_csv_export.py` | The sweep's `ranked.csv`: a non-empty export WRITES rather than raising (the header came from `model_fields` and the rows from `model_dump()`, which differ by the computed `run_kind` — every non-empty sweep report raised at the last step, and no test had ever called the writer with a real row), every computed column reaches the file (`run_kind`, `succeeded_count`) — held as a property, not a count — and an empty ranking still leaves a readable header |
| `test_optimization_analysis.py` | **A sweep ranks CANDIDATES, not days** — since #537 a run writes one row per booking period, so sorting the rows as they arrive would put one combination's best DAY at the top and its worst below every day of a weaker one. `_scope` folds first; the tests pin that each candidate appears once, that its ranked figure is its TOTAL (10 − 4 + 6 = 12, not 10), and that `sensitivity` — which shares the same gate — measures the combination rather than a mean over days weighted by how long each run happened to last. Plus: Ranking (maximize / minimize / deterministic / unknown-objective raise, **an objective that can be undefined is rejected** while the always-measured KPIs stay rankable), typed rows, one-factor sensitivity (influence + per-level means), **error rows excluded** from ranking + sensitivity, **`summarize_sweeps`** (per-sweep grouping: start/duration, run + ok/error counts, algo, objective; non-sweep runs ignored) And a sweep in two account currencies is ranked WITHIN each (`rank_per_currency`), currencies in order — a money objective in EUR and in USD is not one scale. **A figure some runs did not measure** ranks the runs that carry it, leaves the others out and counts them (`unranked_count`); the sensitivity reads only the measured runs. `TestTheListNamesEachSweepsBestCombination` (#666): the sweep list's best combination per currency is the detail's first in that currency, a tie goes as the detail breaks it, the recorded direction ranks, a currency nobody measured has none, the list's sweep lines carry them, and a sweep whose recorded objective cannot rank has none while the list still stands and its own detail refuses. `TestASweepRanksByWhatItsAccountsHoldAtTheEnd` (#32-E): a run of two scenarios ranks by its accounts' summed end equity although `final_equity` is null over the two, the end equity and the realised P&L rank two combinations apart (+50 holding nothing over +80 holding a position 120 under water), a run from before the columns is left out and counted, and the sweep list names the best by it. `TestAnObjectiveThatIsNoNumberIsRefused`: a name, a flag or a structure is refused, and a refusal lists only the figures that can rank |
| `test_sweep_grid_validator.py` | Valid grid passes; **unknown param + out-of-range value pass** (structural-only — existence/range moved to the run's Phase 0); bad path prefix, wrong decision/worker path length, empty value list all raise (structural fail-fast); an objective the ranking would refuse — undefined for a run, one account's reading, no number, unknown — stops the sweep before its first combination, while `net_pnl`, `total_final_equity` and a drawdown pass (#32-E) |
| `test_optimization_analysis.py::TestARunWithNoR` | a ranking by `expectancy` leaves a run with no R out and counts it, a row written as `0.0` before the field could be empty reads empty too; `test_run_results_ledger.py` holds that an empty value reads back empty |
| `test_optimization_analysis.py::TestARankingThatSpansLogicVersions` | a ranking over rows of more than one ledger `logic_version` is flagged with the versions and their counts; one case pins the two generations of `unstamped_input_files` (#647) |
| `test_sweep_mount_reuse.py` (#419) | **warm == cold** (a real mount-reused sweep yields ledger results identical to the cold reload path — off-switch toggled); **data-level abort** (an empty base mount records no runs, and the runner says nothing ran); **a refused base** (the sweep names the refusal and the CLI exits 2); **a grid that decides the need** (one arm declares a stop and the other keeps it itself, and both run under the exception); **the mount build closes its log** (the base set exists only to build the mount, so its log closes once the build is done, on the abort path too); **OOM-signature detection** (`_has_subprocess_oom` on `BrokenProcessPool`) |
| `test_optimization_config_loader.py` | Spec fields parsed, `sweep_name` defaults to file stem, missing spec raises, unknown key rejected (`extra='forbid'`) |
| `test_sweep_directory_shape.py` | **one directory per combination, and nothing else** — a `ScenarioSet` built only to produce the shared mount used to open a run directory as a side effect, leaving a run that never ran (logs, no artifacts, a row in the API index carrying `has_reports: false`). Both halves are pinned: the phantom is gone AND `mount_build.log` exists and carries the data-load record — the point was to MOVE sweep-level output to sweep level, not to lose it |

---

## Why unit-only

A full sweep runs N real batches and depends on imported tick data — slow and environment-specific,
so it is not in the automated suite. The pure stages above (generator, override, ledger, analyzer,
validator) carry the logic and are fully covered. The integration path (runner → batches → ledger →
report) is verified by running the `✨ Optimization: Cautious MACD Grid` launch entry, then
`optimization_cli.py report <sweep_id>`.

The **error path** is likewise covered as units (error-flagged rows + ranking exclusion + the
structural/runtime validation split). The full out-of-range end-to-end (a real combination that fails
at setup → error row → excluded → warned in the report) is verified manually with a 2-combo sweep
(one valid, one out-of-range value), kept out of the suite to stay fast + data-independent.

## How many units a run had (#666)

`test_run_provenance.py::TestSessionProvenance` pins where a run's unit counts come from: a session
that failed in an emergency is one unit, one failed — the counts of its warnings-errors outcome,
the same the route serves — a clean session is one unit, none failed, a session handed no report
counts none (null, never zero), and `test_a_backtest_counts_its_units_from_its_outcome` carries a
backtest's 3 units, 1 failed from its outcome onto the provenance the ledger row is written from.
`test_a_sealed_row_leaves_exactly_the_run_end_columns_empty` holds the two counts among the
columns a sealed day leaves empty, so a running session's row says "not known yet", and
`test_an_older_fragment_counts_no_units` reads a fragment written before the columns as null, and
`test_a_row_counts_the_units_that_did_not_fail` derives the run list's third count on the ledger row
the sweep detail serves.

## What a run's accounts held at its end (#32-E)

`test_every_row_of_a_run_carries_what_its_accounts_held_at_the_end` books two scenarios: each period
row keeps its own account's `final_equity`, and every row repeats the run's sum over both beside the
capital they started with. The sealed-row test holds the two sums among the run-end columns, and
`test_an_older_fragment_records_no_closing_sums` reads a fragment written before them as null.
`test_every_nullable_figure_declares_what_its_null_means` lets a description declare a null meaning
where a ranking may order by it — a number — and refuses one on a description that is no number
and on a column that cannot be null.

## What a run consumed (#518)

`test_run_provenance.py::TestWhatARunConsumed` and two cases in `test_run_results_ledger.py` pin
the record that says WHICH DATA a run was produced over. It lives in `RunProvenance` and the ledger
row, never in the run header — the header is written at the run's start, before anything is
mounted, and has no update path by design, so it cannot know.

| Test | What it pins |
|------|-------------|
| `test_a_live_session_says_it_read_a_stream_rather_than_leaving_it_blank` | `input_plane='stream'` and empty strings. The emptiness has to MEAN something, or it is the same bytes as a sim row whose recording broke |
| `test_a_sim_run_reports_the_distinct_values_and_the_counts` | both halves: the joined distinct values say WHAT, the counts say HOW MUCH, and neither is recoverable from the other |
| `test_a_file_without_a_stamp_counts_whatever_its_class` | `unstamped_input_files` counts the files whose evidence grade is not `stamped`, whatever their class — a stamped external file does not count (#647); admissibility is the post-run check's question, not this count's |
| `test_the_clock_rules_are_those_the_tick_files_state` · `test_no_stated_clock_rule_leaves_the_field_empty` | `server_clock_rules` joins the rules the tick files state; a file that states none adds nothing, so an empty value never reads as a rule (#647) |
| `test_a_warmup_into_external_bars_reaches_the_record` | `external_warmup_timeframes` joins the timeframes whose warmup reached into external bars, over every scenario (#647) |
| `test_a_run_that_read_nothing_says_so_without_inventing_a_value` | an empty scenario list yields zeros, never a placeholder |
| `test_what_a_run_consumed_reaches_the_row` | the consumption columns survive into the ledger fragment |
| `test_the_clock_rules_and_the_external_warmup_read_back_typed` | the two #647 columns reach the typed row, which carries the ledger's current `logic_version` — the number that says which meaning its `unstamped_input_files` holds |
| `test_a_failed_run_still_records_what_it_read` | an ERROR row carries them too — a run that failed over development data and one that failed over production data are different failures |
| `test_a_half_re_rendered_archive_answers_with_both_bases` | `price_bases` reports `order_driven,unknown` rather than collapsing to one value or borrowing the broker's declaration — the mixture is the condition the stamp exists to expose |
| `test_a_scenario_that_mounted_no_bars_records_no_basis` | empty, because the price basis is stamped on BAR files alone and a tick archive carries none |
| `test_every_ledger_column_is_declared_on_the_typed_row` | `RunResultRow` covers `LEDGER_COLUMNS`. Pydantic drops an unknown key silently, so a column added to the table and forgotten on the model is written to disk and reaches no typed reader and no exported CSV — which is what had happened to the six #518 columns and to `r_win_count` / `r_loss_count` |

`RunLedgerIndex.LOGIC_VERSION` moved 3 → 4 and then 4 → 5. Unlike the rename that took it to 3,
both appends change no existing value, so ranking across the boundary stays valid; what an older
fragment cannot do is answer the question at all, and it reads back as None. The 4 → 5 step added
`price_bases` — its own column rather than part of `data_format_versions`, because the two come
from different archives and answer different questions.


## Where a run's orders went (#362)

`test_run_provenance.py::TestWhereTheOrdersWent` pins `orders_to` on the ledger row: a mock
session simulates, a session whose broker posture is real money goes to the venue, and a backtest
simulates by construction. The session's value comes from the one dry-run rule its header was
stamped with (`resolve_orders_to`), so the header and the row cannot disagree about whether real
money moved — and a deployment can say which of its sessions did.

## Which code a session started on (#559)

`test_run_provenance.py::TestTheCodeStandReadsTheHeader` pins `code_stand` on the ledger row. It
states what the header recorded and not what git answers. This repository is named `framework`, never
by its root, which would move with every deploy. A session that did not read the history keeps the
recorded short hash, and an unknown commit stays unknown and is never clean. A header without an
identity, a missing run directory and an identity with no framework entry are each an unknown stand
and never a missing one, because an empty column means the row predates the column. A backtest leaves
it empty.

`test_run_results_ledger.py` reads it back typed, beside a row without one, from an older fragment and
from a sealed day; a malformed stand names its run; and two sessions read back as a code change
through the real files and the real fold.
`test_reduction_invariants.py::TestTheCodeStandFoldsAsAnIdentity` holds the declared reduction: a fold keeps it typed, a row without one does not erase it, two stands
are a reported conflict and rows that agree are none. `test_ranked_csv_export.py` writes it as JSON in
the export and leaves the cell empty where there is none.

`RunLedgerIndex.LOGIC_VERSION` moved with the column. The append changes no existing value, so an
older fragment reads back None: no stand, never a change.
