# Raw Archive Re-import Tests

A re-import can leave files untouched without any count noticing. The importer refuses a file
it judges defective *before* it deletes that file's old parquet, so the old parquet and its
index row stay where they were — and the archive and the tick index still agree on how many
files there are and what they are called. This suite holds the re-import tool to the checks
that can see that case (inbox, rewritten), to its selection rule, and to the rules that keep it
from deleting anything that is not provably a copy.

What the tool is for, its phases and why it lives under `python/experiments/` are in the module
docstring of `python/experiments/raw_archive_reimport/raw_archive_reimport.py`; this document
covers only what the tests pin. What the importer itself accepts or refuses belongs to
[Import Pipeline Tests](import_pipeline_tests.md).

**Location:** `tests/data/raw_archive_reimport/`

---

## Running the Tests

```bash
pytest tests/data/raw_archive_reimport/ -v
```

No data files, no network, no configuration: the configured broker types are injected, and
every path is a temp directory.

---

## Fixtures

Every zip, raw JSON member, parquet and tick index frame is written by the test that needs it,
into its own `tmp_path`. Nothing binary is committed.

| Name (`conftest.py`) | What |
|---|---|
| `tree` → `ArchiveTree` | An archives directory, a finished directory, an inbox and a processed tree under one temp root, plus the tick index frame describing the parquets (times naive, as the real index stores them) |
| `member_json()` | A raw tick file shaped like the collector's: the metadata block first, then the ticks |
| `build_standard_archive()` | Two zips, one named after a `kraken_spot` member while most of what it holds is `mt5`; every member indexed the way a previous import left it |
| `ArchiveTree.simulate_import()` | Stands in for `data_index_cli.py import --override`: rewrites each inbox file's parquet and index row and moves the file to the finished directory — or refuses a file (it stays in the inbox, its old parquet stays), or moves it without rewriting its parquet |

The import is simulated rather than run on purpose: the subject is the tool, and running the
real importer would make every case here depend on what the importer currently refuses.

---

## Test Classes

### `test_raw_archive_catalog.py` — what the archive holds and what a scope selects

#### TestSelectionByMemberName

| Test | What |
|------|------|
| `test_zip_named_after_a_kraken_member_still_yields_its_mt5_members` | The `XRPUSD_…` batch zip holds `mt5` files; they are selected for `mt5`, its namesake is not |
| `test_plan_flags_a_zip_named_after_another_brokers_member` | The plan's zip table warns on exactly that zip |
| `test_plan_reads_only` | Plan writes nothing anywhere in the tree |
| `test_symbols_match_exactly` | `EURUSD` selects nothing that merely starts with it |
| `test_entries_that_are_not_tick_files_are_ignored_and_counted` | Directory entries and other names are counted, never selected; a nested entry is selected by its file name |
| `test_signal_archives_and_subdirectories_are_never_read` | Only zips directly under the archives directory, and never a signal zip |

#### TestBrokerResolution

| Test | What |
|------|------|
| `test_the_index_row_decides_over_the_header` | A member the index files under `mt5` is `mt5`, whatever its header claims |
| `test_the_header_decides_when_the_index_does_not_know_the_member` | A forex-looking symbol whose header says `kraken_spot` is `kraken_spot` — never the symbol's shape |
| `test_the_older_data_collector_key_resolves` | A header without `broker_type` is read by `data_collector`, like the importer reads it |
| `test_broker_type_wins_over_data_collector` | Where both keys are present, `broker_type` is the one read |
| `test_an_unknown_or_missing_broker_is_unresolved_and_never_selected` | Matching is exact: `MT5` is not a configured broker type |
| `test_an_unresolved_member_the_scope_could_cover_blocks_plan_and_extract` | Plan exits 1, extract writes nothing |
| `test_an_unresolved_member_outside_the_scope_does_not_block` | An unresolved `GBPUSD` file does not concern a scope of `EURUSD` alone |
| `test_an_unknown_scope_broker_blocks` | A mistyped `--broker` is named rather than selecting nothing |

#### TestDuplicateNames

| Test | What |
|------|------|
| `test_a_name_in_two_zips_blocks_plan_and_extract` | Plan names both zips and exits 1; extract writes nothing |
| `test_a_name_twice_in_one_zip_is_a_duplicate` | Two entries of one name inside one zip count like two zips |

#### TestLooseFiles

| Test | What |
|------|------|
| `test_a_loose_file_in_no_zip_is_a_source` | It belongs to the archive like a zipped member, and is selectable |
| `test_a_loose_file_whose_name_a_zip_holds_is_a_copy` | It is not counted a second time |

#### TestWindow

| Test | What |
|------|------|
| `test_the_index_row_overlap_decides` | A file named after a day outside the window whose ticks begin inside it is selected, and the reverse is not |
| `test_a_member_the_index_does_not_know_falls_back_to_its_name` | The stamp in the name decides; the plan says how many went which way |
| `test_the_standard_winter_selects_the_winter_mt5_files` | The scope #562 re-imports, over the standard archive |

#### TestArguments

| Test | What |
|------|------|
| `test_a_bare_end_date_covers_that_whole_day` | `2025-11-02..2026-03-08` includes all of 8 March |
| `test_an_explicit_time_is_taken_as_written` | A time is UTC and is not stretched to the end of its day |
| `test_either_side_may_be_open` | `2025-11-02..` and `..2026-03-08` |
| `test_an_unreadable_window_is_refused` | No separator, no side, a reversed window, or words |
| `test_symbols_are_read_without_blanks_and_repeats` | `--symbols` keeps order, drops blanks and repeats |

### `test_raw_archive_extract.py` — the phase that writes

#### TestExtract

| Test | What |
|------|------|
| `test_streams_the_selected_members_and_writes_the_manifest` | Byte-identical files in the inbox, no `.part` left, manifest with scope, members, the index rows before, and a UTC extraction time per member |
| `test_the_archive_is_left_as_it_was` | No zip changes by a byte |
| `test_the_manifest_reads_back_as_it_was_written` | Origin, broker source and size survive the round trip |
| `test_a_loose_source_is_moved_never_copied` | The only copy leaves the finished directory for the inbox |
| `test_symbols_batch_the_extraction` | A scope of one symbol extracts that symbol alone |
| `test_a_member_failing_its_checksum_never_reaches_its_final_name` | A damaged member raises at the end of its stream; the partial file is removed and the manifest does not count it as extracted |

#### TestExtractRefuses

Each refusal leaves the inbox exactly as it was.

| Test | What |
|------|------|
| `test_an_inbox_already_holding_a_tick_file` | A leftover could not be told apart from an extracted file |
| `test_a_partial_extraction_left_in_the_inbox` | A `.part` is an interrupted extraction |
| `test_while_a_manifest_exists` | A re-import is already in progress |
| `test_when_the_disk_is_too_small` | Free disk must cover the selected bytes with headroom |
| `test_when_a_selected_member_already_lies_loose` | The import would replace that loose file, and it may hold what the zip does not |
| `test_when_nothing_is_selected` | An empty selection is not a re-import |

### `test_raw_archive_verify_clean.py` — the proof, then the cleanup

#### TestVerify

| Test | What |
|------|------|
| `test_passes_after_the_import_and_reports_the_shift` | Every check green, in its order; the summary reads `3 files shifted by +1 h · 0 unchanged · 0 tick counts changed`, with a before → after line per changed file |
| `test_count_and_names_cover_the_broker_and_symbols_not_the_window` | A winter extraction is still counted against every `mt5` file |
| `test_a_refused_file_fails_inbox_and_rewritten_while_count_and_names_pass` | The case count and names cannot see |
| `test_an_import_without_override_leaves_every_file_in_the_inbox` | Every selected name is an inbox offender |
| `test_a_stale_parquet_fails_rewritten_while_count_names_and_inbox_pass` | The file left the inbox, its parquet is older than its extraction |
| `test_names_are_compared_in_both_directions` | One file only the archive holds and one only the index names: the counts agree, the names do not |
| `test_a_missing_parquet_fails_rewritten` | An index row pointing at a deleted parquet proves nothing |
| `test_without_a_manifest_verify_fails` | Nothing extracted is not a pass |

#### TestClean

| Test | What |
|------|------|
| `test_refuses_while_verify_is_red` | Nothing deleted, the manifest stays |
| `test_deletes_identical_copies_keeps_a_differing_one_and_never_a_source` | A SHA-256-equal copy goes; a copy a migration changed stays and is listed as awaiting archiving; a loose file no zip holds stays; the zips are untouched |
| `test_a_second_clean_has_nothing_to_do` | Without a manifest clean refuses rather than guessing |
