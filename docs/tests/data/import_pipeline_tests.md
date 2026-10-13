# Import Pipeline Tests Documentation

## Overview

The import pipeline test suite validates the full tick data import lifecycle: JSON schema validation, Parquet conversion, the server clock conversion to UTC, metadata preservation, duplicate detection, structural validation, and file management.

**Test Location:** `tests/data/import_pipeline/`
**Config Source:** `configs/import_config.json` (paths, processing) and `configs/market_config.json` (each broker's `server_clock`)
---

## Fixtures (conftest.py)

### Helper Functions

| Function | Description |
|----------|-------------|
| `build_minimal_tick_json()` | Builds synthetic MQL5 JSON with configurable symbol, broker_type, tick_count, bid/ask start, custom_ticks, extra_metadata |
| `write_json_fixture()` | Writes JSON dict to a directory as `{filename}` |

### Session Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `import_test_dirs` | session | Creates `source`, `target`, `finished` tmp directories via `tmp_path_factory` |
| `populate_persistent_test_output` | session, autouse | Writes reference Parquets to `data/test/import/processed/` (cleans first, then imports 4 symbols: BTCUSD, ETHUSD, EURUSD, GBPUSD) |

### Persistent Test Output

After each test session, reference Parquets are available in `data/test/import/processed/` (paths from `import_config.json` `test_paths`). The processed directory is cleaned at session start to avoid duplicate detection conflicts. This output:
- Persists for manual inspection (`parquet-tools`, IDE viewers)
- Serves as input for future bar renderer tests
- Contains both broker types: `kraken_spot` (BTCUSD, ETHUSD) and `mt5` (EURUSD, GBPUSD)

### Design Notes

- All test data uses **real broker_types** (`kraken_spot`, `mt5`) because `MarketConfigManager` validates against `configs/market_config.json`
- Unit tests use `tmp_path` for full isolation — persistent output is additional, not a replacement
- `build_minimal_tick_json()` generates realistic tick data with configurable parameters

---

## Test Files

### test_json_schema_validation.py

Validates that the importer correctly accepts valid JSON and rejects invalid input.

**TestValidJsonAccepted:**
- Minimal valid JSON with required fields processes successfully
- Legacy `data_collector` field accepted as broker_type alias

**TestInvalidJsonRejected:**
- Missing `metadata` key raises ValueError
- Missing `ticks` key raises ValueError
- Empty ticks array is skipped (no crash, 0 processed)
- Missing `broker_type` (and no `data_collector`) raises ValueError
- Unknown broker_type not in market_config raises ValueError

**TestSchemaTypeDefinitions:**
- All TypedDict classes importable from `import_schema_types`
- Mandatory field lists contain expected entries

---

### test_conversion_pipeline.py

Validates end-to-end conversion from JSON to Parquet.

- Basic conversion creates a Parquet file
- Output contains expected columns (timestamp, bid, ask, last, etc.)
- Tick count in Parquet matches JSON input
- Directory structure follows `{broker_type}/ticks/{SYMBOL}/` pattern
- Numeric columns use optimized dtypes (float32, int32)
- Timestamps parsed as datetime64 with UTC timezone

---

### test_offset_application.py

Validates the conversion of a venue's own timestamps to UTC through the broker's server clock rule,
and the session labels that follow from it. The MT5 server runs on New York time + 7 h, so the one
rule takes two hours off a winter tick and three off a summer tick.

**TestTheRuleNotANumber:**
- A winter MT5 tick (15:00 on the server) becomes 13:00 UTC; a summer one becomes 12:00
- The payroll anchor: 2026-01-09 15:30 on the server is 13:30 UTC — the archive held it at 12:30
  while a fixed offset stood here
- Kraken (`UTC+0h`) stays as it is
- `time_msc` moves by the same per-tick amount as `timestamp`

**TestTheChangedHour:**
- A tick in the hour a daylight saving change repeats refuses the file — no parquet is written

**TestTheHeaderSaysWhatWasApplied:**
- A winter file states `user_time_offset_hours` `-2` and `server_clock_rule` `America/New_York+7h`;
  a summer file states `-3`

**TestSessionRecalculation:**
- Server midnight in summer is 21:00 UTC — the transition hour; in winter it is 22:00 — already the
  Asian session
- A UTC clock keeps the collector's label

---

### test_parquet_metadata.py

Validates Parquet file header metadata.

**TestCoreMetadata:**
- `source_file` matches input filename
- `symbol` matches input
- `broker_type` matches input
- `importer_version` matches TickDataImporter.VERSION
- `tick_count` matches actual row count
- `utc_conversion_applied` flag correct for a server clock that is or is not UTC
- `user_time_offset_hours` is the offset the rule resolved to (`-2` on a winter date), beside `server_clock_rule`

**TestSourceMetadata:**
- `source_meta_` flat fields present (e.g., `source_meta_broker_type`)
- `source_meta_symbol_info` is valid JSON string
- Parsed nested metadata has correct content
- Already-captured keys (`symbol`, `broker`) not duplicated as `source_meta_`
- A converter's `external_source` block lands as JSON, not as a Python repr (#561)

---

### test_duplicate_detection.py

Validates duplicate detection by source file NAME, answered from the tick index, and the refusal of
a file that overlaps the archive.

- First import succeeds (no duplicate)
- Second import of same file detected as duplicate (0 processed)
- Override mode allows re-import of duplicate
- Different source file not flagged as duplicate

**TestTheArchiveIsCheckedBeforeTheWrite** (#561):
- A file whose ticks fall inside an archived file's span is refused and nothing is written
- A refused `--override` keeps the parquet it would have replaced — the check runs before the delete
- Touching edges are not an overlap
- A batch is checked against itself: of two overlapping files in one inbox, the second is refused

**DUPLICATE AT LAST Policy:** The `populate_persistent_test_output` session fixture imports 4
reference files into `data/test/import/processed/` at session start. All duplicate detection tests
use `tmp_path` for full isolation, so they don't conflict with the persistent import. However, the
persistent import must complete first — if duplicate detection tests ever run against shared
directories, they must be ordered last (after the reference data is established and directories are
clean).

---

### test_bar_render_admission.py

Render workers start by memory (#442):

- **TestTheEstimate** — the measured symbol is estimated at its measured peak; an empty symbol
  costs the process alone
- **TestTheAdmission** — two large symbols never run side by side; a freed worker takes the largest
  that fits; a symbol larger than the budget renders alone; the worker count still caps

### test_kraken_history_converter.py

Kraken's published trade history through the converter and the real import (#561). Every test
builds its own small archive — a zip with Kraken's manifest, cut into byte-range parts with a
checksum list — so nothing binary is committed.

- **TestTheArchiveIsCheckedBeforeItIsRead** — verified parts are recorded with their checksums; a
  part differing from its checksum, a manifest of another schema, and parts nobody verified are
  refused
- **TestAPairIsReadExactly** — BTCUSD is the member `XBTUSD.csv` and never `XBTUSDC.csv`; a header
  in another column order and a pair the archive does not hold are refused
- **TestEachTradeBecomesOneTick** — Kraken's first BTCUSD trade maps field by field, with no
  `collected_msc`; the file names its converter identity and its parts; the same parts give the
  same ticks
- **TestWhereConversionStopsAndSplits** — conversion stops before our first collected tick; one
  file per month, never more ticks than the cap
- **TestTheImportStampsItExternal** — a converted file imports as `external`, stamped, with the
  deficiencies `no_quote` and `no_arrival_time` in the index
- **TestTheArchiveStepProvesBeforeItRemoves** — an imported conversion is zipped intact and its
  loose copies removed, the record marked; a conversion not yet imported is refused, nothing is
  zipped and nothing removed

---

### test_data_deficiency.py

What a tick file LACKS, measured from its rows when the tick index scans it (#561).

- **TestTheVocabularyIsDeclared** — every `DataDeficiency` has its declaration, and every declared
  check id is in the validation check catalog
- **TestWhatAFileLacksIsMeasured** — a recorded, quoted file lacks nothing; bid = ask throughout is
  `no_quote`; an absent or zeroed arrival column is `no_arrival_time`; both reconstruction
  fingerprints (every multi-tick second ending on .999, the arrival copied from the event time) are
  `arrival_time_reconstructed`
- **TestMissingTradesAreCountedAlways** — a few missing trade ids are a count, not a deficiency; a
  run of missing ids spanning more than five minutes is one; no trade ids means "not measurable"
- **TestTheIndexCarriesThem** — a file imported without a quote and without an arrival time is
  indexed with both deficiencies, through a persisted index

---

### test_collected_msc.py

Tests for the `collected_msc` field (V1.3.0), `time_msc` offset consistency, tick order preservation, and `server_time` removal.

**TestCollectedMscPresence:**
- `collected_msc` column present in Parquet output
- `collected_msc` dtype is int64

**TestCollectedMscBackwardCompat:**
- JSON without `collected_msc` imports WITHOUT the column (no zero is written); the reader adds the
  tick loop's zero when it loads the file

**TestCollectedMscValues:**
- V1.3.0 data preserves `collected_msc` values through import
- `collected_msc` not affected by the server clock conversion (stays unchanged)

**TestTimeMscOffset:**
- `time_msc` shifted by the same server clock offset as `timestamp` (−2 h on a winter date)
- `time_msc` unchanged for a UTC clock
- `timestamp` and `time_msc` consistent (same UTC moment) after the conversion

**TestTickOrderPreservation:**
- Parquet row order matches JSON array order — a burst of three ticks sharing one `time_msc`, distinguishable only by their arrival stamp, with bid values as position markers. Their JSON order *is* the arrival order, so any re-sort would lose it
- `collected_msc` stays non-decreasing through the import

**TestServerTimeRemoved:**
- New imports do not contain `server_time` column

---

### test_quality_checks.py

Validates rejection behaviour and file management.

**TestImportRejection:**
- Ticks with bid <= 0 reject the file — nothing written, error recorded, batch survives
- A wide spread is a market condition, not a defect — the file still imports
- No temporary helper columns leak into the final Parquet

**TestMoveProcessedFiles:**
- With `move_processed_files=True`, JSON moved to finished directory
- With `move_processed_files=False`, JSON remains in source directory

---

### test_import_validation.py

Unit tests for `TickImportValidator` — one case per rejection reason plus the cross-file plane. The
constants it enforces are measured values; each test names the measurement it rests on.

**TestHealthyFile:**
- A file satisfying every invariant passes untouched
- Ticks sharing an arrival millisecond are allowed (Kraken bursts several per ms)

**TestRejectionReasons:**
- Backwards `collected_msc` step
- Timezone-offset `collected_msc` (class A) — message names the migration
- A late arrival in a file declaring `collected_msc_timebase: "utc"` — message names a collector defect instead
- A whole hour between arrival and event in such a file — message names a clock conversion, not the
  collector: two clocks a whole hour apart, the shape every US-winter MT5 file took once its event
  time was converted right while its arrival time still followed the old fixed offset
- `2^64`-scale anchor overflow (class C) — split into two segments
- Row count disagreeing with `summary.total_ticks`
- Inverted spread (`ask < bid`)
- `timestamp` and `time_msc` describing different moments
- Empty tick array

**TestTolerances:**
- Lag exactly at the ±30 s window edge is accepted, one millisecond past it is not
- A weekend-sized gap (48 h) is not a segment break
- A gap past the 7-day threshold is

**TestMissingColumns:**
- Missing or all-zero `collected_msc` never rejects; an all-zero column warns that the file records
  no arrival time

**TestWeekendOpen:**
- A regular winter open (Sunday 17:01 New York = 22:01 UTC) passes; the same in summer at 21:01 UTC
- An open one hour early (21:01 UTC on a winter Sunday) refuses the file, naming `-1 h` and the
  server clock
- A late open that is no whole hour (2 h 31 min) only warns — a holiday or a feed gap
- A market without weekends is not checked; a gap far longer than a weekend is not read as one

**TestArchiveOrdering:**
- An ordered archive produces no findings
- Overlapping coverage of one symbol is reported
- Files touching at exactly 0 ms distance are not an overlap (227 such pairs exist in the archive)

---

### test_tick_index_persistence.py

Validates that `data_format_version` survives the tick index write/read cycle — the index is the only path by which the version reaches a run report.

**TestVersionRoundTrip:**
- Persisted index file carries the `data_format_version` column
- A manager loading the index sees the real version, not `'unknown'`
- The empty-schema branch declares the same columns as a populated index

**TestLegacyIndexTolerance:**
- An index file without the version column still loads (entries intact) and reads as `'unknown'` — never an exception that silently empties the index

### test_bar_archive_integrity.py

Pins the two halves of bar-archive integrity: an import stays inside the directory it was
given, and a bar file is proven readable at the moment it is written.

**TestScopedImportLeavesProductionAlone:**
- A full import into a temporary directory leaves the production bar index and the production discovery-cache index byte-identical. Skips explicitly when no production archive exists, rather than passing on two absent files
- The scoped import writes its own bar index into the target directory, and that index can be reloaded

**TestWriteTimeVerification:**
- A correctly written file verifies without raising
- A file truncated after writing is refused — only a full read finds this, which is the whole point
- A readable file whose row count disagrees with what was written is refused too

**TestIndexBuildReportsUnreadableFiles:**
- A file the scan cannot read is named in an error-level block, with the balance (`N of M bar files`) and the repair command
- A build in which every file reads produces no error block

**TestThePriceBasisSurvivesIntoTheIndex:**
- The stamped basis reaches the index, survives a reload and is a column of the persisted index
- A file written before the stamp reads as `unknown`, never as today's declaration

**TestTheOriginOfABarFileSurvivesIntoTheIndex** (#561):
- `origin_classes` and the seam `collected_from` survive a reload; a file from before the stamp answers nothing
- The seam is the start of the first tick file not from a venue's published history; one origin alone has no seam
- A warmup whose first bar opens before the seam is external; a file of external history only is external throughout; collected data alone never is

Uses `RecordingLogger` (`tests/shared/recording_logger.py`): the project's loggers do not
route through the stdlib `logging` module, so `caplog` cannot see them, and these cases
assert what was REPORTED rather than what was returned.


### test_import_coordinator.py

The one import over both import inboxes (`ImportCoordinator`, #175), run against a temporary
tree:

- **TestBothArchives** — ticks and signals are imported, moved and rendered; every step ends
  done, except the caches, which a scoped import shows skipped as `not the production archive`;
  the import's own log holds both importers' lines, the summary and the line naming itself; the
  frame serializes to JSON; the scan names the broker from each file's metadata head
- **TestPinnedIssue** — a duplicate is counted and pinned as the bottom line; an `--override` run,
  which logs a warning per replaced file but counts none, pins nothing
- **TestOnlyOneArchive** — `--only ticks` and `--only signals` leave the other import inbox
  untouched
- **TestEmptyInboxes** — with nothing to import every step after the scan is shown skipped
- **TestInterruptedRender** — a stop at the third symbol leaves its previous bars untouched,
  removes nothing and leaves no `.partial`; the bar index is REBUILT — a symbol new in this import
  and rendered before the stop is in it; the stop is a counted warning, pinned and in the
  summary's warning list; the outcome maps to exit code `130`
- **TestFailedSteps** — a bar index that cannot be rebuilt, a signal index that cannot be rebuilt,
  and a bar, tick or signal file an index rebuild cannot read each end their step failed, count
  an error and store no rate; a tick index missing an unreadable file still serves the render;
  the caches are not rebuilt over a failed bar index; the pinned error is the newest one even when
  it lands in a list the summary prints first
- **TestWithTheScreen** — the default path from a terminal: the screen starts and stops, the lines
  logged while it runs reach the import's own log but not the console, and the summary reaches
  the console after it
- **TestStops** — a code crash is raised after the summary, stores no rates and restores the
  previous SIGTERM handler; a SIGTERM ends the import as interrupted, the running step says so
- **TestSigtermBlock** — the block the import and a standalone render run under turns SIGTERM into
  a stop and gives the previous handler back
- **TestTimings** — the step rates are stored after a complete production import only, never
  after a scoped or interrupted one; the next import's summary holds each step's estimate against
  what it took
- **TestExitCodes** — every outcome has an exit code, and an import with errors is not reported
  as success
- `test_bar_files_carry_the_render_after_a_complete_import` — a complete import leaves only
  finished bar files, each readable in full

### test_bar_render_replace.py

- **TestReplaceAndClean** — a second render replaces every file and leaves no `.partial`; a new
  file that fails its verification never replaces the good one, which stays byte-identical; a
  leftover `.partial` of a timeframe the render does not write is removed at the start; files
  without a source (a symbol without ticks, a timeframe not configured) are removed after a
  complete render, never after an interrupted one; bars whose tick files are on disk are kept,
  with a warning, when the tick index does not list their symbol
- **TestBarIndexUpdate** — an unreadable bar file makes the index rebuild report failure and
  count one error that names the repair
- **TestWorkerLifetime** — a parent that starts a pool with the render's initializer and is then
  killed outright takes its workers with it (Linux; the test runs that parent as a process of its
  own and ends any worker left behind)
- **TestParallelRender** — two workers report every phase of their symbols with their memory, and
  each finished symbol is counted
- **TestCrashDiagnosis** — an abruptly ended worker is named as a likely out-of-memory end, with
  the setting to lower and its current value (#442)

### test_import_progress_tracker.py

The state behind the import screen: a stored rate times this import's quantity is a step's
estimate (only in the same unit, and for the render only at the same worker count); one step
without an estimate leaves the total unknown; a running step below 5 % of its work counts down its
estimate and past it extrapolates its own progress, pinned with exact numbers on a clock the test
moves by hand, and the step carries the same figure the header sums; a pending step carries its
estimate, a finished one none; the frame carries the whole import's estimated duration and end
time while it runs; the comparison holds each step's estimate at the start against its duration,
and its total sums only the steps that ran and had an estimate, saying so when that is not every
planned step; only finished steps yield a rate to store; the newest counted issue is pinned by its
first line, without the emoji variation selector; worker lines follow their events, leave with
their symbol and keep their number across symbols; a full queue keeps the newest frame; a frame
shares nothing with the tracker; the refresh keeps publishing while nothing reports — the clock
moves and a report the throttle dropped still reaches the screen — and stops when told, and
without a queue it never starts.

### test_import_live_display.py

The screen rendered into a recording console — no live terminal: at 80 and at 160 columns every
step, the render workers, the memory bar, the counters with the last warning, and the remaining
time with the total and the end time are shown; each step's time is the frame's own figure, never
one the screen derives; a finished import names its outcome and its log file and shows no estimate
any more; every step status and every outcome renders (a missing icon would end the display
thread); at 64 columns every status icon stays visible; a note cut for width ends in `…` rather
than in a smaller number; sizes use one MB. A frame the screen cannot draw is reported once to the
log files and the screen goes on — the next frame still appears (#586); before, the display
thread ended unseen, the console being muted while the import runs.

### test_import_config_display.py

The screen's settings and the timings path are declared in the model or manager and in
`configs/import_config.json`, with the same values.

---

## Architecture

- Tests are **fully isolated** — each test creates temporary directories, no shared state
- The importer tests use `TickDataImporter` directly (not via CLI) for precise control; the
  coordinator and render tests build `ImportCoordinator` and `BarImporter` against a temporary tree
- `auto_render_bars=False` in most tests to skip bar rendering overhead — `test_bar_archive_integrity.py` deliberately sets it `True`, because the scope of the bar render is what it examines
- Synthetic data covers both `kraken_spot` (server clock `UTC+0h`) and `mt5` (`America/New_York+7h`) broker types; the default fixture date, 2026-01-15, lies in US winter
- No production data required — all test data generated by `build_minimal_tick_json()`
- **DUPLICATE AT LAST** — persistent session fixture imports first; duplicate detection tests use `tmp_path` isolation but must conceptually run after reference data is established


## `test_origin_stamp.py` — the provenance chain through the importer

A producer's stated identity enters the importer, is resolved ONCE against this side's
registry, and is stamped into the parquet and carried into the index. Contract:
[`data_provenance.md`](../../architecture/data_provenance.md).

| Test | Description |
|------|-------------|
| `test_a_registered_identity_lands_as_production_and_stamped` | registry hit → class plus evidence `stamped` |
| `test_the_identity_itself_travels_verbatim` | the block survives as `source_meta_origin` — a class can be re-derived later only if its premise did |
| `test_an_unregistered_identity_lands_as_unknown` | a stated identity nobody registered is a gap in that file, not a legacy file |
| `test_a_legacy_file_under_a_claim_is_attested` | no identity, covered by an attestation → `attested`, never `stamped` |
| `test_a_legacy_file_with_no_claim_is_unknown` | the honest default |
| `test_the_index_reports_the_stamp_rather_than_re_resolving` | the registry is edited AFTER the import; the index still reports what the file was imported under |
| `test_an_index_entry_survives_the_parquet_round_trip` | the step where a column added to the entry but not to the persist list disappears silently |

**`test_tick_index_persistence.py` changed with it.** An index missing a column used to be read
tolerantly, with the absent column answering `unknown` — the best available answer while nothing
could tell a stale index from a current one. The index now carries a schema stamp, so it is
rebuilt instead and the column comes back with its real value. The tolerant defaults in the load
path stay where they are; they guard a half-written index, which is a different failure.

---

## `test_origin_host_move.py` — one identity, two producing hosts

An identity belongs to a data root rather than to a machine, so a move is legitimate — which is
precisely why it has to be visible. A **restore** continues one series on new hardware; a **copy**
makes two writers assert one identity, and that is the failure the provenance contract exists to
prevent. Nothing in the data separates them, so the suite pins a REPORT rather than a verdict.

| Test | What it holds |
|---|---|
| `test_it_reaches_the_index_from_the_block` | the host reaches the index, and the producer's block is read rather than replaced |
| `test_a_file_without_a_host_reads_empty_rather_than_raising` | `''` is the honest reading, and it saves every caller a missing-key branch |
| `test_two_hosts_under_one_identity_are_named` | the case the derivation exists for |
| `test_one_identity_on_one_host_is_silent` | silence is the normal answer |
| `test_two_identities_on_two_hosts_are_silent` | two producers on two machines is ordinary; only ONE identity on several hosts carries the question |
| `test_a_missing_host_is_not_a_second_host` | the legacy archive carries no host, and counting `''` would report every pre-field identity as having moved |

The derivation lives at the INDEX rather than at the import. The index sees every file for an
identity at once, where an importer mid-batch compares against a stale picture and misses a change
that arrived inside its own run — the reason is the same one that puts the origin RESOLUTION at
import and the origin COMPARISON here.

## The refusal, in `test_origin_stamp.py`

From format 1.7.0 a file naming no producer is refused. The proof is the **absence of a parquet**
rather than an exception: a data error is a scenario-level failure and not a crash, so what
matters downstream is that the file does not enter the archive.

Two controls sit beside it and are what make the boundary readable. The same file WITH an identity
imports, so the refusal is about the missing block and not about the version. And a 1.6.0 file is
NOT refused and NOT attested — it resolves to `unknown`, which is the fail-closed direction the
boundary was chosen for: 1.6.0 never reaches production, but a development file at that version
exists and must not be swept into a claim written about the legacy archive.
