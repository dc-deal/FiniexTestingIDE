# Reporting Pipeline Tests

`tests/framework/reporting/` — 25 files, **200 tests**. Unit coverage for the unified reporting
pipeline (#391–#403): the builders that DERIVE the canonical model, the IO/store layer that PERSISTS
it, and the console renderers that PRESENT it.

## What this suite is for

The pipeline's whole point is that one model feeds every surface — console, file, CSV, API JSON —
so a number is derived **once** and rendered many times. That only holds if the derivation is tested
independently of any surface, which is what these tests do: they build a model from real result
objects and assert on the model, then render it and assert on the text.

Two rules the suite exists to defend:

- **No decisions in reports.** A builder calculates and a renderer prints; a verdict ("is this worth
  a warning?") belongs to a validator. Tests that pin a threshold here are pinning *presentation*
  (colour, order), never *whether* something fires.
- **Stamp at the source.** Any value a report needs is resolved once from its owner and carried on
  the record. A renderer that re-resolves — instantiating a config manager, re-reading a broker
  spec — drifts per surface, so the tests construct records and never let a renderer reach outward.

## Layout

| Group | Files | What they pin |
|---|---|---|
| **Trade & order** | `test_trade_history_report` · `test_trade_history_render` · `test_order_history_report` · `test_trade_excursion` · `test_trade_projection` | the trade record end to end: MAE/MFE in the instrument's own unit, R/expectancy, per-execution rows |
| **Portfolio & execution** | `test_portfolio_report` · `test_aggregated_portfolio_report` · `test_execution_stats_report` · `test_execution_header_summary` · `test_pending_orders_report` | balances and currency aggregation; execution counters; the header a reader sees first; the figures derived in the builder rather than the renderer (`max_dd_pct`, the spot estimate over the stamped currency split, `execution_rate_pct`) |
| **Run-level** | `test_run_summary` · `test_run_summary_render` · `test_run_meta_report` · `test_run_console_renderer` · `test_shared_report_coordinator` | the cross-section KPI model and the one coordinator both pipelines share; an undefined `profit_factor` survives the JSON round trip as `None` |
| **Signal** | `test_signal_report` | see below |
| **Feed stability** | `test_feed_stability_report` | disturbance episodes across both staleness domains (#451) — every boundary derived from observed state, a stress config contributing only its label |
| **Diagnostics** | `test_profiling_report` · `test_worker_decision_report` · `test_block_splitting_report` · `test_scenario_details_report` · `test_broker_report` | per-worker timing, decision breakdown, window splitting, broker facts; the #420 cadence figures derived once in the builder |
| **Deployment** | `test_deployment_history` · `test_profile_fingerprint` | see below |
| **Store & warnings** | `test_report_store` (31) · `test_warnings_errors_report` | the cross-run ledger; that a `run_group` does not hide a run from the index or from any report route; the tiered warning model (#395); that an operator Ctrl+C is told apart from a crash, both of which arrive as `shutdown_mode='emergency'`; that a finding's origin (`check` / `domain`) reaches `WarningRow`, that an advisory sharing a result with a rejection is kept, and that a Tier-2 log-pot row claims no origin |
| **Persistence contract** | `test_report_io_encoding` | artifacts are UTF-8 on disk and read back as bytes, so neither writer nor reader lets its locale pick the codec — plus a drift guard that no IO unit reintroduces the platform default |

## `test_run_identity.py` — the id, the header, the derived index (#475)

A run used to be identified by a second-resolution timestamp directory name. Measured: 188 runs,
4 collisions, two across run types — and the resolver returned the FIRST match, so the API served
one run's artifacts under another run's id. A frontend could not detect that, because nothing in a
report payload said which run it belonged to. Both halves are closed now: the id is distinct, and
**every artifact carries the `run_id` it was built from**, so a consumer can check what it
received instead of trusting the route it asked on.

| Class | What it pins |
|---|---|
| `TestTheIdIsDistinctAndStillReadable` | two runs in the same second get different ids; the timestamp prefix keeps byte order equal to time order (the index sorts on it, the sweep ranking tie-breaks on it); a taken id is re-minted rather than joined; **the id stays inside `[0-9a-f_]`** — a consumer interpolates it into a URL path unencoded, and that safety is now asserted rather than merely true |
| `TestTheHeaderSurvivesTheRunItDescribes` | the header round-trips, and it stands alone — written at the run's START, so a run that crashes before producing anything is still identifiable |
| `TestTheIndexIsDerivedAndRebuildable` | delete the index, rebuild from the headers, get the identical result. That is the property the design rests on — an index that could not be rebuilt would be a second source of truth. Also: a run is addressable without walking the tree (the sweep combination sits one level deeper and the lookup no longer has to know — it is a `simulation` with a `parent_id`, not a type of its own), an unknown or crafted id resolves to nothing (index membership replaced a shape check — it is the stronger guard, since it accepts only ids that exist), and the run's **artifact list** is told, never inferred — the list rather than a boolean, because the two pipelines produce different sets (18 files for a sim run, 14 for a live session, measured), so a consumer that only learned "yes, some" would still be guessing which |

## `test_run_tree_pruning.py` — what may never be deleted, and what each selector selects (#482)

A cleanup command is only as good as the things it refuses to touch, so this suite is organised
around the refusals first.

| Class | What it pins |
|---|---|
| `TestWhatMayNeverBeDeleted` | a run with `reporting=expected` and no artifacts survives `--keep-last 1` beside a newer sibling — it crashed before reporting, and that makes it the only record of the failure. Same for a run holding `field_study.jsonl`: raw evidence behind a real-money release certificate. And the dry run leaves the tree byte-identical, because showing must be free of consequence |
| `TestTheSelectors` | `--keep-last` counts per scenario set, not across the tree · the always-on `reporting=none` criterion · orphans stay untouched without `--orphans` · a run's own `io/` is never mistaken for an orphan |
| `TestASweepIsAFamily` | **the sweep is the unit, never the combination.** `--keep-last 1` over two 3-combination sweeps deletes all three of the older one and none of the newer — never 1 of 3, because a half-pruned sweep leaves a `ranked.csv` ranking runs that no longer exist. The sweep directory goes with its last combination, and is never itself classed as an orphan although it legitimately has no header |
| `TestAnEmptyOrStaleTree` | the two states a hand-cleared tree reaches. An empty tree is a no-op that still writes an index. And index rows whose directory is gone are **reported** — the rebuild drops them either way, so a dry run that showed an empty report while three rows were about to vanish would be lying by omission. Found by trying it, not by design |
| `TestApplyAndTheIndex` | `apply` removes exactly what `plan` decided · after a prune the index-header invariant holds in BOTH directions · one unremovable directory is reported and does not abort the rest |
| `TestTheLedgerKeepsItsRowsAndSaysWhy` | a prune removes RECORDS, never RESULTS. The pruned run's ledger row survives with its figures untouched and gains a `records_pruned_at` stamp; a run that stays keeps an unstamped row. The two stores have opposite retention on purpose, and the stamp is what stops a surviving row from implying its figures can still be recomputed from entries that are gone |

The ledger path is injected into the pruner exactly like the index and the roots, and for a
sharper reason than either: this store is WRITTEN, so a suite pointed at a throwaway tree would
otherwise stamp the real books.

The guard test was mutation-checked: disabling the `reporting=expected` branch in the pruner turns
exactly that one test red and leaves the other thirteen green.

## `test_signal_report.py` — two planes, and what each may claim

The largest single file after the store, because the signal section is the one that renders
**different things depending on where its data came from**.

| Plane | Sim / AutoTrader mock | AutoTrader live |
|---|---|---|
| Provenance, composition, cadence, extent, stream position | read from parquet by `SignalCoverageReport` | accumulated from arrivals by `SignalObservedAccumulator` |
| Gap classification, window coverage | measured against the market calendar | **not applicable — no archive exists** |

`TestArchivePlane` and `TestRuntimePlane` cover the first column; `TestFeedPlane` and
`TestSequencePosition` cover the second.

**`TestFeedClaimsNothingItCannotKnow` is the regression for the whole design.** Both values it
guards would otherwise be produced by a *field default* and read as a measurement:

- an empty gap map rendering as **`no gaps`** — asserting continuity for a series that was never
  analysable
- a `coverage_ratio` default of `1.0` rendering as **100 % coverage** of a window that never existed

Found by the first live observation run (2026-08-23), which produced no signal section at all: the
builder gated on a scenario map only the mock path ever fills, so the fresh/stale/blind counters
were collected on every one of 977 ticks and had nowhere to go. The tests also pin that the live
cadence is labelled `(producer)` rather than `(measured)` — a session that received three envelopes
has no sample to take a median from — and that the archive path still says `(measured)`, which is
the guard that the sim output did not move.

`TestSequencePosition` pins one rule worth stating on its own: **an epoch restart is not a hole.**
Sequence numbers restart when the producer boots, so the distance across that boundary measures
nothing, and counting it would report a restart as lost data.

## Running the Tests

```bash
python -m pytest tests/framework/reporting/ -v
```

Or via launch.json: `🧩 Pytest: Reporting (All)`.

## `test_scenario_details_origin.py` — which SCENARIO read what (#518)

The ledger records per run; this row records per scenario, in the same encoding, so the two can
be compared rather than parsed against each other. The run-level roll-up raises the question a
mixed set poses and cannot answer it — two brokers exist here and they differ on the account
model, the price formation and the market type at once.

| Test | What it pins |
|------|-------------|
| `test_the_three_values_reach_the_row` | the distinct values arrive at the per-scenario grain |
| `test_a_FAILED_scenario_still_says_what_it_read` | the row an early return could have skipped — failing over development data and over production data are different failures |
| `test_the_encoding_is_the_one_the_ledger_uses` | sorted AND distinct: the same files in a different read order must produce the same string, or two identical runs look different |
| `test_the_price_basis_reaches_the_row_at_this_grain_too` | the basis is read from the BAR index, and per scenario because that is where a mixed archive is visible — a run-level `order_driven,unknown` is true and useless for deciding which scenario's numbers to trust |
| `test_a_scenario_that_read_nothing_reports_empty_rather_than_a_placeholder` | empty is empty, never a stand-in |

## `test_deployment_history.py` — a restarted bot read back as one history (#497)

A live session's ledger row is written under its own run id, and until the deployment identity
the ledger's only other reader filtered on `sweep_id` — which a live session does not have. So
the row was written and unreachable (§44 calls that a store with no read path). These pin the
grouping and, just as deliberately, what it refuses to do.

| Test | What it pins |
|------|-------------|
| `test_sessions_of_one_deployment_become_one_history` | the grouping itself |
| `test_a_one_off_row_joins_nothing` | not even a placeholder group — inventing one asserts a continuity nobody declared |
| `test_sessions_are_ordered_by_start_not_by_arrival` | the ledger is a set of fragments; nothing about a read returns them in order |
| `test_a_multi_currency_session_appears_once` | one run writes one row PER CURRENCY — counting rows would report a two-currency bot as twice-restarted |
| `test_a_changed_operation_is_marked_separately` | a raised stop level is not a different strategy; that separation is why there are two hashes |
| `test_an_unreadable_timestamp_yields_no_gap_rather_than_a_wrong_one` | an unmeasurable duration reported as unmeasured costs a blank; reported as a figure it costs an investigation |
| `test_the_rows_carry_the_running_figure` | the drawdown column is CUMULATIVE — `max()` is the deployment's reduction and a sum double-counts |

No threshold anywhere decides what counts as too long a gap: that is a judgement about the
market and the operator's night, not about the data.

## `test_profile_fingerprint.py` — two hashes over one profile (#497)

`param_hash` covers `strategy_config` and is what #512 compares a backtest against;
`profile_hash` covers the operational rest. The property pinned here is that each answers ONLY
its own question — a value answering both would answer neither. A changed safety threshold moves
`profile_hash` and not `param_hash`; a changed strategy parameter does the reverse; a renamed or
moved profile moves neither, because where a file sits is not a property of the run.

The projection (`_plain`) is pinned separately: config blocks come in both shapes §6 allows, a
dict is ordered so a reformatted JSON file does not read as a change, and the result stays
JSON-serialisable — the `repr` fallback exists to keep the function total, and a value reaching
it would carry an address and make two identical runs fingerprint differently.
