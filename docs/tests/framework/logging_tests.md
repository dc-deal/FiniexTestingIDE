# Logging Test Suite

Pins two contracts: the log buffer carries **records**, not rendered lines — and the run's own
time is a **column** derived from the canonical clock, not a tick counter baked into the message.

## What this suite is for

`LogRecord` (`python/framework/types/log_record_types.py`) is what a logger buffers. Before it,
the buffer held a pre-rendered console line — timestamp, level column, ANSI colour codes and the
tick prefix all baked into one string. Three consequences followed, and this suite locks all
three out:

| Consequence | Test class |
|---|---|
| ANSI escape codes travelled into `warnings_errors.json` and out over the HTTP API | `TestNoRenderingReachesTheArtifact` (in the reporting suite, where the artifact is) |
| Consumers had to take the line apart again (`split(' | ', 1)`) to recover the message | `TestTheRecordCarriesTheFact` |
| The buffer filled only when the CONSOLE threshold passed, so a display setting decided what the run report saw | `TestADisplaySettingCannotHideAReportInput` |

The record then replaced its tick fields with one `event_time`. A tick index counts one of three
pass kinds — a heartbeat or ghost interval advances the clock with no tick, and #375 adds timer
and resolution events beside it — so the index described one kind and mislabelled the other two.

**What guards the rendering is no longer "identical to the past".** The line changed on purpose.
The guard is now `TestOneFormulaForBothSurfaces`: console and file go through one formula and may
differ **only in colour**. That is the guard that catches the drift the file half carried — it
rendered its own literal, so a change to one silently desynchronised the scenario log file from
the scenario console output.

## Layout

| Class | What it pins |
|---|---|
| `TestTheRecordCarriesTheFact` | the message is bare (no colour, no level, no timestamp); observation time (`timestamp`) and event time (`event_time`) are separate fields per §9; no event time without a clock; a record survives `pickle` — it crosses the process boundary on `ProcessResult` |
| `TestOneFormulaForBothSurfaces` | the file line is the console line with the ANSI codes stripped, with and without the column; a log without the column renders exactly the pre-column line, so `global.log` and the run-level logs are provably untouched |
| `TestTheColumnIsARole` | the column is absent when the role was not declared, and holds a fixed-width filler when the role is declared but no clock has been attached — the two states a record alone cannot tell apart; the filler is never a wall-clock substitute (§9) |
| `TestADisplaySettingCannotHideAReportInput` | with the console gate closed, WARNING and ERROR are still captured; INFO and DEBUG are not; `get_records(level)` filters; **both** display surfaces re-apply the threshold — `flush_buffer` and `print_buffer` |
| `TestTheClockIsPulledNotPushed` | a logger with no clock records no event time; an attached clock stamps every later record. The pull is one attachment instead of a call site per pass kind — the pushed variant is what left the live session log without a time column for as long as it existed |

Two details worth knowing about:

- **The filler must match the stamp's width.** `format_log_event_time(None)` is padded to
  `EVENT_TIME_WIDTH`, so the column does not shift when the run enters its tick loop — the same
  reason `format_log_elapsed` is fixed width.
- **`print_buffer` is the second display surface.** `flush_buffer` filtered the console threshold
  and `print_buffer` did not, so a warning the threshold suppressed still reached the console
  through the batch flush. Both are pinned now.

## Running

```bash
pytest tests/framework/logging/ -v --tb=short
```

VS Code: **"🧩 Pytest: Logging (All)"** launch configuration.

## Related

- The capture rule and what a Tier-2 row may claim (`WarningTier.LOGGER_PRODUCED`):
  [Warnings & Errors — Tier Taxonomy](../../architecture/warnings_errors_tiers.md)
- What crosses the process boundary, and where the clock is attached:
  [Batch Data Flow](../../architecture/batch_data_flow.md)
- The artifact-side proof lives with the artifacts:
  [Reporting Pipeline Tests](reporting_tests.md)
