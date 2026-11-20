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
  "fail_fast": true,
  "lanes": 4,
  "suite_timeout_seconds": 900
}
```

| Key | Type | Description |
|-----|------|-------------|
| `excluded` | `List[str]` | Suite directories skipped entirely (accepts both plain names `shared` and group/suite form `simulation/benchmark`) |
| `ignored` | `List[str]` | Directories that are not test suites (silently skipped) |
| `fail_fast` | `bool` | `true`: stop starting further suites once one fails. `false`: run all suites |
| `lanes` | `int` | Ceiling on suites running side by side. The machine sets the other ceiling — the runner starts the smaller of the two, so a second suite running beside this one lowers the number by itself |
| `suite_timeout_seconds` | `int` | How long one suite may run before the runner gives up on it. Running one suite at a time made a hang obvious; several at once hide it |

The model behind these keys is `SuiteRunnerConfig`
(`python/framework/types/config_types/suite_runner_config_types.py`), which owns every default
and refuses a key it does not know — a misspelled setting is a hard error rather than one that
silently does nothing.

User overrides are deep-merged into the base config. Lists are replaced, not appended.

## Directory Structure

Test suites are grouped by pipeline under `tests/`:

```
tests/
├── autotrader/       # AutoTrader pipeline (integration, safety, live_executor, order_guard)
├── simulation/       # Backtesting pipeline (baseline, margin_validation, benchmark, ...)
├── parity/           # Dual-pipeline parity tests — simulation vs. AutoTrader (#294)
├── data/             # Data pipeline (import_pipeline, data_integration, ...)
├── framework/        # Framework mechanics (bar_rendering, worker_tests, path_based_loading)
└── shared/           # Helper modules (not a suite — ignored)
```

For the full test classification (marks, test types, suite map) see [test_taxonomy.md](test_taxonomy.md).

Integration tests that use JSON profiles/scenario sets must reference the test subdirectory of
the respective config source:
- AutoTrader tests → `configs/autotrader_profiles/mock/`
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
4. Decides how many lanes to run: the smaller of the configured ceiling and what the machine
   reports free, never below one
5. Runs the suites through a pool of that many lanes, each as `pytest tests/<group>/<suite>/ -v
   --tb=short -p no:cacheprovider --basetemp <own dir>` in a subprocess of its own. Each suite is
   handed a temporary directory of its own, so several starting in the same second do not compete
   over the counter pytest would otherwise use, and the cache provider is off because it writes
   one file in the working directory that every suite would be writing at once
6. Classifies how each run ended by the code it exited with — tests failed, interrupted, internal
   error, usage error, killed by a signal — or as timed out where the runner gave up on it
7. Prints a compact result line as each suite lands, so the order read is the order they finished
8. If `fail_fast` is enabled and a suite fails, no further suite is STARTED; the ones already
   running are left to finish, because killing work seconds from done buys nothing and leaves
   half-written directories behind

## Output

Successful run:
```
Running 26 test suites...
Started:  2026-06-17 14:30:05 UTC
──────────────────────────────────────────────────
Lanes:    4
──────────────────────────────────────────────────
  autotrader/live_executor           58 passed  (4s)
  data/import_pipeline               57 passed  (7s)
  parity                             no tests   (0s)
  simulation/baseline                45 passed  (9s)
  autotrader/integration             39 passed  (15s)
  ...
──────────────────────────────────────────────────
Suites: 26 with a verdict
TOTAL: 858 passed  (41s on the clock, 2m 23s across suites)

  autotrader        150 passed
  data              124 passed
  framework         298 passed
  parity            0 passed
  simulation        286 passed
```

Result lines appear as each suite finishes, so they read in completion order rather than
alphabetically; the per-category breakdown underneath is back in discovery order, because a
summary someone scans for a name must not depend on which lane was quickest.

**Two durations, and they answer different questions.** The clock is how long the run took. The
figure across suites is how much work it did. Running one suite at a time made them the same
number, so only one was printed — side by side they diverge, and reporting the sum alone after a
six-minute run would read as the run having taken twenty-four.

The per-category breakdown groups suites by their top-level directory. Suites with no collected
tests show `no tests` (e.g. placeholder directories like `parity/` before Phase 2 lands).

Aborted run (fail_fast):
```
Running 26 test suites...
Started:  2026-06-17 14:30:05 UTC
──────────────────────────────────────────────────
Lanes:    4
──────────────────────────────────────────────────
  autotrader/integration             39 passed  (18s)
  data/import_pipeline               ❌ 2 failed, 55 passed  (4s)
  framework/reporting                ❌ timed out (exit -1)  (15m 00s)
──────────────────────────────────────────────────
  data/import_pipeline:
    FAILED tests/data/import_pipeline/test_duplicates.py::test_a_repeated_tick_is_dropped
  framework/reporting: timed out (exit -1) — its counts describe a fragment, not the suite
ABORTED (fail_fast) — no further suites were started
Suites: 2 with a verdict, 1 without, 23 never started
TOTAL: 94 passed, 2 failed  (15m 04s on the clock, 15m 22s across suites)
```

**Where the suites' temporary files go.** Each suite works in a directory of its own under one
root per run. pytest empties a directory it is handed and does not rotate it, so the runner clears
the root itself — after a run where everything passed. A run that found something keeps it and
prints where, because that is where the evidence sits:

```
Temp:     /tmp/finiex_suite_8f3a21c0 (kept — a suite did not pass)
```

**Three states, never two.** A suite that reached a verdict, one that ran without reaching one,
and one the run never got to are different facts. The counts only describe the first group: a
suite that was killed or timed out carries the numbers it managed before it stopped, which say
how far it got and nothing about the suite. No suite is named as the cause of the abort — with
several lanes in flight the one that failed first is not necessarily the one the order suggests,
and naming the wrong one sends a reader to the wrong place.

## AutoTrader Mock Profiles & Display

AutoTrader mock profiles under `configs/autotrader_profiles/mock/` default to
`"display": {"enabled": false}` — the live console dashboard would waste cycles during
automated integration tests. When launching a mock profile interactively (e.g. via a
`🧪 AutoTrader: ...` entry in `launch.json`), pass `--display` to the CLI to force the
dashboard on without editing the profile:

```bash
python python/cli/autotrader_cli.py run \
  --config configs/autotrader_profiles/mock/mock_session_test.json \
  --display --delay 1
```

### Why no display toggle for Simulation?

The two pipelines have different process architectures:

- **AutoTrader:** Single process — event loop + Rich dashboard share the same process
  (separate threads). `display.enabled: false` eliminates rendering overhead (~100ms
  refresh cycles).
- **Simulation:** Multi-process — each scenario runs as a subprocess. Subprocesses export
  live stats via IPC queue to the parent (wall-clock gated, ~300ms interval). No in-process
  terminal rendering. The queue export is controlled via `app_config.json →
  monitoring.enabled` and has negligible overhead (one boolean check per tick when disabled,
  periodic dict serialization when enabled).

## Test Config Isolation

Backtesting test scenarios (`configs/scenario_sets/backtesting/`) **must keep explicit values** for all parameters — seeds, balances, latency ranges, etc. They must **not** rely on `app_config.json → backtesting.default_trade_simulator_config` inheritance.

**Reason:** Tests assert against deterministic outcomes (trade counts, P&L values, latency ranges).
If a test config inherits from `app_config.json` and someone changes an app default, all tests break
silently with wrong assertions rather than clear errors.

**Rule:** Normal scenario sets can be slimmed down to inherit app defaults. Test scenario sets are pinned — they define their own truth.

## Output Isolation — tests never write production data

Autouse fixtures in `tests/conftest.py` redirect everything a run persists, for the whole test
session. All of them are session-scoped and need no opt-in:

| What | Fixture | Redirected to |
|---|---|---|
| user config overrides | `FINIEX_CONFIG_ISOLATION=1` at module import | skipped entirely |
| the global log | the `pytest_configure` hook, before the test modules are collected | a temporary directory removed after the session |
| the run tree + its index | `_isolate_run_tree` | `tmp_path_factory` |
| the cross-run results ledger | `_isolate_run_results_ledger` | `tmp_path_factory` |
| the run-config store | `_isolate_run_config_store` | `tmp_path_factory` |
| the config directory's cache (#554) | `_isolate_config_directory` | `tmp_path_factory` |
| the run-patch store — except in a session of release-gate suites alone, whose certificate names its patch (`tests/shared/release_gate_session.py`) | `_isolate_run_patch_store` | `tmp_path_factory` |
| every strategy repository's own patch home (`.finiex_run_patches/`) | `_isolate_foreign_run_patches` — a test in throwaway repositories puts the real one back with `real_foreign_patch_homes` | `tmp_path_factory` |
| both carry-over stores | `_isolate_carry_over_stores` | `tmp_path_factory` |
| the list of what runs on the machine (#603) | `_isolate_activity_registry` | `tmp_path_factory` |

**Why redirect rather than switch logging off.** Turning `file_logging.scenario.enabled` off
would not even keep the tree clean: it drops the log files only, while a run's directory — its
record, with the header, the reports and the streams — is created either way (#621). And it would
take away what tests legitimately use: diagnosing a run means reading every one of its log files,
and a failing test's log is the first thing to look at. Redirecting keeps all of that and still
leaves the operator's tree untouched.

Measured 2026-08-30, before `_isolate_run_tree` existed: **134 of 138 rows** in the operator's
production run index came from two suite runs. The API served them and the viewer listed them.

**The global log is redirected in a hook, not a fixture.** Its logger is made the first time a
module that logs through it is imported — while pytest collects the test modules, before any
session fixture runs — and keeps its file for the rest of the process. A redirect in a fixture
came too late: every suite wrote into the operator's `logs/global.log` (measured 2026-10-25: a
three-second run of one suite appended 407 bytes to a 357 MB file).

### Every run a test starts is a fixture (#576)

A test's run is constructed to show something, and its header says so: every configuration a test
runs declares `"run_purpose": "fixture"` at its top level — everything in
`configs/scenario_sets/backtesting/`, `configs/autotrader_profiles/mock/` and `tests/fixtures/`.
`tests/framework/config/test_run_purpose_declaration.py` holds the folders to it.

The session checks it from the other side. `_refuse_runs_that_are_not_fixtures` wraps
`RunIndex.register_run` — the one call both pipelines make when a run starts — and fails the test
whose run carries any other purpose, naming the configuration:

```
regular_no_overrides.json declares run_purpose "regular" — a run a test starts must come from a
configuration that declares "run_purpose": "fixture"
```

A test that proves the stamp of a `regular` or `certificate` run itself requests the
`any_run_purpose` fixture, which lifts the refusal for that one test.

### Where the redirected output lands

`tmp_path_factory` is a pytest built-in, not something this project configures — pytest picks the
location and cleans it up:

```
/tmp/pytest-of-<user>/pytest-<N>/
├── run_tree0/                  ← _isolate_run_tree  (the trailing 0 is mktemp's counter)
│   ├── runs_index.parquet
│   ├── simulation/<set>/<run_id>/
│   └── autotrader/<profile>/<run_id>/
├── run_results_ledger0/        ← _isolate_run_results_ledger
├── run_configs0/               ← _isolate_run_config_store
├── config_directory0/          ← _isolate_config_directory
├── run_patches0/               ← _isolate_run_patch_store
├── foreign_run_patches0/       ← _isolate_foreign_run_patches
├── carry_over0/                ← _isolate_carry_over_stores
└── activities0/                ← _isolate_activity_registry
```

- **Retention is pytest's**, not ours: it keeps the last **3** numbered sessions and removes older
  ones (`tmp_path_retention_count` / `tmp_path_retention_policy`, both left at their defaults). No
  manual cleanup, and `/tmp` is empty after a container restart anyway.
- **One directory per pytest PROCESS.** The runner starts a separate process per suite, so a full
  run produces ~58 of them and pytest keeps three. `pytest-current` therefore points at the LAST
  suite that ran, not at the whole run — worth knowing before hunting for a specific test's log.
- `--basetemp=<dir>` moves it, but pytest **deletes that directory if it exists** — never point it
  at anything worth keeping.
- Side effect worth having: `/tmp` is the container's own filesystem, not the 9p mount the project
  sits on, so test output is written at ~2 µs per access instead of ~2.1 ms.

## Files

| File | Purpose |
|------|---------|
| `configs/test_config.json` | Base configuration |
| `python/configuration/test_config_loader.py` | Config loader with user override support |
| `python/cli/test_runner_cli.py` | CLI entry point and runner logic |
| `python/framework/types/config_types/suite_runner_config_types.py` | The model that owns the defaults |
| `python/framework/types/test_runner_types.py` | How a suite's run ended, and what one suite produced |
