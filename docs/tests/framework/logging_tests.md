# Logging Test Suite

Pins the contract of the log buffer: it carries **records**, not rendered lines.

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

The fourth thing it pins is that the change was **invisible on screen**: rendering moved out of
the capture path, so the printed line must be character-identical to what the old formula
produced.

## Layout

| Class | What it pins |
|---|---|
| `TestTheRecordCarriesTheFact` | the message is bare (no colour, no level, no timestamp); observation time (`timestamp`) and event time (`tick_time`) are separate fields per §9; no tick fields outside the tick loop; a record survives `pickle` — it crosses the process boundary on `ProcessResult` |
| `TestRenderingMovedButDidNotChange` | `render_record()` output is character-identical to the pre-refactor formula, which the test **reproduces in code** rather than as an expected string — including the millisecond truncation and the fixed-width elapsed column; the tick prefix is rebuilt from the record's own fields |
| `TestADisplaySettingCannotHideAReportInput` | with the console gate closed, WARNING and ERROR are still captured; INFO and DEBUG are not; `get_records(level)` filters |

`TestRenderingMovedButDidNotChange._as_before` is worth knowing about: it holds the OLD formula
verbatim. A hand-written expected string would have been wrong — the original truncates
milliseconds (`3.417s` renders as `416ms`), and only comparing against the formula catches that.

## Running

```bash
pytest tests/framework/logging/ -v --tb=short
```

VS Code: **"🧩 Pytest: Logging (All)"** launch configuration.

## Related

- The capture rule and what a Tier-2 row may claim (`WarningTier.LOGGER_PRODUCED`):
  [Warnings & Errors — Tier Taxonomy](../../architecture/warnings_errors_tiers.md)
- What crosses the process boundary: [Batch Data Flow](../../architecture/batch_data_flow.md)
- The artifact-side proof lives with the artifacts:
  [Reporting Pipeline Tests](reporting_tests.md)
