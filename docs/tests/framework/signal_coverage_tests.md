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

### Window queries

`has_snapshot_at_or_before` / `latest_snapshot_at_or_before` mirror the worker's
own resolution (nearest snapshot at or before the tick), so the validator's
verdict matches what the run will actually see.

### Validator behaviour

| Case | Outcome |
|---|---|
| scenario binds no signal source | skipped, no findings |
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

## Fixtures

Both files write minimal signal parquets into `tmp_path` carrying only the
`collected_msc` / `symbol` columns. That is all the report reads (column
projection), so the tests exercise the real read path without needing an
imported source.
