# Benchmark Tests Documentation

## Quick Reference

```bash
# Validate existing certificate (CI, fast, no benchmark execution)
pytest tests/benchmark/test_benchmark_certificate.py -v

# Run full benchmark (3 runs, generates report + logs)
pytest tests/benchmark/test_throughput_regression.py -v --release-version 1.2.0

# With tester comment (stored in report JSON)
pytest tests/benchmark/test_throughput_regression.py -v --release-version 1.2.0 --comment "Acer laptop, performance mode: balanced"
```

---

## Overview

The benchmark test suite validates performance regression against registered system baselines. Unlike functional tests that verify correctness, these tests ensure the system performs within acceptable tolerances compared to a known baseline.

**Key Principle:** Performance tests are system-bound. A test passing on one system does not guarantee it passes on another. New systems can be registered by running the benchmark scenario manually and adding the results to the configuration.

**Test Configuration:** `backtesting_loadtest_40_scenarios.json`
- Symbol: USDJPY
- Account Currency: JPY
- Scenarios: 40 parallel blocks (12-hour windows, New York session)
- Total Ticks: 1,496,267
- Baseline System: AMD Ryzen 7 8845HS (16 cores, 28+ GB RAM)

**Total Tests:** 13

---

## Fixtures (conftest.py)

### Configuration Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `benchmark_config` | session | Parsed benchmark_config.json with tolerances and validity settings |
| `reference_systems` | session | Parsed reference_systems.json with registered systems and baselines |

### System Validation Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `system_fingerprint` | session | Current system hardware details (CPU, cores, RAM) |
| `debug_mode_detected` | session | Boolean indicating if debugger is attached |
| `validated_system` | session | System ID after matching against registered systems (FAILS if unregistered) |
| `baseline_metrics` | session | Baseline performance metrics for the validated system |

### Execution Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `benchmark_execution_runs` | session | List of BenchmarkRunResult from N runs (default: 3) with summary, timing, and log paths |
| `benchmark_metrics` | session | Median metrics across all runs: ticks/s, tickrun_time, warmup_time, summary_generation_time, raw_measurements |

### Report Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `benchmark_report` | session | Complete report dict with median metrics, deviations, raw measurements, artifacts, and status |

---

## Test Files

### test_environment_check.py (2 Tests)

Pre-flight validation before benchmark execution. Ensures the environment is suitable for performance measurement.

#### TestEnvironmentCheck

| Test | Description |
|------|-------------|
| `test_no_debugger_attached` | Verifies no debugger is attached (debugpy, pydevd, sys.gettrace). Debuggers invalidate timing measurements. FAILS if debugger detected, other tests continue but report shows FAILED. |

#### TestSystemResources

| Test | Description |
|------|-------------|
| `test_placeholder_for_future_checks` | Placeholder for future system resource checks (RAM availability, CPU throttling, etc.) |

---

### test_throughput_regression.py (7 Tests)

Main benchmark tests comparing measured performance against baseline. Only runs on registered systems.

#### TestThroughputRegression

| Test | Description |
|------|-------------|
| `test_ticks_per_second` | Primary throughput metric (median of 3 runs). Must be within ±20% of baseline. FAILS if slower, PASSES with warning if faster. |
| `test_tickrun_time` | Tick processing duration (median of 3 runs). Must be within ±20% of baseline. CPU-bound, excludes warmup. |
| `test_warmup_time` | Data loading phase (median of 3 runs). Must be within ±30% of baseline. IO-bound, larger tolerance due to disk/cache variance. |

#### TestBenchmarkExecution

| Test | Description |
|------|-------------|
| `test_all_scenarios_successful` | All 40 benchmark scenarios must complete successfully in all 3 runs |
| `test_tick_count_matches` | Total ticks processed (1,496,267) must match baseline exactly. Mismatch indicates config or data change. |
| `test_scenario_count_matches` | Scenario count (40) must match baseline exactly |

#### TestReportGeneration

| Test | Description |
|------|-------------|
| `test_zz_save_benchmark_report` | Saves benchmark report to `reports/` directory, copies log artifacts to `reports/logs/run_N/`. Runs last (alphabetically). |

---

### test_benchmark_certificate.py (4 Tests)

CI-friendly tests that validate benchmark certificates without running the actual benchmark.

#### TestBenchmarkCertificate

| Test | Description |
|------|-------------|
| `test_report_exists` | A benchmark report must exist in `reports/` directory. SKIPS if not found (allows local full-suite runs). |
| `test_report_not_expired` | Report must not be expired (90-day validity). FAILS if `valid_until` is in the past. |
| `test_report_passed` | Report `overall_status` must be "PASSED". Also checks `debug_mode_detected` flag. |
| `test_report_integrity` | Report must have all required fields: timestamp, valid_until, system_id, scenario, debug_mode_detected, overall_status, metrics. |

---

## Configuration Files

### benchmark_config.json

Global benchmark settings including tolerances and validity.

```json
{
  "scenario": "backtesting/backtesting_loadtest_40_scenarios.json",
  "runs": 3,
  "tolerances": {
    "ticks_per_second": { "percent": 20.0 },
    "tickrun_time_s": { "percent": 20.0 },
    "warmup_time_s": { "percent": 30.0 }
  },
  "certificate": {
    "validity_days": 90
  }
}
```

| Setting | Value | Rationale |
|---------|-------|-----------|
| runs | 3 | Statistical stability via median — eliminates single-run variance |
| ticks_per_second tolerance | ±20% | CPU-bound, stable across runs |
| tickrun_time_s tolerance | ±20% | CPU-bound tick processing duration |
| warmup_time_s tolerance | ±30% | IO-bound (disk, WSL bridge), higher variance expected |
| validity_days | 90 | Forces quarterly re-validation |

### reference_systems.json

Registered systems with hardware specs and baseline metrics.

```json
{
  "systems": {
    "ryzen_7_8845hs_16core": {
      "description": "Frank's Development System - WSL2/Docker",
      "hardware": {
        "cpu_model": "AMD Ryzen 7 8845HS w/ Radeon 780M Graphics",
        "cpu_cores": 16,
        "ram_minimum_gb": 28.0
      },
      "baseline": {
        "created": "2026-03-18T09:00:00Z",
        "scenario": "backtesting/backtesting_loadtest_40_scenarios",
        "metrics": {
          "ticks_per_second": 90000,
          "tickrun_time_s": 17.0,
          "warmup_time_s": 18.0,
          "total_ticks": 1496267,
          "scenarios_count": 40
        }
      }
    }
  }
}
```

### Benchmark Scenario Structure

The benchmark scenario `backtesting_loadtest_40_scenarios.json` defines 40 USDJPY trading blocks:

```json
{
  "version": "1.0",
  "scenario_set_name": "backtesting_loadtest_40_scenarios",
  "global": {
    "data_mode": "realistic",
    "strategy_config": {
      "decision_logic_type": "CORE/aggressive_trend",
      "worker_instances": {
        "rsi_fast": "CORE/rsi",
        "envelope_main": "CORE/envelope"
      }
    },
    "trade_simulator_config": {
      "balances": { "EUR": 10000 }
    }
  },
  "scenarios": [
    {
      "name": "USDJPY_blocks_01",
      "symbol": "USDJPY",
      "start_date": "2025-09-18T16:00:00+00:00",
      "end_date": "2025-09-19T04:00:00+00:00"
    }
    // ... 39 more scenarios
  ]
}
```

---

## Registering a New System

When running on a new system (different CPU, new machine), you must first register it before benchmark tests will pass.

### Step 1: Run the Benchmark Scenario Manually

**Option A: VS Code (without debugger!)**

Use the launch configuration "🧪 Run (BENCHMARK Scenario)" but run it **without debugging** (Ctrl+F5 or "Run Without Debugging"):

```json
{
    "name": "🧪 Run (BENCHMARK Scenario)",
    "type": "debugpy",
    "request": "launch",
    "program": "${workspaceFolder}/python/cli/strategy_runner_cli.py",
    "args": [
        "run",
         "backtesting/backtesting_loadtest_40_scenarios.json"
    ],
    "console": "integratedTerminal",
    "justMyCode": false
}
```

**Option B: CLI (Docker/Linux)**

```bash
pytest tests/benchmark/ -v --release-version dev
```

### Step 2: Read the Executive Summary

After the run completes, look for the Executive Summary in the output:

```
------------------------------------------------------------
🎯 EXECUTIVE SUMMARY
------------------------------------------------------------
EXECUTION RESULTS
--------------------------------------------------------------------
Scenarios:          40 executed
Success Rate:       100.0% (40/40 successful)
Status:             ✅ Complete Success
Batch Time:         35.5s (warmup: 14.9s | tickrun: 20.6s)
Mode:               Parallel (max 99 workers)

IN-TIME PERFORMANCE (Simulated Market Time)
--------------------------------------------------------------------
Total Simulation:   415.0 hours (17.3 days)
Avg per Scenario:   10.38 hours
Ticks Processed:    1,496,267 total
Ticks/Hour:         3,605 (market density)

REAL-TIME PERFORMANCE (Tick Processing Speed)
--------------------------------------------------------------------
Tick Run Time:      20.6 seconds           ← tickrun_time_s
Ticks/Second:       72,527 (processing rate)  ← ticks_per_second

SYSTEM RESOURCES
--------------------------------------------------------------------
CPU Cores:          16
RAM:                27.9 GB available / 30.3 GB total
```

### Step 3: Add Your System to reference_systems.json

Extract the relevant values and add a new entry:

```json
{
  "systems": {
    "your_system_id": {
      "description": "Your description - OS/Environment",
      "hardware": {
        "cpu_model": "Your CPU Model (from /proc/cpuinfo or system info)",
        "cpu_cores": 16,
        "ram_minimum_gb": 24.0
      },
      "baseline": {
        "created": "2026-03-18T00:00:00Z",
        "scenario": "backtesting/backtesting_loadtest_40_scenarios",
        "metrics": {
          "ticks_per_second": 72527,
          "tickrun_time_s": 20.6,
          "warmup_time_s": 14.9,
          "total_ticks": 1496267,
          "scenarios_count": 40
        }
      }
    }
  }
}
```

### Step 4: Verify Registration

Run the benchmark tests:

```bash
pytest tests/benchmark/test_throughput_regression.py -v
```

**Important Notes:**

- **Always review manually** - Performance tests require human judgment. Check that values are reasonable.
- **3-run median** - The benchmark automatically runs 3 times and uses the median for each metric.
- **No debugger** - Never run with debugger attached; it invalidates all timing.
- **Consistent environment** - Close unnecessary applications, ensure no heavy background processes.

---

## Benchmark Report Format

Reports are saved as JSON with full audit trail in `tests/benchmark/reports/`. Filename format: `benchmark_report_{version}_{timestamp}.json`.

```json
{
  "release_version": "1.2.0",
  "timestamp": "2026-03-18T09:08:33Z",
  "valid_until": "2026-06-16T09:08:33Z",
  "git_commit": "abc1234",
  "system_id": "ryzen_7_8845hs_16core",
  "system_details": {
    "cpu_model": "AMD Ryzen 7 8845HS w/ Radeon 780M Graphics",
    "cpu_cores": 16,
    "ram_total_gb": 30.3,
    "platform": "Linux 6.6.87.2-microsoft-standard-WSL2"
  },
  "scenario": "backtesting/backtesting_loadtest_40_scenarios.json",
  "runs": 3,
  "debug_mode_detected": false,
  "overall_status": "PASSED",
  "metrics": [
    {"name": "ticks_per_second", "measured": 90024.88, "reference": 90000, "deviation_percent": 0.03, "tolerance_percent": 20.0, "status": "PASSED"},
    {"name": "tickrun_time_s", "measured": 16.62, "reference": 17.0, "deviation_percent": -2.24, "tolerance_percent": 20.0, "status": "PASSED"},
    {"name": "warmup_time_s", "measured": 17.79, "reference": 18.0, "deviation_percent": -1.17, "tolerance_percent": 30.0, "status": "PASSED"},
    {"name": "summary_generation_time_s", "measured": 1.2, "reference": null, "deviation_percent": null, "tolerance_percent": null, "status": "INFO"},
    {"name": "total_ticks", "measured": 1496267, "reference": 1496267, "deviation_percent": null, "tolerance_percent": null, "status": "INFO"},
    {"name": "scenarios_count", "measured": 40, "reference": 40, "deviation_percent": null, "tolerance_percent": null, "status": "INFO"}
  ],
  "raw_measurements": {
    "ticks_per_second": [89500.12, 90024.88, 90500.44],
    "tickrun_time_s": [16.8, 16.62, 16.5],
    "warmup_time_s": [18.1, 17.79, 17.5],
    "summary_generation_time_s": [1.1, 1.2, 1.3]
  },
  "artifacts": [
    {"source": "logs/scenario_sets/.../scenario_summary.log", "destination": "tests/benchmark/reports/logs/run_1/scenario_summary.log", "copied_at": "..."},
    {"source": "logs/scenario_sets/.../scenario_global_log.log", "destination": "tests/benchmark/reports/logs/run_1/scenario_global_log.log", "copied_at": "..."}
  ],
  "warnings": []
}
```

**Notes:**
- All measured values are **medians** across 3 runs
- `raw_measurements` contains the individual run values for traceability
- `artifacts` lists all log files copied to `reports/logs/run_N/`
- If `debug_mode_detected` is `true`, the report is automatically `FAILED` regardless of metric results

---

## CLI Parameters

### `--release-version`

Optional parameter that controls the `release_version` field in the generated JSON report. Defaults to `"dev"`.

```bash
# Development runs (default)
pytest tests/benchmark/ -v --release-version dev

# Release runs
pytest tests/benchmark/ -v --release-version 1.2.0
```

| Value | Meaning |
|-------|---------|
| `"dev"` | Default. Development/testing run — not a valid release artifact |
| `"X.Y.Z"` | Release version. Report is a valid release artifact |

Reports with `"release_version": "dev"` are not valid release artifacts. For releases, always specify the actual version number.

### `--comment`

Optional free-text comment stored in the report JSON. Useful for documenting test conditions.

```bash
pytest tests/benchmark/ -v --release-version 1.2.0 --comment "Acer laptop, performance mode: ultra"
```

The comment appears as `"comment": "..."` in the generated report. Omitted if not provided.

---

## Architecture Notes

### Test Design Philosophy

The benchmark suite uses a **certificate-based validation** approach:

1. **System Registration:** Only known systems can run benchmarks
2. **Baseline Comparison:** Measured values compared against stored baselines
3. **Tolerance Bands:** Acceptable deviation ranges per metric
4. **Certificate Generation:** Reports are committed to repo as proof of testing
5. **CI Validation:** CI checks certificates, not actual performance

### Debug Mode Detection

Debuggers add significant overhead that invalidates timing measurements. Detection checks:

```python
DEBUGGER_ACTIVE = (
    (hasattr(sys, 'gettrace') and sys.gettrace() is not None)
    or 'debugpy' in sys.modules   # VS Code
    or 'pydevd' in sys.modules    # PyCharm
)
```

### Key Data Flow

```
for each run (3x):
  BatchOrchestrator.run()
    └→ BatchExecutionSummary
         ├→ batch_warmup_time (IO-bound: Phases 0-5)
         ├→ batch_tickrun_time (CPU-bound: Phase 6)
         └→ process_result_list[]
              └→ tick_loop_results.coordination_statistics.ticks_processed
  BatchReportCoordinator.generate_and_log()
    └→ summary_generation_time (measured externally)

Per metric: statistics.median([run_1, run_2, run_3]) → report value
```

### Metric Calculation

```python
# Per-run extraction
tps_values = [total_ticks / r.summary.batch_tickrun_time for r in runs]
tickrun_times = [r.summary.batch_tickrun_time for r in runs]
warmup_times = [r.summary.batch_warmup_time for r in runs]

# Median for report
ticks_per_second = statistics.median(tps_values)
tickrun_time = statistics.median(tickrun_times)
warmup_time = statistics.median(warmup_times)
```

### Test Isolation

The certificate tests (`test_benchmark_certificate.py`) are designed to run **without** triggering the full benchmark. They only read JSON files from the `reports/` directory.

This is achieved by:
- No `autouse=True` fixtures that depend on benchmark execution
- Report saving happens explicitly via `test_zz_save_benchmark_report`
- Certificate tests have no fixture dependencies on execution fixtures

---

## Workflow

### Local Benchmark Run

```bash
# Run benchmark (3 runs x 40 scenarios, generates median report + log artifacts)
pytest tests/benchmark/ -v --release-version dev

# Release benchmark
pytest tests/benchmark/ -v --release-version 1.2.0

# Commit the report and logs
git add tests/benchmark/reports/
git commit -m "Update benchmark report"
```

### CI Pipeline

```bash
# CI only validates the certificate (fast, ~1 second, no benchmark execution)
pytest tests/benchmark/test_benchmark_certificate.py -v
```

### Full Local Test Suite

```bash
# Run everything (certificate + benchmark)
# Note: Certificate tests may skip if no report exists yet
pytest tests/benchmark/ -v
```

---

## Test Behaviors

| Scenario | Behavior |
|----------|----------|
| Debugger attached | `test_no_debugger_attached` FAILS, report shows `debug_mode_detected: true` |
| Unregistered system | `validated_system` fixture FAILS with registration instructions |
| Slower than tolerance | Throughput tests FAIL with possible causes listed |
| Faster than tolerance | Tests PASS with WARNING to update baseline |
| No report in CI | Certificate tests SKIP (not FAIL) |
| Expired report | `test_report_not_expired` FAILS |
| Report status FAILED | `test_report_passed` FAILS |

---

## Troubleshooting

### "Warmup time regression"

Possible causes (from `benchmark_config.json`):
- Disk change or degradation (HDD vs SSD)
- Warmup algorithm modified
- Parquet file structure changed

### "Tickrun time regression"

Possible causes:
- CPU throttling or background load
- Algorithm regression in worker/decision logic
- Memory pressure causing swapping

### "Unregistered system"

See [Registering a New System](#registering-a-new-system) above.

### "Certificate tests run the full benchmark"

This should not happen after the isolation fix. If it does:
- Ensure `conftest.py` does NOT have `autouse=True` on report-saving fixtures
- Ensure certificate tests don't import/use `benchmark_execution_summary` fixture