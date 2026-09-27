# Config Directory Tests

`tests/framework/config_directory/test_config_directory.py` — the directory of every configuration
file that can start a run (#554): `python/framework/config_directory/`.
`test_config_directory_views.py` beside it covers the console views over the same model
(`reporting/console/config_directory_summary.py`). The routes over it are
[API Endpoint Tests](api_endpoint_tests.md#directory-routes-test_directory_endpointpy).

## Why this suite exists

The scenario listing it replaced had four properties a directory must not have, and each is
pinned from the side it failed on: it WROTE the run-config store (a RECORD) on every read, so a
half-finished file became a permanent version; a file that did not parse vanished without a word;
AutoTrader profiles were not in it at all; and it paid a full loader pass per file. Every test
builds its own tree under `tmp_path` and points the directory at it through the same getters the
real one reads (`_Roots`), so nothing here reads the operator's files.

## What Is Tested

### `TestWhatAFileDeclares`

| Test | Description |
|------|-------------|
| `test_a_scenario_set_counts_its_disabled_scenarios_as_declared` | declared 3, enabled 2; symbols, brokers and market types come from the ENABLED scenarios |
| `test_the_strategy_is_the_one_the_cascade_resolves` | a scenario overriding its decision logic shows both — through the loader's own `ScenarioCascade.merge_strategy_config` |
| `test_a_profile_is_one_unit_with_its_live_facts` | bot id, adapter, declared `dry_run`; no adapter means the loader's default `mock`, no `dry_run` means the broker decides; the `configs/` sub-folder is recorded |
| `test_a_broker_the_market_config_does_not_know_is_unknown_not_a_crash` | market type `unknown` |

### `TestAFileBeingEditedIsARowNotAnError`

| Test | Description |
|------|-------------|
| `test_broken_json_is_an_unreadable_row_with_its_line` | `unreadable`, the reason names the line |
| `test_a_marker_with_the_wrong_shape_is_unreadable` | a `scenarios` that is not a list |
| `test_json_that_is_no_configuration_is_not_served` | an analysis result beside a strategy is not a row |

### `TestWhichCopyWins`

| Test | Description |
|------|-------------|
| `test_the_operators_copy_wins_and_names_what_it_shadows` | the resolver's order — `user_configs` over `user_algos` over `configs` — and `shadowed` names the losers |
| `test_the_operators_own_layout_is_not_served` | no sub-folder from a user root |

### `TestOneNameBelongsToOneKind`

| Test | Description |
|------|-------------|
| `test_a_set_and_a_profile_of_one_name_are_one_conflict` | the row is `unreadable`, and its reason names where the other file lives |
| `test_a_copy_of_the_same_kind_is_precedence_not_a_conflict` | a set shadowing a set stays readable |
| `test_the_run_start_is_refused_for_either_kind` | `refuse_config_name_conflict` raises for a set and for a profile, and passes a name nothing else holds |
| `test_both_loaders_ask_before_a_run_starts` | the scenario-set loader and the AutoTrader loader both call the refusal |

### `TestTheCacheReadsOnlyWhatChanged`

| Test | Description |
|------|-------------|
| `test_an_unchanged_file_is_not_read_again` | a second walk parses nothing |
| `test_a_changed_file_is_read_again_and_a_deleted_one_disappears` | only the changed file is parsed |
| `test_a_new_logic_version_reads_everything_again` | the base class's code check turns into a re-read |
| `test_within_the_freshness_window_nothing_is_walked` | served from memory; `refresh` walks at once |
| `test_a_read_writes_nothing_but_its_own_cache` | every file written lies under the cache root |

### `TestTheRunsAreJoinedFromTheRunIndex`

| Test | Description |
|------|-------------|
| `test_each_file_counts_the_runs_of_its_own_pipeline` | matched on `config_snapshot` AND run type — a live run naming a set's file is not a run of that set; a file that never ran counts 0 |
| `test_the_detail_lists_its_scenarios_and_its_runs_newest_first` | scenarios read fresh with the cascade applied, run ids newest first |
| `test_an_unknown_file_has_no_detail` | None, which the route turns into `config_file_not_found` |

### `test_config_directory_views.py`

The views only format, so what they can get wrong is what the operator reads.

| Test | Description |
|------|-------------|
| `TestTheList::test_a_long_file_name_is_never_cut` | the file name is what `show` takes; the column is as wide as the longest name |
| `TestTheList::test_an_unreadable_file_is_listed_with_its_reason` | a broken file is a line in the UNREADABLE block, with its reason |
| `TestTheList::test_the_kind_filter_shows_only_that_kind` | `--kind` shows one kind |
| `TestOneEntry::test_a_profile_declaring_real_orders_says_so` | `dry_run: false` reads `FALSE — real orders` |
| `TestOneEntry::test_an_undeclared_dry_run_is_the_broker_default_not_false` | `null` is decided at session start, never shown as false |
| `TestOneEntry::test_a_shadowing_file_names_the_copy_that_does_not_run` | `shadowed` is spelled out |
| `TestOneEntry::test_an_unreadable_entry_shows_its_reason_and_nothing_it_cannot_know` | no run figures for a file that could not be read |
| `TestValidation::test_an_accepted_set_is_not_presented_as_runnable` | the parameter-name caveat stands even when every set passes |
| `TestValidation::test_a_refusal_shows_the_first_line_of_its_reason` | a refusal is one line, not a trace |
