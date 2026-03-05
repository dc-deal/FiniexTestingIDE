# Unified Test Runner

Runs all core test suites sequentially and prints a compact pass/fail summary.

## Usage

```bash
python python/cli/test_runner_cli.py
```

Also available as VS Code launch config: **Pytest: Run All Core Tests**

## Configuration

**Base config:** `configs/test_config.json`
**User override:** `user_configs/test_config.json` (optional, gitignored)

```json
{
  "excluded": ["mvp_benchmark"],
  "ignored": ["shared"],
  "fail_fast": true
}
```

| Key | Type | Description |
|-----|------|-------------|
| `excluded` | `List[str]` | Suite directories skipped entirely |
| `ignored` | `List[str]` | Directories that are not test suites (silently skipped) |
| `fail_fast` | `bool` | `true`: abort on first suite failure. `false`: run all suites |

User overrides are deep-merged into the base config. Lists are replaced, not appended.

## How It Works

1. Loads config via `TestConfigLoader` (with user override support)
2. Scans `tests/` for subdirectories
3. Filters out `excluded`, `ignored`, and `__pycache__` directories
4. Runs each suite via `pytest tests/<suite>/ -v --tb=short` as subprocess
5. Parses pytest summary output for pass/fail/error/skipped counts
6. Prints compact per-suite result line
7. If `fail_fast` is enabled and a suite fails, execution stops immediately

## Output

Successful run:
```
Running 12 test suites...
──────────────────────────────────────────────────
  active_order_display   10 passed
  data_integration        9 passed
  import_pipeline        46 passed
  ...
──────────────────────────────────────────────────
TOTAL: 575 passed, 0 failed
```

Aborted run (fail_fast):
```
Running 12 test suites...
──────────────────────────────────────────────────
  active_order_display   10 passed
  data_integration       ❌ 1 failed, 8 passed
──────────────────────────────────────────────────
ABORTED (fail_fast) after data_integration
Suites run: 2
TOTAL: 18 passed, 1 failed
```

## Files

| File | Purpose |
|------|---------|
| `configs/test_config.json` | Base configuration |
| `python/configuration/test_config_loader.py` | Config loader with user override support |
| `python/cli/test_runner_cli.py` | CLI entry point and runner logic |
