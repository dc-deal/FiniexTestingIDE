# MVP Benchmark Tests Documentation

## Overview

The MVP benchmark test suite validates performance regression against registered system baselines. Unlike functional tests that verify correctness, these tests ensure the system performs within acceptable tolerances compared to a known baseline.

**Key Principle:** Performance tests are system-bound. A test passing on one system does not guarantee it passes on another. New systems can be registered by running the benchmark scenario manually and adding the results to the configuration.

**Test Configuration:** `mvp_backtesting_loadtest_40_scenarios.json`
- Symbol: USDJPY
- Account Currency: JPY (auto-detected)
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
| `benchmark_execution_summary` | session | BatchExecutionSummary from running the 40-scenario benchmark |
| `benchmark_metrics` | session | Extracted metrics: ticks/s, tickrun_time, warmup_time, total_ticks |

### Report Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `benchmark_report` | session | Complete report dict with metrics, deviations, and status |

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
| `test_ticks_per_second` | Primary throughput metric. Measured ticks/s must be within ±10% of baseline. FAILS if slower, PASSES with warning if faster. |
| `test_tickrun_time` | Tick processing duration must be within ±10% of baseline. Excludes warmup time. |
| `test_warmup_time` | Data loading phase must be within ±15% of baseline. Larger tolerance due to IO variance (disk speed, caching). |

#### TestBenchmarkExecution

| Test | Description |
|------|-------------|
| `test_all_scenarios_successful` | All 40 benchmark scenarios must complete successfully |
| `test_tick_count_matches` | Total ticks processed (1,496,267) must match baseline exactly. Mismatch indicates config or data change. |
| `test_scenario_count_matches` | Scenario count (40) must match baseline exactly |

#### TestReportGeneration

| Test | Description |
|------|-------------|
| `test_zz_save_benchmark_report` | Saves benchmark report to `reports/` directory. Runs last (alphabetically) to ensure all tests complete first. Outputs commit reminder. |

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
  "scenario":  "backtesting/mvp_backtesting_loadtest_40_scenarios.json",
  "tolerances": {
    "ticks_per_second": { "percent": 10.0 },
    "tickrun_time_s": { "percent": 10.0 },
    "warmup_time_s": { "percent": 15.0 }
  },
  "certificate": {
    "validity_days": 90
  }
}
```

| Setting | Value | Rationale |
|---------|-------|-----------|
| ticks_per_second tolerance | ±10% | Primary CPU-bound metric |
| tickrun_time_s tolerance | ±10% | Tick processing duration |
| warmup_time_s tolerance | ±15% | IO-bound, disk variance expected |
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
        "created": "2026-01-07T07:12:34Z",
        "scenario":  "backtesting/mvp_backtesting_loadtest_40_scenarios"",
        "metrics": {
          "ticks_per_second": 49133,
          "tickrun_time_s": 30.5,
          "warmup_time_s": 22.3,
          "total_ticks": 1496267,
          "scenarios_count": 40
        }
      }
    }
  }
}
```

### Benchmark Scenario Structure

The benchmark scenario `mvp_backtesting_loadtest_40_scenarios.json` defines 40 USDJPY trading blocks:

```json
{
  "version": "1.0",
  "scenario_set_name":  "backtesting/mvp_backtesting_loadtest_40_scenarios"",
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
      "initial_balance": 10000,
      "currency": "EUR"
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
         "backtesting/mvp_backtesting_loadtest_40_scenarios.json"
    ],
    "console": "integratedTerminal",
    "justMyCode": false
}
```

**Option B: CLI (Docker/Linux)**

```bash
python python/cli/strategy_runner_cli.py run mvp_backtesting_loadtest_40_scenarios.json
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
        "created": "2026-01-08T00:00:00Z",
        "scenario":  "backtesting/mvp_backtesting_loadtest_40_scenarios"",
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
pytest tests/mvp_benchmark/test_throughput_regression.py -v
```

**Important Notes:**

- **Always review manually** - Performance tests require human judgment. Check that values are reasonable.
- **Run multiple times** - Consider running 2-3 times to ensure stable measurements.
- **No debugger** - Never run with debugger attached; it invalidates all timing.
- **Consistent environment** - Close unnecessary applications, ensure no heavy background processes.

---

## Benchmark Report Format

Reports are saved as JSON with full audit trail in `tests/mvp_benchmark/reports/`.

```json
{
  "timestamp": "2026-01-07T18:36:07Z",
  "valid_until": "2026-04-07T18:36:07Z",
  "git_commit": "986911d",
  "system_id": "ryzen_7_8845hs_16core",
  "system_details": {
    "cpu_model": "AMD Ryzen 7 8845HS w/ Radeon 780M Graphics",
    "cpu_cores": 16,
    "ram_total_gb": 30.3,
    "platform": "Linux 6.6.87.2-microsoft-standard-WSL2"
  },
  "scenario":  "backtesting/mvp_backtesting_loadtest_40_scenarios.json",
  "debug_mode_detected": false,
  "overall_status": "PASSED",
  "metrics": [
    {
      "name": "ticks_per_second",
      "measured": 53510.66,
      "reference": 49133,
      "deviation_percent": 8.91,
      "tolerance_percent": 10.0,
      "status": "PASSED"
    },
    {
      "name": "tickrun_time_s",
      "measured": 27.96,
      "reference": 30.5,
      "deviation_percent": -8.32,
      "tolerance_percent": 10.0,
      "status": "PASSED"
    },
    {
      "name": "warmup_time_s",
      "measured": 19.46,
      "reference": 22.3,
      "deviation_percent": -12.73,
      "tolerance_percent": 15.0,
      "status": "PASSED"
    }
  ],
  "warnings": []
}
```

**Note:** If `debug_mode_detected` is `true`, the report is automatically `FAILED` regardless of metric results.

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
BatchOrchestrator.run()
  └→ BatchExecutionSummary
       ├→ batch_warmup_time (IO-bound phase)
       ├→ batch_tickrun_time (CPU-bound phase)
       └→ process_result_list[]
            └→ tick_loop_results.coordination_statistics.ticks_processed
```

### Metric Calculation

```python
total_ticks = sum(
    r.tick_loop_results.coordination_statistics.ticks_processed
    for r in batch_execution_summary.process_result_list
)
ticks_per_second = total_ticks / batch_execution_summary.batch_tickrun_time
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
# Run benchmark and generate report (runs all 40 scenarios)
pytest tests/mvp_benchmark/test_throughput_regression.py -v

# Commit the report
git add tests/mvp_benchmark/reports/
git commit -m "Update benchmark report"
```

### CI Pipeline

```bash
# CI only validates the certificate (fast, ~1 second, no benchmark execution)
pytest tests/mvp_benchmark/test_benchmark_certificate.py -v
```

### Full Local Test Suite

```bash
# Run everything (certificate + benchmark)
# Note: Certificate tests may skip if no report exists yet
pytest tests/mvp_benchmark/ -v
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