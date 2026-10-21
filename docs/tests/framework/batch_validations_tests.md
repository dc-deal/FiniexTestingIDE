# Batch Validations Test Suite

## Purpose

Unit tests for batch pipeline validation and configuration components:
`ScenarioValidator` (symbol detection and registration checks),
`BrokerDataPreparator` (`get_valid_broker_scenario_map` filtering),
`MarketConfigManager` (`ConfigMode` parsing),
`BrokerConfigFactory` (symbol integrity validation and the two identity hashes), and
`KrakenConfigFetcher` (runtime cache merge behavior, lazy symbol addition, and the
account's fee tier), and `ScenarioDataValidator`'s **data-origin gate** — which archive
origins a scenario may read, and the warning that measures what arming the gate would
cost before it is armed (see
[`data_provenance.md`](../../architecture/data_provenance.md)).

## What Is Tested

### `test_scenario_validator.py`

#### `TestDetectQuoteCurrency` / `TestDetectBaseCurrency`

Symbol splitting via known-quote-suffix detection — the fallback used before broker
config is available.

| Test | Description |
|------|-------------|
| `test_standard_usd_pair` | EURUSD → quote `USD` |
| `test_standard_jpy_pair` | USDJPY → quote `JPY` |
| `test_standard_eur_pair` | GBPEUR → quote `EUR` |
| `test_crypto_dashusd_7char` | DASHUSD (7 chars) → quote `USD`, base `DASH` — regression for #265 |
| `test_lowercase_symbol` | Input case-insensitive |
| `test_fallback_unknown_suffix` | No known suffix → last 3 / all-but-last-3 chars |

#### `TestValidateScenarioSymbols`

Phase 0 step that checks each scenario's symbol against the authoritative broker config.

| Test | Description |
|------|-------------|
| `test_valid_symbol_no_validation_result` | Symbol in broker config → no `validation_result` appended |
| `test_unknown_symbol_marks_scenario_invalid` | Symbol absent → `ValidationResult(is_valid=False)` appended, error logged |
| `test_broker_not_in_map_skips_scenario` | Broker type not in map → silent skip (upstream error handles it) |

#### `TestValidateSwapModes`

Phase 0 step (#407) rejecting a symbol whose `swap_mode` the swap engine does not model
(`points` / `none` are supported; the rest fail per-scenario — the batch is not aborted).

| Test | Description |
|------|-------------|
| `test_points_mode_passes` | `swap_mode=points` → no `validation_result` appended |
| `test_none_mode_passes` | `swap_mode=none` (swap-free / spot) → passes (books nothing) |
| `test_percentage_mode_marks_invalid` | `swap_mode=percentage` → `ValidationResult(is_valid=False)`, error names the mode |
| `test_unknown_mode_marks_invalid` | `UNKNOWN` (unparseable string mapped by the adapter) → invalid |
| `test_broker_not_in_map_skips_scenario` | Broker type not in map → silent skip |

### `test_scenario_data_validator.py`

#### `TestMissingCoverageRange`

`DataCoverageReport` is constructed with `start_time` / `end_time` as `None` and keeps them when its
analysis finds no files. Dereferencing that `None` raised an `AttributeError` that aborted the whole
batch — a DATA condition escaping as a code crash, which the split between a data error
(the scenario is excluded) and a code crash (the run stops) exists to prevent.

| Test | Description |
|---|---|
| `test_absent_range_is_an_error_not_a_crash` | Report with no range → one error string returned, nothing raised |
| `test_the_error_names_the_broker_and_symbol` | The message names the broker type and symbol, so the operator knows what to import |
| `test_a_half_filled_range_is_refused_too` | Only `end_time` missing → still refused |

#### `TestRangeChecksStillFire`

The guard must not swallow the checks it stands in front of.

| Test | Description |
|---|---|
| `test_scenario_inside_the_range_is_clean` | Period within coverage → no errors |
| `test_start_before_available_data_is_reported` | `start_date` earlier than the first tick → error naming BEFORE |
| `test_end_after_available_data_is_reported` | `end_date` later than the last tick → error naming AFTER |

#### `TestTheGapChecksSearchTheReport` (#599)

The start-date and tick-stretch checks run once per scenario, and a symbol with years of a venue's
history holds hundreds of thousands of gaps: they ask the report by time window now
(`gap_containing`, `gaps_in_window`) instead of scanning every gap. A real report holds the gaps, so
the window queries are the real ones; that they answer what the scans answered is pinned in
`tests/framework/data_coverage/test_gap_timeline.py`.

| Test | Description |
|---|---|
| `test_a_start_inside_a_short_gap_moves_to_its_end` | The start shifts to the gap's end, with a warning and no error |
| `test_a_start_inside_a_large_gap_is_an_error` | An error naming the gap, no warning |
| `test_a_start_on_a_gap_s_edge_is_not_inside_it` | On the edge there is data on one side: no finding, the start stays |
| `test_a_stretch_reports_only_the_forbidden_gaps_wholly_inside_it` | A large gap inside is reported, a short one inside is allowed, one across the stretch's end is not judged |

#### `TestAStartInsideAnAdmittedGap`

The start check and the tick-stretch check answer one question — may this gap be in the window — and
must agree: a data gap the run admits in the middle of a window is admitted at its start too, by
moving the start to the gap's end. A market closure is not a data gap and stays an error.

| Test | Description |
|---|---|
| `test_a_start_inside_an_admitted_moderate_gap_moves_to_its_end` | `moderate` admitted → the start moves to the gap's end; the warning names the category and the minutes |
| `test_a_start_inside_a_moderate_gap_not_admitted_is_an_error` | `moderate` not admitted → an error, the start stays |
| `test_a_start_inside_an_admitted_large_gap_moves_too` | `large` admitted → the same shift |
| `test_a_start_inside_a_market_closure_stays_an_error` | A weekend gap, admitted inside a stretch, is still an error at the start |

A test that drives the validator with a STUB report builds it through
`tests/shared/coverage_report_stub.py`: a bare `MagicMock` would answer the window queries with
another mock, which the validator reads as a gap.

### `test_broker_data_preparator.py`

#### `TestGetValidBrokerScenarioMap`

Validates that the broker map passed to Phase 7 reporting contains only
`(broker_type, symbol)` pairs from valid scenarios — not symbol name alone.

| Test | Description |
|------|-------------|
| `test_same_symbol_different_brokers_only_valid_broker_survives` | DASHUSD on KRAKEN (valid) + MT5 (invalid) → only KRAKEN entry in result. Core regression for the name-only filter bug. |
| `test_all_valid_scenarios_full_map_returned` | All scenarios valid → map returned unchanged |
| `test_all_invalid_scenarios_empty_map_returned` | No valid scenarios → empty dict |

### `test_market_config_manager.py`

#### `TestConfigModeParsing`

Parsing and validation of the `config_mode` field introduced in #252.

| Test | Description |
|------|-------------|
| `test_dynamic_mode_parsed_correctly` | `kraken_spot` with `config_mode=dynamic` → `ConfigMode.DYNAMIC` |
| `test_static_default_when_omitted` | `mt5_forex` without `config_mode` field → `ConfigMode.STATIC` (default) |
| `test_invalid_config_mode_raises` | `config_mode=turbo` → `ValueError` with mode name in message |
| `test_get_config_mode_getter` | `get_config_mode('kraken_spot')` returns `ConfigMode.DYNAMIC` |
| `test_unknown_broker_raises` | `get_config_mode('unknown_broker')` → `ValueError` |

Uses `unittest.mock.patch` on `MarketConfigFileLoader.get_config` — no file I/O.

### `test_broker_config_factory.py`

#### `TestSymbolIntegrityValidation`

`_validate_symbol_integrity()` checks that `base_currency` + `quote_currency` match the symbol key.

| Test | Description |
|------|-------------|
| `test_valid_config_passes` | Correct BTCUSD base/quote → no error |
| `test_wrong_base_currency_raises` | DASHUSD with `base=ETH` → `ValueError` naming the symbol |
| `test_wrong_quote_currency_raises` | BTCUSD with `quote=EUR` → `ValueError` naming the symbol |
| `test_7char_symbol_validates_correctly` | DASHUSD → base `DASH`, quote `USD` passes |
| `test_missing_currency_fields_skipped` | Entry without `base_currency` → no crash |

#### `TestConfigHashComputation`

`_inject_config_hashes()` computes a stable 8-char SHA256 of the symbols block — without each
entry's `_last_fetched`, so a refresh that reads the same values back moves nothing (#608).

| Test | Description |
|------|-------------|
| `test_hash_is_8_chars` | Hash injected into `_config_meta.symbols_hash` is exactly 8 chars |
| `test_hash_stable_for_same_symbols` | Same symbols dict → same hash across calls |
| `test_hash_changes_when_symbol_spec_changes` | `volume_min` change → different hash |
| `test_hash_stable_when_only_meta_changes` | Different `last_fetched` timestamps → same hash |
| `test_a_refresh_that_only_restamps_moves_neither_hash` | Two entries differing only in `_last_fetched` → same `symbols_hash` and `config_hash` |
| `test_the_fetcher_and_the_factory_compute_one_hash` | `compute_symbols_hash()` — the fetcher's call — equals the injected value |

#### `TestTheReproducibilityHashCoversTheFees`

The second hash, and why there are two (#337). `config_hash` covers `symbols` **and**
`fee_structure`, because a fee rate moves realised P&L on every trade — two runs at different
rates are different runs, and under the old single hash their recorded identity was identical.
`symbols_hash` keeps its narrower meaning: whether a cache still describes the same instruments.

| Test | Description |
|------|-------------|
| `test_a_changed_fee_rate_moves_the_config_hash` | Taker 0.40 → 0.80 → different `config_hash` |
| `test_a_changed_fee_rate_leaves_the_SYMBOLS_hash_alone` | The two must not blur into one |
| `test_it_is_stable_when_nothing_that_matters_changed` | A meta-only difference changes neither |
| `test_the_fee_covering_hash_is_the_one_reported` | Both present → `config_hash` wins |
| `test_a_config_written_before_the_split_still_reports_an_identity` | Only `symbols_hash` → falls back |

All tests call static methods directly with in-memory dicts — no file I/O.

### `test_broker_data_preparator.py` — `TestTheBacktestReadsItsFeesFromTheSeed`

The determinism seam (#337). `kraken_spot` runs in `config_mode: dynamic`, so a backtest loads
its broker config from a gitignored, machine-local cache. Fee rates arriving that way make two
runs over identical data disagree with nothing in either run able to say why. Fees therefore come
from the git-tracked seed; symbol specifications keep coming from the cache, because they are
expensive to fetch and really do change at the venue.

What the cache holds is not versioned: a later fetch overwrites it, so two backtests over identical
data can still read different specifications, and their `config_hash` values say only that they
differ. A run therefore freezes the configuration it read — specifications from the cache, fees
from the seed — and names it as `broker_config_id` in its broker section (#547, pinned in
[Batch Core Tests](../simulation/batch_core_tests.md)); a specification read from an entry older
than the cache's refresh interval is also said once per symbol after the run (`broker_spec_stale`,
`TestTheBrokerSpecAgeIsSaidOnce` below).

| Test | Description |
|------|-------------|
| `test_a_cache_claiming_other_rates_does_not_reach_the_backtest` | Cache says 9.99/9.99 → the run uses the seed |
| `test_the_symbols_still_come_from_the_cache` | A `volume_min` only the cache declares survives into the built config |
| `test_a_seed_without_a_fee_structure_refuses_rather_than_falling_back` | No seed fee block → the run stops instead of pricing itself from the cache |

#### `TestTheSpecFetchIsStamped`

The preparation stamps when each scenario's symbol specification was fetched (#608), where the
cache is read; the post-run check counts it.

| Test | Description |
|------|-------------|
| `test_a_dynamic_scenario_carries_its_entry_s_fetch_instant` | The entry's `_last_fetched` reaches `broker_spec_fetched_at` as a UTC instant |

### `test_market_config_manager.py` — `TestATypoInMarketConfigIsRefused`

`market_config.json` had no unknown-key guard, so a misspelled key in the file carrying
`dry_run` and `credentials_file` was silently ignored. The models now forbid extras.

### `test_kraken_config_fetcher.py`

#### `TestMergeWithCache`

`_merge_with_cache()` behavior: no tombstoning of existing symbols, per-symbol `_last_fetched` timestamps.

| Test | Description |
|------|-------------|
| `test_existing_symbol_not_tombstoned` | ETHUSD in cache + DOTUSD fetched → ETHUSD stays `_active: true` |
| `test_fresh_symbol_gets_active_and_last_fetched` | Fresh symbol gets `_active: true` and valid ISO `_last_fetched` field |
| `test_existing_symbol_last_fetched_preserved` | Existing symbol's `_last_fetched` unchanged when a different symbol is fetched |
| `test_no_cache_returns_fresh_symbols_only` | No existing cache → result contains only the fresh symbol |

#### `TestFetchWithCacheSymbolCheck`

`fetch_broker_config_with_cache()` asks the REQUESTED symbol's own `_last_fetched` (#608): every
fetch rewrites the file, so the file's age said nothing about a symbol, and Kraken's ETHUSD minimum
moved behind a file stamp four days old.

| Test | Description |
|------|-------------|
| `test_fresh_cache_with_symbol_skips_api` | Entry younger than 7 days → no API call |
| `test_fresh_cache_missing_symbol_triggers_fetch` | Symbol absent → API called, symbol merged, existing symbols preserved |
| `test_a_young_file_with_an_old_entry_is_refreshed` | The defect: a young file stamp, an 11-day-old entry → refreshed |
| `test_an_entry_without_a_stamp_is_not_known_to_be_fresh` | No `_last_fetched` reads as stale, never as fresh |
| `test_a_forced_refresh_fetches_a_fresh_entry` | `force_refresh` (the sync's `--force`) asks the venue whatever the age |
| `test_a_changed_minimum_warns_and_is_named` | A moved `volume_min` warns once, in plain numbers; a field read for the first time is not a change |
| `test_an_unchanged_refresh_and_a_new_symbol_stay_quiet` | No change, or nothing cached before → no warning |
| `test_a_failed_refresh_falls_back_to_the_entry_and_says_so` | API down, symbol cached → the cache, with a warning naming the symbol |
| `test_a_failed_fetch_of_an_uncached_symbol_is_a_hard_error` | API down, symbol never cached → `ConnectionError` naming it |

#### `TestCostMinimum`

Kraken's `costmin` — the minimum order VALUE — is read as `cost_min` (#608).

| Test | Description |
|------|-------------|
| `test_costmin_is_read_beside_ordermin` | `ordermin` → `volume_min`, `costmin` → `cost_min` |
| `test_a_pair_without_costmin_has_no_value_minimum` | Absent → `0.0` |

#### `TestTheEntryStamp`

Whatever stands in a symbol's `_last_fetched`, the age arithmetic receives a UTC instant or
nothing — a hand-edited stamp would otherwise reach the subtraction from an aware clock and stop
the boot (#608).

| Test | Description |
|------|-------------|
| `test_the_written_form_is_read_as_utc` | `2026-10-18T12:54:22Z` → that instant, UTC |
| `test_a_stamp_without_an_offset_is_read_as_utc` | No offset → read as UTC, never naive |
| `test_an_unreadable_stamp_is_no_stamp` | A number, `None`, empty, not a date → `None`: the entry counts as not known to be fresh |

#### `TestFetchFeeTier`

`fetch_fee_tier()` over `/0/private/TradeVolume` (#337). The trap it pins: the venue keys its
answer by its OWN canonical pair name, so `pair=ETHUSD` comes back under `XETHZUSD` and indexing
by the symbol raises `KeyError`. Measured against the real account 2026-09-08.

| Test | Description |
|------|-------------|
| `test_the_answer_is_read_although_it_is_keyed_by_the_VENUE_s_pair_name` | `pair=ETHUSD` → `fees['XETHZUSD']` is read positionally |
| `test_the_forward_looking_half_is_carried` | Next tier and its volume threshold survive onto `FeeTierInfo` |
| `test_a_transport_failure_is_never_fatal` | A raising transport → `None` + a warning, the declared rates stand |
| `test_an_unusable_answer_is_declined_rather_than_guessed` | A malformed block → `None`, never a half-read rate |

#### `TestTheBalanceRead`

`fetch_account_balance()` as the boot reads it (#598). Kraken lists every asset the account holds
or once held, at zero where it is empty, and leaves out one it never held — measured 2026-10-20.
So an absent key is a zero balance, and the read itself runs under the broker's ladder.

| Test | Description |
|------|-------------|
| `test_a_currency_the_account_never_held_reads_zero` | An answer without `XETH` reads 0.0 for ETH — before, the session refused to start on a fresh account |
| `test_a_kraken_key_wins_over_the_code` | XRP is read under Kraken's own key `XXRP` |
| `test_the_key_map_does_not_grow_with_each_read` | The class-level key list stays as declared after repeated reads |
| `test_a_passing_fault_is_retried_within_the_budget` | Two timeouts, then an answer: read on the third attempt |
| `test_an_exhausted_budget_raises_instead_of_reading_as_no_balance` | Three timeouts → `ConnectionGaveUpError`, never "no balance" |
| `test_the_venue_refusing_the_key_ends_the_read_at_once` | A Kraken error in the answer ends the read after one attempt |

#### `TestTheVenueSayingNotNow`

The same read through the real request path (#598). Kraken reports maintenance inside an HTTP 200
answer, as `EService:Unavailable`, so the venue's own "not now" has to reach the ladder as a passing
fault rather than as its answer.

| Test | Description |
|------|-------------|
| `test_maintenance_is_waited_out_within_the_budget` | One `EService:Unavailable`, then an answer: read on the second request |
| `test_maintenance_beyond_the_budget_is_a_passing_start_failure` | Three of them → `ConnectionGaveUpError`, which the start reads as passing (exit 75) |
| `test_a_refused_key_is_not_waited_out` | `EAPI:Invalid key` ends the read after one request and stays a refusal (exit 2) |

#### `TestTheCapabilityIsByOverride`

| Test | Description |
|------|-------------|
| `test_the_abstract_default_declines` | A fetcher for a venue without tiers (MT5, #209) inherits `None` — no flag, no `isinstance` |
| `test_without_a_cache_every_fetch_asks_the_venue` | No cache kept → `fetch_broker_config_with_cache` asks the venue each time, forced or not |
| `test_without_a_cache_no_change_is_reported` | No cache kept → `get_last_spec_changes()` is empty |

---

## Why This Matters

The `(broker_type, symbol)` pair filter is critical: if the map were filtered by symbol
name alone, an invalid scenario on broker A would keep broker A's entry alive whenever
the same symbol happens to be valid on broker B — causing `BrokerSummary` to render
symbols from invalid scenarios.

`_validate_symbol_integrity()` guards against copy-paste errors in broker config JSON and
schema drift in refreshed runtime cache files — both would silently produce wrong P&L
calculations if base/quote currencies are mismatched against the symbol key.

`config_hash` is the identity of what a run priced with: the batch summary and the AutoTrader
live-display header show its 8 characters, so two runs can be told apart by the specification
they ran against.
It covers the fee structure as well as the symbols, because a rate change is a change to what the
run produces — the narrower `symbols_hash`, which says only whether a cache still describes the
same instruments, reported two differently-priced runs as identical. Eight characters tell two
configurations apart; they cannot give one back. The content itself is frozen per run and named by
`broker_config_id` in the run's broker section (#547).

## Test Approach

All tests use `unittest.mock.MagicMock` — no real broker configs, parquet files, or
subprocess infrastructure required.

`TestGetValidBrokerScenarioMap` sets `_broker_scenario_map` directly on the preparator
after construction (white-box unit test setup — no `prepare()` call needed).

### `test_validation_types.py`

The atomic finding every validator produces. `ValidationFinding` carries its own `severity`,
the `check` that decided it, its `domain` and its `scope`; `ValidationResult` is a typed
collection of them, and `is_valid` / `errors` / `warnings` are **views** over that collection.

| Test | Description |
|------|-------------|
| `test_no_findings_is_valid` | An empty result passes |
| `test_an_advisory_alone_does_not_reject` | A WARNING never excludes a scenario |
| `test_one_error_rejects` | An ERROR does |
| `test_a_mixed_result_reports_both` | One result carries a rejection AND an advisory — severity belongs to the finding, not the container |
| `test_views_follow_a_finding_added_later` | The derived flag cannot drift from the list it summarizes |
| `test_check_and_domain_survive_on_the_finding` | Origin is carried, not reconstructed |
| `test_domain_is_a_closed_set` | `ValidationDomain` is an Enum — a free string would break filtering |
| `test_a_rejected_scenario_stays_excluded` | The execution gate that excludes a rejected scenario reads the derived flag |

### `test_post_run_validator.py`

The batch-global advisories that can only be known AFTER the run — the "lift" that took these
verdicts out of the console renderers, where a report was deciding whether to warn. Every test
builds a `BatchExecutionSummary` and reads the resulting `check` → message map, so it asserts on
the structured finding rather than on printed text.

| Test | Description |
|------|-------------|
| `test_debug_mode` / `test_no_debug_mode_when_not_debug` | A serial/debug batch says its timings are not representative |
| `test_a_strategy_under_no_version_control_is_warned_about` · `test_a_state_git_could_not_read_is_warned_about_as_unknown` · `test_committed_or_merely_dirty_code_is_not_warned_about` · `test_a_run_without_a_captured_identity_is_not_warned_about` | `unversioned_code` (#551): code in no repository — a `user_algos/` package before `git init` — or a state git could not read is a Tier-1 warning with the remedy; committed or merely dirty code is not, and a run that captured no identity claims nothing |
| `test_no_advisory_for_any_declared_version` | A declared data format version is never flagged |
| `test_unknown_version_advisory` / `test_unknown_counted_against_all_files` | A missing version IS flagged, and the count is against all files — the advisory claims only the field's absence, never anything about the data |
| `test_stress_test` | An active stress config is surfaced, so a stressed run cannot read as clean (shared with the AutoTrader session validator) |
| `test_budget_granularity` | A budget below data granularity has no effect and says so |
| `TestTheDeficiencyCount` | What the data LACKS is said once per run and counted, grouped by where the files came from (#561): a file without a quote is counted; complete data says nothing whatever its format; external and collected files are counted apart; `synthetic` is left to the pre-run signal check; external data gets `data_origin_external` and is not counted as unstamped; a warmup from external history is said in the same finding although the ticks are our own, and shares it with external ticks |
| `TestTheBrokerSpecAgeIsSaidOnce` | A symbol specification read from a cache entry older than seven days is said once per symbol, with how many scenarios read it (`broker_spec_stale`, #608); a fresh entry and a static broker say nothing |
| `test_clean_batch_no_warnings` | The zero state — nothing invented |
| `test_coordination_overhead` / `test_no_overhead_when_low` | Coordination cost above half the computation is flagged; below it is not |
| `test_infra_bottleneck` / `test_expected_bottleneck_no_warning` | A non-hot-path operation dominating is flagged; `worker_decision` dominating is normal |
| `TestTheStreamHoldsEverySubmission` | A scenario whose order-event stream holds fewer submissions than its executor counted is named in `order_event_stream_incomplete` (#362) — a record lost on its way back from the subprocess; a complete stream says nothing (shared with the AutoTrader session validator) |

`TestThePerformanceVerdictsLiveHere` covers the parallel-execution verdict (penalty flagged,
saving not, sequential never) — and pins one ABSENCE:

| Test | Description |
|------|-------------|
| `test_no_per_component_slowness_verdict_is_produced` | There is deliberately no fixed-millisecond "slow worker" advisory. It existed, contradicted `_check_budget` in constructible cases, and was removed as misinformation — see [Warnings & Errors — Tier Taxonomy](../../architecture/warnings_errors_tiers.md). The test exists so it is not re-added by reflex |

### `test_market_fit_advisory.py`

The pre-run advisory that a decision logic is running outside its recommended market or
instrument. It used to be a `logger.warning` inside the scenario subprocess — wrong in channel
(a verdict arriving as an unadjudicated log line) and wrong in time (every input is static
config, yet it took a subprocess to learn a scenario was mismatched).

| Class | What it pins |
|-------|-------------|
| `TestTheVerdictIsAFindingNotALogLine` | a mismatch produces a `market_fit` finding in domain `algo`, carrying its scenario as scope — and never sets `is_valid=False` |
| `TestItAnswersWithoutTheRun` | no instantiation, no subprocess; an unresolvable logic is SKIPPED rather than reported, so this gate cannot produce a false advisory over another gate's failure |
| `TestTheCheckItself` | the pure function both pipelines call: no recommendation → no opinion; market and instrument are independent findings; an unknown broker is silent, never fatal |
| `test_the_two_halves_stayed_apart` | the version line (observation) and the market fit (verdict) are separate functions — merging them back would silently re-route the verdict into the log pot |

### `test_scenario_package_index.py`

The data-package lookup contract. Packages are keyed by `SingleScenario.scenario_index`; the
quality validator receives the list FILTERED to still-valid scenarios, so a loop position stops
matching the index as soon as one scenario is excluded.

| Test | Description |
|------|-------------|
| `test_each_scenario_gets_its_own_package_after_an_exclusion` | s0 excluded → s1 must still get pkg1, not None, and s2 must not get s1's data |
| `test_no_exclusion_is_unaffected` | The case where position and index coincide keeps working |
| `test_it_raises_rather_than_skipping_silently` | A hole raises `ScenarioPackageMissingError` — it can no longer be explained by an exclusion |

### `test_validation_check_catalog.py`

The catalog that gives every `check` id a title and a sentence
(`validators/validation_check_catalog.py`, served as `GET /api/v1/validation-checks`) is complete
in both directions. The emitted ids are found by walking the SOURCE — a keyword `check='…'`, a
`…_CHECK` constant, and the helpers that pass an id on (`_add`, `_finding`, `_as`) — so a new check
fails the suite until it is declared, and an entry for a check the code no longer emits fails too.

| Test | Description |
|------|-------------|
| `test_the_walk_found_the_checks` | the walk found a non-trivial number of ids, so the two below are not vacuous |
| `test_every_emitted_check_is_declared` | an id the code emits with no entry is named with its file and line |
| `test_every_declared_check_is_emitted_somewhere` | an entry no finding can carry any more |
| `test_every_id_is_declared_once` | no id twice |
| `test_every_entry_is_a_label_and_one_sentence_for_a_person` | a title and a one-line sentence, no backticks |

## Files

- `tests/framework/batch_validations/test_scenario_validator.py`
- `tests/framework/batch_validations/test_post_run_validator.py` — one of its checks
  (`stress_test`) is **shared** with the AutoTrader session validator via
  `validators/shared_advisory_checks.py`; the AutoTrader half is pinned in
  [Session Validation tests](../autotrader/session_validation_tests.md)
- `tests/framework/batch_validations/test_market_fit_advisory.py`
- `tests/framework/batch_validations/test_validation_types.py`
- `tests/framework/batch_validations/test_scenario_package_index.py`
- `tests/framework/batch_validations/test_scenario_data_validator.py`
- `tests/framework/batch_validations/test_broker_data_preparator.py`
- `tests/framework/batch_validations/test_market_config_manager.py`
- `tests/framework/batch_validations/test_broker_config_factory.py`
- `tests/framework/batch_validations/test_kraken_config_fetcher.py`
- `tests/framework/batch_validations/test_validation_check_catalog.py`

## Running the Tests

```bash
pytest tests/framework/batch_validations/ -v --tb=short
```

VS Code: **"Pytest: Batch Validations (All)"** launch configuration.


### `test_scenario_data_validator.py` — the data-origin gate

Two questions with deliberately different answers. The ERROR excludes one scenario and lets
every other one in the set run; the WARNING reports what a narrower policy WOULD refuse, so
the cost of arming the gate is a number rather than a surprise.

| Test | Description |
|------|-------------|
| `test_an_inadmissible_class_excludes_the_scenario` | a class outside `admitted_origin_classes` is an error naming how many files carry it |
| `test_an_admitted_class_passes` | a widened policy admits it |
| `test_the_open_default_admits_everything` | the state this ships in — every file resolves to `unknown` until a producer stamps an identity, so a strict default would refuse every scenario on day one |
| `test_a_scenario_that_loaded_nothing_is_not_judged` | no loaded files, no finding |

**The measurement is not here.** How far the archive still is from producer-stamped data is a
property of the archive rather than of one scenario, so it is one finding per run in
`PostRunValidator._check_data_origin`, beside the same question for `data_format_version`. A
per-scenario warning would fire on every scenario of every run until a producer stamps an identity,
and a warning that always fires teaches people to skip warnings.
