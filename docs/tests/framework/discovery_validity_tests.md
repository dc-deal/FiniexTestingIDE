# Discovery Cache Validity Tests

`tests/framework/discovery_validity/` — the config-fingerprint comparison across all three
discovery cache families (#486 finding 57).

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

This is the same trap CLAUDE.md already records for these caches — *a corrected classification had
no visible effect until the caches were rebuilt by hand* — one layer deeper: not the code that
produced the content, but the configuration it was produced under.

## Coverage

| Test | Asserts |
|---|---|
| `test_a_matching_fingerprint_is_valid_and_a_changed_one_is_not` | Parameterized over all **three** families: a cache built under the live config stays valid; a changed fingerprint invalidates it while the source file has not moved |
| `test_a_cache_without_a_fingerprint_is_not_valid` | A file written before the fingerprint existed cannot vouch for itself and is rebuilt |
| `test_it_returns_every_key_decoded` | `read_cache_metadata` yields every key from ONE file open |
| `test_a_missing_file_is_none_rather_than_an_error` | An absent cache is `None`, never an exception |

## Why the metadata reader matters

Each validity check needs several metadata keys at once. Opening the parquet footer once per key
doubles the check: measured over the 16 real `extreme_moves` entries, the footer reads cost ~65 ms
whichever API is used, and a second read of the same files adds the same again. `read_cache_metadata`
opens once and returns the decoded dict, so the comparison adds **one config load per instance
(~9 ms), and zero file operations per entry** — it is genuinely free, not merely cheap.

The live fingerprint is memoized per instance for the same reason: `DiscoveriesConfigLoader` reads
its JSON twice per construction, and a validity check runs once per cached entry.
