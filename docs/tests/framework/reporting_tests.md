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

## Running

```bash
python -m pytest tests/framework/reporting/ -v
```

Or via launch.json: `🧩 Pytest: Reporting (All)`.
