# Reporting Pipeline Tests

`tests/framework/reporting/` — unit coverage for the unified reporting pipeline (#391–#403): the
builders that DERIVE the canonical model, the IO/store layer that PERSISTS it, and the console
renderers that PRESENT it.

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
| **Run origin & code identity** | `test_git_repo_identity` · `test_code_identity` · `test_run_origin` · `test_certificate_tree_state` | see below |
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
| `TestTheParentIdSaysWhatKindOfParentItIs` | `parent_id` holds two different things of one shape — a sweep id and a deployment id — so the header carries `parent_kind` beside it. Pins the kind through a register and a rebuild, that a standalone run names neither, and that a header written before the field still reads: it keeps its parent and reports an unknown kind, because refusing the pair would make the index unreadable for its own history |
| `TestTheIndexIsDerivedAndRebuildable` | delete the index, rebuild from the headers, get the identical result. That is the property the design rests on — an index that could not be rebuilt would be a second source of truth. Also: a run is addressable without walking the tree (the sweep combination sits one level deeper and the lookup no longer has to know — it is a `simulation` with a `parent_id`, not a type of its own), an unknown or crafted id resolves to nothing (index membership replaced a shape check — it is the stronger guard, since it accepts only ids that exist), and the run's **artifact list** is told, never inferred — the list rather than a boolean, because the two pipelines produce different sets (18 files for a sim run, 14 for a live session, measured), so a consumer that only learned "yes, some" would still be guessing which |

## `test_git_repo_identity.py` — the git reads behind a code identity (#551)

`git_info_utils` answers three questions per repository — which commit, is the working tree different
from it, and what patch puts the difference back — and none of the answers may depend on how the
developer configured git. Measured in review before these tests existed: `status.showUntrackedFiles=no`
hid an untracked strategy, an assume-unchanged file was invisible to `git status`, and
`diff.noprefix=true` made a stored patch unappliable. Each case builds the repository that produced
one of those answers in `tmp_path`, never this working tree; only the two framework-root cases read
`/app`, and only with `rev-parse`. The repositories come from `tests/shared/git_test_repos.py`, whose
OWN git calls ignore the global and system configuration — the code under test is left exposed to it,
because that it answers the same anyway is the subject.

| Class | What it pins |
|---|---|
| `TestARepositoryIsReadByItsOwnRoot` | a repository is addressed by its root, not the cwd: a clean one names its commit and no changes, a modified tracked file and an untracked file each make it dirty, a new directory is listed file by file, an ignored file does not count, and a path in no repository has no top level |
| `TestTheFrameworkRootIsTheCodesOwn` | with the cwd in another repository, the framework root and the commit are still the checkout the CODE is imported from |
| `TestNoGitIsUnknownNeverAnError` | without a git binary every read answers None and none raises |
| `TestAUserConfigurationCannotHideAChange` | `status.showUntrackedFiles=no` hides nothing; a file flagged assume-unchanged or skip-worktree counts as changed (`!h <path>`) and its edit is in the patch — forced deterministic with `utime` and `core.trustctime=false`, because `update-index` clears only the first of the two flags and a stale stat entry otherwise hides a same-size edit; a submodule edited under `diff.ignoreSubmodules=all` still counts |
| `TestThePatchRendersTheSameUnderAnyConfiguration` | under `diff.noprefix`, `diff.mnemonicPrefix`, `diff.context=0`, colour and an external diff driver, **the patch applied with `git apply --binary` onto a fresh clone reproduces the tree byte for byte**, and its bytes equal the ones rendered without those settings |
| `TestTheRepositorysOwnIndexIsNeverTouched` | the patch is rendered over a temporary index copy: the real index is byte-identical afterwards, flags and `??` entries included |
| `TestTheTemporaryIndexKeepsGitsRacyCheck` | the copy keeps the index's mtime, so an entry git can only judge by content (same size, same second) still reaches the patch — built deterministically with a held `index.lock` |
| `TestAPatchWithoutAnOrdinaryHead` | an unborn HEAD with staged and untracked files yields a patch against the empty tree; a SHA-256 repository restores from its patch, and an unborn one diffs against ITS OWN empty tree (skipped where git cannot create one) |
| `TestIgnoredFilesAreListed` | every file of an ignored directory is listed; a directory nothing ignores lists nothing |
| `TestTheReadsAreRunAtMostOncePerProcess` | a second read answers the first state; `clear_git_caches()` reaches every cache — each read goes back to git after one clear |

## `test_code_identity.py` — which code ran, read from real temporary repositories (#551)

A run header used to name this repository's commit and nothing else, while a user strategy lives in
a repository of its own. These tests pin the builder that closes that gap, against repositories
created in `tmp_path` — never against this working tree, whose state would decide the result. An
autouse fixture stands a temporary repository in for the builder's FRAMEWORK root
(`get_framework_root`) for the same reason, and because `git status` on this tree costs ~1.8 s.
Every read is cached per process — git's and the package digests — so each test clears
`clear_git_caches()` AND `clear_package_digest_cache()` before and after; clearing one leaves the
other holding the previous case.

| Class | What it pins |
|---|---|
| `TestTheFrameworkRepositoryIsAlwaysStated` | a dirty framework records its changes, a content diff hash and the patch reference its sink returned — the patch stored under the SHA256 of its own bytes, restorable onto a fresh clone; a clean one is restorable from its commit alone and keeps no patch; without git the state is UNKNOWN, still names the checkout and never counts as clean; a checkout git refuses to read is unknown, never "unversioned" |
| `TestTheDiffIdentifiesAndRestoresTheTree` | the diff hash is stable across captures and moves with the content; the stored patch, read back from the real store by the recorded reference, restores the tree; a clean repository records no diff; **the diff hash is the same with and without hostile diff settings, and for the same delta in a SHA-1 and a SHA-256 repository** — whose patch bytes differ |
| `TestAComponentNoCommitCanContainIsUnversioned` | a strategy in a directory the framework repository ignores, and a strategy file its own repository ignores, are both unversioned: no repository, a digest that is not the digest of nothing and moves with an edit, and a dirty identity although `git status` sees a clean tree |
| `TestCredentialHomesNeverReachThePatch` | a modified placeholder and an untracked key under a `credentials/` directory are listed in `patch_excluded`, absent from the patch bytes, and restored as committed / absent while the rest of the tree comes back; the diff hash records THAT they changed and does not move with their content |
| `TestWhatAPatchCannotCarry` | an untracked nested repository and a submodule with edits inside are recorded but never restorable — the patch cannot hold their content |
| `TestTheComponentIsIdentifiedByItsPackage` | a strategy loaded from a path names its file, its repository and its declared version; the package digest is **equal for the same code committed or not**, moves when a SIBLING module changes while the version string does not, and ignores a bytecode cache; a file at a repository's root is its own package — a sibling file does not move it; a component in no repository is identified by digest but never clean; one named by several scenarios is recorded once |
| `TestEveryComponentOfARunIsRecorded` | a CORE decision, a CORE worker and a path worker each carry their declared version, their file and their repository; the framework is not listed a second time among the other repositories |
| `TestAComponentThatCannotLoadIsRecordedWithoutASource` | a decision or worker module raising `NameError` at import, and a `get_metadata()` that raises, degrade to an entry without a source plus a warning naming it — the components beside it are still recorded; a missing file likewise |
| `TestTheCaptureDescribesTheTreeItStartedFrom` | a second capture in one process repeats the first after an edit (cached); `verify_component_digests` is quiet while nothing moved and names the component whose package was edited, gained a file, or lies in no repository; a new process sees the edit |

## `test_certificate_tree_state.py` — a certificate is not dirtied by its own artifact (#466)

Runs `get_git_info` against a scripted `git`. The stand-in drops the `-C <root>` and pinned
`-c key=value` arguments before its lookup and FAILS on any call it does not know, so a new git call in
the reader shows up here first. Pins that an untracked artifact under the reports directory is exempt,
and that the exemption stays narrow: a modified tracked file there, an untracked file elsewhere, a
sibling directory sharing the prefix and an entry flagged assume-unchanged all still count.

## `test_run_origin.py` — where the two blocks are written and read (#551)

The capture is mostly replaced here: what is under test is the wiring from the entry points to the
header, and from the header to the index and the ledger. One class runs a REAL capture, because the
ledger's versions depend on the component resolution alone and nothing else would go red.

| Class | What it pins |
|---|---|
| `TestTheOriginIsAlwaysStated` | the console states `console` / `operator` / the host, which under isolation is the declared test id; an allowed dirty start is recorded; a broken host identity file refuses before anything is stated and is left byte-identical — and refuses a SIMULATION as a configuration error: the startup-abort exit code 1, the message naming the file, no stack trace |
| `TestTheHeaderCarriesBothBlocks` | both blocks round-trip; a header written before them reads as unknown; an unreadable framework state counts as dirty |
| `TestTheScenarioSetStatesBoth` | a set constructed without a channel says `direct`, a declared one says what it was told; a reporting run captures over EVERY scenario's strategy, a run commissioned not to report captures nothing and still states its origin |
| `TestTheEntryPointsDeclareTheirChannel` | the strategy runner CLI declares `cli`; the optimization runner declares `sweep` for every combination |
| `TestTheIndexProjectsBothBlocks` | the five flat columns say what the header says, None where it says nothing; a rebuild reproduces the append exactly; an unreadable framework is unknown in `framework_dirty` and dirty in `code_dirty`; the served run list does not grow |
| `TestTheLedgerReadsItsProvenanceFromTheHeader` | a live row takes its versions and `git_dirty` from the header — dirty when only the ALGO repository is, an unresolved worker left out; the header is read from the run directory the caller holds, **never through the derived run index** — a session no index row names still finds it; a clean run is clean everywhere; no recorded identity, an unreadable header and a missing run directory read as unknown and dirty, never clean, and each says so in the run's OWN log; the row's `git_commit` is the header's framework commit, and a branch read for another commit is left out; a simulation row reads the header in its own run directory, reports the versions of the snapshot's strategy, not the union, and reports an unknown identity in its summary log |
| `TestARealCaptureReachesTheLedger` | a real capture of a CORE decision, a CORE worker and a worker loaded from a path in a committed algo repository, written into a real live header and read back through the ledger: every component has a source, the decision and worker versions are the ones the classes declare (the path worker's distinct on purpose), `git_dirty` is False — and the header's `git_commit`, its code identity and the ledger's commit and branch all name the framework repository although the first git reads ran with the cwd in ANOTHER checkout (the worktree trap) |
| `TestEachPatchStaysWithItsRepository` | a real capture over two dirty throwaway repositories: the strategy repository's patch is kept INSIDE it (`.finiex_run_patches/`, referenced relative to its root) and never copied into the run-patch store, this repository's goes to the run-patch store and leaves no patch directory behind, and a strategy repository whose patch home cannot be written costs that patch — `restorable` false, the diff hash still recorded — and nothing else. The one test class that puts the real foreign patch home back (`real_foreign_patch_homes`) |

## `test_run_tree_pruning.py` — what may never be deleted, and what each selector selects (#482)

A cleanup command is only as good as the things it refuses to touch, so this suite is organised
around the refusals first.

| Class | What it pins |
|---|---|
| `TestWhatMayNeverBeDeleted` | a run with `reporting=expected` and no artifacts survives `--keep-last 1` beside a newer sibling — it crashed before reporting, and that makes it the only record of the failure. Same for a run holding `field_study.jsonl`: raw evidence behind a real-money release certificate. And the dry run leaves the tree byte-identical, because showing must be free of consequence |
| `TestTheSelectors` | `--keep-last` counts per scenario set, not across the tree · the always-on `reporting=none` criterion · orphans stay untouched without `--orphans` · a run's own `io/` is never mistaken for an orphan |
| `TestASweepIsAFamily` | **the sweep is the unit, never the combination.** `--keep-last 1` over two 3-combination sweeps deletes all three of the older one and none of the newer — never 1 of 3, because a half-pruned sweep leaves a `ranked.csv` ranking runs that no longer exist. The sweep directory goes with its last combination, and is never itself classed as an orphan although it legitimately has no header |
| `TestKeepLastCountsPerParentKind` | **the quota is per KIND, because a shared quota belongs to the louder population.** Both parent ids are a prefix plus a timestamp, and sorting them together sorts by the prefix — so `sweep_` outranks `deploy_` on every comparison and a deployment could never be spared while enough sweeps existed, however recent it was. Two sweeps and one deployment under `--keep-last 2`: the live sessions survive although they are the newest runs in the tree. The second case shows the selector still SELECTS — the older parent of each kind goes |
| `TestAnEmptyOrStaleTree` | the two states a hand-cleared tree reaches. An empty tree is a no-op that still writes an index. And index rows whose directory is gone are **reported** — the rebuild drops them either way, so a dry run that showed an empty report while three rows were about to vanish would be lying by omission. Found by trying it, not by design |
| `TestApplyAndTheIndex` | `apply` removes exactly what `plan` decided · after a prune the index-header invariant holds in BOTH directions · one unremovable directory is reported and does not abort the rest |
| `TestTheLedgerKeepsItsRowsAndSaysWhy` | a prune removes RECORDS, never RESULTS. The pruned run's ledger row survives with its figures untouched and gains a `records_pruned_at` stamp; a run that stays keeps an unstamped row. The two stores have opposite retention on purpose, and the stamp is what stops a surviving row from implying its figures can still be recomputed from entries that are gone |

The ledger path is injected into the pruner exactly like the index and the roots, and for a
sharper reason than either: this store is WRITTEN, so a suite pointed at a throwaway tree would
otherwise stamp the real books.

The guard test was mutation-checked: disabling the `reporting=expected` branch in the pruner turns
exactly that one test red and leaves the other thirteen green.

`test_booking_periods_report.py` also pins the file/console split: `TestTheFileGetsEverythingAndOnlyTheConsoleIsTrimmed` shows the compact form keeping the heading and the reconciliation while every row goes — including the case that must never be compacted away, a report that does NOT reconcile. `TestTheSectionIsWiredIntoTheSharedRenderer` pins that it renders inside `render_all` rather than after a coordinator's capture, which is where it sat while the simulation's table reached the terminal and nothing else.

## `test_booking_segment_recorder.py` — when a period ends, and what the check costs

The recorder holds a period's state while it is open, and `check_boundary` runs on EVERY tick from
BOTH event sources. The suite has two halves because that fact has two consequences.

The BEHAVIOUR half pins that a day flip seals exactly once and at the market's own boundary: a
forex day flips at 21:00 UTC (17:00 New York) and NOT at the midnight three hours later, a feed
that goes quiet for two days still seals on the tick that returns, and a unit with no anchor books
nothing at all.

The COST half pins that an ordinary tick does not convert a timezone. It counts the calls to
`trading_day_of` by replacing it in the recorder's namespace: twenty-four ticks across one day
must produce exactly ONE conversion, and forty-eight hours of ticks exactly two — one to open a
period and one to seal it.

That half is not premature optimisation. Measured 2026-09-22, `trading_day_of` costs 1.52 µs at
UTC and 1.87 µs at America/New_York, so asking it per tick spends 2.3-2.8 s of a 1.5-million-tick
benchmark run against a 23.4 s baseline — about a tenth of the tick loop, to re-derive a date that
changes once a day. The cached boundary brings the same check to 0.071 µs. **The behaviour is
identical either way**, which is exactly why the behaviour tests cannot catch the regression:
mutation-checked, the two cost tests go red against the uncached recorder (24 conversions instead
of 1) while all five behaviour tests stay green.

## `test_booking_segment_builder.py` — one booking period, from the records inside it

The Hauptbuch step. Every case that matters is a trade that CROSSES a boundary, so the file is
organised around the six shapes a trade can have against two periods: wholly inside the first,
wholly inside the second, opened before the first, crossing, still open at the end, and closing
exactly ON the boundary.

The rule under test is that a trade belongs to the period it was **closed** in, window end
exclusive. Two consequences are pinned as properties rather than as examples. The periods
**partition** the records — `Σ trade_count` equals the closed trades and `Σ net_pnl` the realised
total, so nothing is counted twice and nothing falls between. And a crossing trade books its
**whole** result in the later period, which is correct bookkeeping and deliberately not the whole
story about the earlier one: that is why a period also carries its equity band.

One test exists only to stop a plausible shortcut: the period's LOW is tracked, not derived. A
run of 100 → 90 → 120 has a peak of 120, a decline of 10 against the peak that stood then, and a
low of 90 — so `peak − drawdown` answers 110, a value that never occurred.

## `test_booking_periods_report.py` — the table, and the line that makes it trustworthy

The reconciliation is the point. A column of period summaries is believed because it agrees with
the figure the run reports by a **different** route (the portfolio aggregate) — two derivations
of one number meeting is evidence, one number printed twice is not. A mismatch is reported rather
than raised: it is the finding the table exists to surface, and a trade realised outside every
period looks exactly like it.

A tolerance test guards the opposite failure: thirty additions do not land on the same last bit
as one aggregate over the same trades, and a table that cried mismatch over 1e-10 would be a
table nobody reads.

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

The grouping lives in `builders/deployment_history_builder.py` since #539, because the API serves
the same history and two derivations of one deployment are two chances to disagree about what its
drawdown is. The console module kept its two renderers and nothing else; the CLI's output was
compared byte for byte across the move.

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
