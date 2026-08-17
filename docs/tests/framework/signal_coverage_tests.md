# Signal Coverage Tests

Unit tests for signal-series continuity: `SignalCoverageReport` and the two
`ScenarioDataValidator` checks that consume it.

**Location:** `tests/framework/signal_coverage/`
**Mark:** `framework`, `unit`
**Launch:** `🧩 Pytest: Signal Coverage (All)`

## Files

| File | Covers |
|---|---|
| `test_signal_coverage_report.py` | cadence measurement, gap detection, classification, window queries |
| `test_signal_scenario_validation.py` | pre-load availability checks, post-load stretch check |

## What the tests pin

### Cadence is measured, not configured

A signal series is an eval-cadence grid with processing jitter (~97% of real
envelopes land within 60s of the bar close). The report takes the **median**
snapshot distance and applies the same 2x tolerance the tick report uses for its
bar interval. `test_jitter_below_tolerance_is_no_gap` pins that ordinary jitter
never registers as a gap.

### Weekend is NOT an expected closure

`TestWeekendIsNotExpected` is the regression guard for the signal-specific rule:
the producing engine runs 24/7 regardless of the traded market, so a Friday →
Monday hole is a **real outage** (LARGE), never `GapCategory.WEEKEND`. Reusing
the tick report's market weekend rule here would silently absolve exactly the
outages this report exists to surface.

### Thresholds

Signal gaps use their own thresholds from `discoveries_config.json`
(`signal_coverage.thresholds`): short < 30min, moderate < 1h, large above — a
tighter ladder than the tick report's 4h, because no producer restart takes
longer than an hour.

### Provenance fields

`TestDataOrigin` pins the mock-versus-real discriminator, including the two
absence cases that must never read as "real": a parquet written **before** the
column existed (no column at all — must not raise) and a present-but-empty value.
Both resolve to `''` = unknown. A source carrying both values reads as `mixed`
and counts as synthetic.

`TestConfigFingerprint` pins the same absence semantics for the comparability
marker, plus the case that matters later: two fingerprints inside one archive
read as `mixed` — the stretches on either side are then not one series.

`TestTriggerReason` pins the pass-cause counts. Three guarantees beyond the usual
absence handling: counts are per **envelope**, not per parquet row (a naive
`value_counts` would weight each snapshot by its symbol count); an unrecognized
value is kept rather than rejected — the vocabulary is closed on the producer
side, but a future engine version must not break our reader; and a **partially
stamped** archive states its unattributed share
(`test_partially_stamped_archive_states_the_unknown_share`).

That last case is the one real archives actually hit: a producer gains the field
during a restart, so one archive holds both. Counting only the stamped envelopes
would render `54 scheduled · 2 boot · 2 breaking` next to `2,512 snapshots` — a
line that reads like a complete composition. `trigger_unknown` is kept separate
from `trigger_reasons` so no renderer can lose it.

### Window queries

`has_snapshot_at_or_before` / `latest_snapshot_at_or_before` mirror the worker's
own resolution (nearest snapshot at or before the tick), so the validator's
verdict matches what the run will actually see.

### Validator behaviour

| Case | Outcome |
|---|---|
| scenario binds no signal source | skipped, no findings |
| source declares `data_origin: synthetic` | **warning** — generated data, not a market record |
| source/symbol not imported | **error** — scenario excluded (§33 config/data) |
| window closes before the series opens | **error** — nothing can ever resolve |
| no snapshot at or before window start | **warning** — run starts blind, with the blind duration |
| window start sits inside a gap | **warning** — run starts on an already-aged snapshot |
| window reaches past the last snapshot | **no finding** — contracted degradation (#434) |
| forbidden gap inside the tick stretch | **error** — category not in `allowed_gap_categories` |
| gap outside the tick stretch | ignored — a signal is only resolved at ticks |

The stretch tests run through the public `validate_loaded_data` path — the same
call the batch makes in Phase 5 — rather than reaching into a private method.

`TestAvailabilityWiring` covers the Phase 1 → Phase 2 glue in
`DataCoverageReportManager`: that a signal finding actually reaches the scenario's
`ValidationResult`, that an error excludes the scenario and a warning does not.
The tick coverage report is stubbed at the `DataCoverageReportCache.get_report`
seam so only the signal checks can produce findings.

## Related: the finished archive

`tests/data/signal_import/` carries `TestFinishedArchive`, which pins the import-side
counterpart: an imported JSONL moves to `data/finished/signals/` with its structure
intact, a re-run without `--override` finds nothing and reports no error, and
`--override` reads the archive back. Relevant here because a coverage report is only
as complete as the parquet tree the import produced.

## Related: the preceding-bucket guarantee

The report reads the **whole** archive; the runtime reads a window-scoped file
subset. That divergence once hid a defect — a window opening at a day boundary
resolved a gap at tick 1 because the previous bucket was never loaded, while the
coverage report correctly saw a snapshot 10 minutes earlier. The fix lives in
`SignalIndexManager.get_relevant_files` (return the preceding bucket when no
overlapping one begins at or before `start`) and is pinned by
`tests/data/signal_import/` — `two_bucket_index` plus
`test_first_tick_resolves_a_signal`.

Worth knowing when changing either side: **a coverage verdict and a runtime
resolution can legitimately differ**, because they read different file sets. When
they disagree about whether data exists, the runtime is the one to trust — and
the disagreement is the bug signal.

## Fixtures

Both files write minimal signal parquets into `tmp_path` carrying only the
`collected_msc` / `symbol` columns. That is all the report reads (column
projection), so the tests exercise the real read path without needing an
imported source.
