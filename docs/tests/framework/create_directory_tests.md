# Create Directory Tests

`tests/framework/test_create_directory.py` — that `create_directory()`
(`python/framework/utils/file_utils.py`) is the one way the IDE creates a directory, and that a
refusal says what went wrong. Runs under the synthetic `framework/_root` suite.

## Why this file exists

The IDE creates every directory it works in and needs access to all of them. Before #621 each
writer called `mkdir` itself, and an operating system that refused — no permission, a read-only
volume, a full disk — surfaced as a bare traceback in whichever writer came first. Now one
function creates every directory, and a refusal is `DirectoryCreationDeniedError`: a hard error
for whatever needed the directory, with a message that opens with the cause.

```
OS Permission Error — directory creation denied: runs/autotrader/my_profile/<run_id>
[Errno 13] Permission denied — it was to hold the run's record: header, reports and streams.
Check that the IDE may write there; the path comes from file_logging.run_logs.
```

## What it pins

| Class | What it pins |
|---|---|
| `TestCreatingADirectory` | the directory is created with its parents; an existing one is left as it is, its files untouched; a new one gets the mode asked for (the activity list asks for `0o700`) |
| `TestARefusalIsAHardErrorThatSaysWhy` | a permission refusal opens with `OS Permission Error — directory creation denied: <path>` and names the errno, what the directory was to hold and the setting; any other refusal — a file standing where a parent should be — opens with `OS Error`; the error is an `OSError` too, so a caller that carries on without its directory keeps catching it |
| `TestARunWhoseDirectoryIsRefusedStops` | a backtest whose run directory is refused ends as a configuration error with the message and no stack trace, before any scenario runs; the AutoTrader CLI's refusal is pinned beside its siblings in `tests/autotrader/config/test_session_origin.py` |

## Running the Tests

```bash
pytest tests/framework/test_create_directory.py -v
```
