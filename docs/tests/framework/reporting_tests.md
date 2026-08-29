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
| **Store & warnings** | `test_report_store` (28) · `test_warnings_errors_report` | the cross-run ledger; the tiered warning model (#395); that an operator Ctrl+C is told apart from a crash, both of which arrive as `shutdown_mode='emergency'` |
| **Persistence contract** | `test_report_io_encoding` | artifacts are UTF-8 on disk and read back as bytes, so neither writer nor reader lets its locale pick the codec — plus a drift guard that no IO unit reintroduces the platform default |

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
