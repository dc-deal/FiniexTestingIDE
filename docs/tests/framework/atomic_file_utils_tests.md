# Atomic File Utils Tests

`tests/framework/test_atomic_file_utils.py` — that a file written through
`python/framework/utils/atomic_file_utils.py` is old or new, never half, and that a durable write
also survives a crash of the machine. Whether a write is durable is the store's to say: every write
names its store (#632). Runs under the synthetic `framework/_root` suite.

## Why durability is tested at all

A file renamed to a new name can come back empty after a power loss: the filesystem may commit the
rename before the bytes. A sealed trading day (#476) is such a file, and right after it is written
the stream's own file is replaced, so the day record is the day's only copy. A durable write forces
the bytes to the disk before the rename and the rename after it. A process that is merely killed
loses nothing either way; that case is the atomic write's, and it is covered where the stores use
it.

## What Is Tested

| Test | Description |
|------|-------------|
| `test_the_bytes_reach_the_disk_before_the_rename_and_the_rename_after` | a durable write syncs the file, then renames, then syncs the folder — in that order |
| `test_a_derived_store_is_not_synced` | a write into a derived store costs no sync |
| `test_a_carry_over_a_record_and_the_host_identity_are_synced` | both carry-overs, a record and the host identity are written durably |
| `test_a_mode_is_kept` | a mode the caller names is the file's — a run patch stays readable by its owner only |
| `test_a_folder_that_refuses_to_sync_leaves_the_write_standing` | a mount that refuses to sync a folder does not fail the write; the file is in place |
| `test_a_write_that_fails_leaves_no_temporary_file` | a write that fails half-way leaves nothing behind |

## What Is Not Tested Here

A real power loss — it cannot be provoked in a test. What the stores write through the helper is
tested with the stores: the day records in [Reporting Tests](reporting_tests.md), the ledger in the
optimization suite's ledger tests. Which store writes durably, and that nothing renames a file into
place by hand, is the store suite's `test_store_durability.py` ([Store Tests](store_tests.md)).
