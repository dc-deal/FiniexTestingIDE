# Discovery Cache Validity Tests

`tests/framework/discovery_validity/` — the config-fingerprint comparison across the bar-derived
discovery cache families (#486 finding 57), and the rule that every family's cache write reaches
the discovery index (#602).

Run: `python -m pytest tests/framework/discovery_validity/ -v`

Architecture: [Data Storage Layout](../../architecture/data_storage_layout.md) ·
[Discovery System](../../discovery_system.md)

---

## What this suite exists to catch

All three families — extreme moves, data coverage, volatility profile — **wrote** a
`config_fingerprint` into every cache file and **none of them read it back**. Validity rested on
the source bar file's mtime alone, and a config change moves no bar file. A cache built under
different parameters therefore read as valid indefinitely.

The concrete case: `data_coverage.thresholds.short` decides every gap's `GapCategory`, and the
category decides which scenarios `ScenarioDataValidator` excludes. Change the threshold, and
yesterday's categories keep being served to every backtest.

This is the same trap already recorded for these caches — *a corrected classification had
no visible effect until the caches were rebuilt by hand* — one layer deeper: not the code that
produced the content, but the configuration it was produced under.

## Coverage

| Test | Asserts |
|---|---|
| `test_a_matching_fingerprint_is_valid_and_a_changed_one_is_not` | Parameterized over all **three** families: a cache built under the current config stays valid; a changed fingerprint invalidates it while the source file has not moved |
| `test_a_cache_without_a_fingerprint_is_not_valid` | A file written before the fingerprint existed cannot vouch for itself and is rebuilt |
| `test_a_cache_of_another_method_is_not_valid` | A volatility cache without `method_version`, or with another one, is rebuilt although its fingerprint matches (#647) |
| `test_the_fingerprint_a_generated_profile_checks_is_the_config_s_alone` | The method version stays out of the fingerprint a generated profile records, so a new method does not call the committed profiles stale |
| `test_it_returns_every_key_decoded` | `read_cache_metadata` yields every key from ONE file open |
| `test_a_missing_file_is_none_rather_than_an_error` | An absent cache is `None`, never an exception |

## Why the metadata reader matters

Each validity check needs several metadata keys at once. Opening the parquet footer once per key
doubles the check: measured over the 16 real `extreme_moves` entries, the footer reads cost ~65 ms
whichever API is used, and a second read of the same files adds the same again. `read_cache_metadata`
opens once and returns the decoded dict, so the comparison adds **one config load per instance
(~9 ms), and zero file operations per entry** — it is genuinely free, not merely cheap.

The current fingerprint is memoized per instance for the same reason: `DiscoveriesConfigLoader` reads
its JSON twice per construction, and a validity check runs once per cached entry.

## Every cache write reaches the index

A family that writes its cache on demand — a report computed because its cache was invalid — used
to leave `discovery_caches_index.parquet` describing the generation before it, and
`store_cli.py catalog` reported a stale index nobody had asked to rebuild. Every family now writes
through `DiscoveryCacheIndex.write_cache_file`, which writes the file beside its place, moves it
there and enters it in the index in the same step.

| Test | Asserts |
|---|---|
| `test_the_file_is_written_and_the_index_stays_current` | One write leaves the file on disk, its row in the index, and the index current |
| `test_a_rewritten_file_keeps_one_row_and_leaves_no_temporary_file` | A second write of the same file replaces its row, and nothing but the file is left in the folder |
| `test_no_module_writes_a_parquet_file_past_it` | No module under `python/framework/discoveries/` other than the index unit calls `write_table` or `to_parquet` — a family writing past the index would bring the stale index back with the suite green |

## The volatility profile measures each period against its trailing year

`test_volatility_profile_ranks.py` (#647). Each period's `atr` is its ATR over close divided by the
mean of the periods of the 365 days up to and including it, and its `atr_percentile` the share of
those periods strictly below it. One rolling pass over the period starts computes both; a
comparison against every period of the year, per period, would grow with the history times the
year. A one-hour period takes its session from its hour, which every bar in it shares. The frames
are synthetic.

| Test | Asserts |
|---|---|
| `test_each_rank_is_the_share_of_its_year_strictly_below` | Ties share a rank, each period is ranked among the periods up to and including it |
| `test_ranks_and_means_match_a_plain_count_over_each_year` | On irregular starts across three years with tied values, the ranks equal a plain count over each year exactly and the means agree |
| `test_a_year_forgets_what_lies_before_it` | A reference a year on holds none of the periods before that year |
| `test_no_periods_give_no_reference` | No periods, no reference — never an error |
| `test_every_period_carries_the_ratio_and_rank_of_its_own_atr_over_close` | A built profile's ratios and ranks equal a count by hand over each hour's mean ATR over close |
| `test_a_one_hour_period_takes_the_session_of_its_hour` | Every one-hour period's session is the session of its UTC hour |
| `test_a_price_that_grows_a_hundredfold_at_steady_volatility_reads_as_ordinary` | Over 400 days of a price growing a hundredfold at a steady 2 % range every period reads `medium` — against the whole history's absolute ATR its first months read very low and its last very high |
