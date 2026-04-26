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
  "excluded": ["simulation/benchmark"],
  "ignored": ["shared"],
  "fail_fast": true
}
```

| Key | Type | Description |
|-----|------|-------------|
| `excluded` | `List[str]` | Suite directories skipped entirely (accepts both plain names `shared` and group/suite form `simulation/benchmark`) |
| `ignored` | `List[str]` | Directories that are not test suites (silently skipped) |
| `fail_fast` | `bool` | `true`: abort on first suite failure. `false`: run all suites |

User overrides are deep-merged into the base config. Lists are replaced, not appended.

## Directory Structure

Test suites are grouped by pipeline under `tests/`:

```
tests/
├── autotrader/       # AutoTrader pipeline (integration, safety, live_executor, order_guard)
├── simulation/       # Backtesting pipeline (baseline, margin_validation, benchmark, ...)
├── parity/           # Dual-pipeline parity tests — simulation vs. AutoTrader (#294)
├── data/             # Data pipeline (import_pipeline, data_integration, ...)
├── framework/        # Framework mechanics (bar_rendering, worker_tests, user_namespace)
└── shared/           # Helper modules (not a suite — ignored)
```

For the full test classification (marks, test types, suite map) see [test_taxonomy.md](test_taxonomy.md).

Integration tests that use JSON profiles/scenario sets must reference the `backtesting/`
subdirectory of the respective config source:
- AutoTrader tests → `configs/autotrader_profiles/backtesting/`
- Simulation tests → `configs/scenario_sets/backtesting/`

Each integration test suite has its own dedicated config file named after the test purpose
(not the tick source or data): `mock_session_test.json`, `trade_lifecycle_test.json`,
`margin_validation_test.json`. This applies equally to both pipelines.

## How It Works

1. Loads config via `TestConfigLoader` (with user override support)
2. Scans `tests/` 2-level deep for suite directories (`group/suite`). If a top-level group
   directory contains test files directly, it is treated as a flat suite.
3. Filters out `excluded`, `ignored`, and `__pycache__` directories. `excluded` matches
   both plain names (e.g. `shared`) and `group/suite` form (e.g. `simulation/benchmark`).
4. Runs each suite via `pytest tests/<group>/<suite>/ -v --tb=short` as subprocess
5. Parses pytest summary output for pass/fail/error/skipped counts
6. Prints compact per-suite result line with the `group/suite` path
7. If `fail_fast` is enabled and a suite fails, execution stops immediately

## Output

Successful run:
```
Running 26 test suites...
──────────────────────────────────────────────────
  autotrader/integration             39 passed  (15s)
  autotrader/live_executor           58 passed  (4s)
  data/import_pipeline               57 passed  (7s)
  parity                             no tests   (0s)
  simulation/baseline                45 passed  (9s)
  ...
──────────────────────────────────────────────────
TOTAL: 858 passed  (2m 23s)

  autotrader        150 passed
  data              124 passed
  framework         298 passed
  parity            0 passed
  simulation        286 passed
```

The per-category breakdown groups suites by their top-level directory. Suites with no collected tests show `no tests` (e.g. placeholder directories like `parity/` before Phase 2 lands).

Aborted run (fail_fast):
```
Running 26 test suites...
──────────────────────────────────────────────────
  autotrader/integration             39 passed  (18s)
  data/import_pipeline               ❌ exit code 2  (4s)
──────────────────────────────────────────────────
ABORTED (fail_fast) after data/import_pipeline
Suites run: 2
TOTAL: 39 passed  (22s)
```

## AutoTrader Mock Profiles & Display

AutoTrader mock profiles under `configs/autotrader_profiles/backtesting/` default to
`"display": {"enabled": false}` — the live console dashboard would waste cycles during
automated integration tests. When launching a mock profile interactively (e.g. via a
`🧪 AutoTrader: ...` entry in `launch.json`), pass `--display` to the CLI to force the
dashboard on without editing the profile:

```bash
python python/cli/autotrader_cli.py run \
  --config configs/autotrader_profiles/backtesting/mock_session_test.json \
  --display --delay 1
```

### Why no display toggle for Simulation?

The two pipelines have different process architectures:

- **AutoTrader:** Single process — tick loop + Rich dashboard share the same process
  (separate threads). `display.enabled: false` eliminates rendering overhead (~100ms
  refresh cycles).
- **Simulation:** Multi-process — each scenario runs as a subprocess. Subprocesses export
  live stats via IPC queue to the parent (wall-clock gated, ~300ms interval). No in-process
  terminal rendering. The queue export is controlled via `app_config.json →
  monitoring.enabled` and has negligible overhead (one boolean check per tick when disabled,
  periodic dict serialization when enabled).

## Test Config Isolation

Backtesting test scenarios (`configs/scenario_sets/backtesting/`) **must keep explicit values** for all parameters — seeds, balances, latency ranges, etc. They must **not** rely on `app_config.json → default_trade_simulator_config` inheritance.

**Reason:** Tests assert against deterministic outcomes (trade counts, P&L values, latency ranges). If a test config inherits from `app_config.json` and someone changes an app default, all tests break silently with wrong assertions rather than clear errors.

**Rule:** Normal scenario sets can be slimmed down to inherit app defaults. Test scenario sets are pinned — they define their own truth.

## Files

| File | Purpose |
|------|---------|
| `configs/test_config.json` | Base configuration |
| `python/configuration/test_config_loader.py` | Config loader with user override support |
| `python/cli/test_runner_cli.py` | CLI entry point and runner logic |
