"""
FiniexTestingIDE - MVP Benchmark Test Fixtures
System validation and benchmark execution fixtures

Provides:
- System fingerprint validation
- Debug mode detection
- Benchmark scenario execution
- Report generation
- Configuration loading
"""

import sys
import json
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List

from python.configuration.app_config_manager import AppConfigManager
from python.scenario.scenario_config_loader import ScenarioConfigLoader
from python.framework.types.scenario_set_types import ScenarioSet
from python.framework.batch.batch_orchestrator import BatchOrchestrator
from python.framework.types.batch_execution_types import BatchExecutionSummary

from tests.mvp_benchmark.system_fingerprint import (
    get_system_fingerprint,
    find_matching_system,
    get_git_commit,
    SystemFingerprint
)


# =============================================================================
# DEBUG MODE DETECTION
# =============================================================================

def is_debugger_attached() -> bool:
    """
    Detect if a debugger is currently attached.

    Checks multiple indicators:
    - sys.gettrace(): Set when debugger steps through code
    - debugpy module: VS Code Python debugger
    - pydevd module: PyCharm/IntelliJ debugger

    Returns:
        True if debugger is detected, False otherwise
    """
    return (
        (hasattr(sys, 'gettrace') and sys.gettrace() is not None)
        or 'debugpy' in sys.modules
        or 'pydevd' in sys.modules
    )


# =============================================================================
# PATHS
# =============================================================================

BENCHMARK_CONFIG_DIR = Path(__file__).parent / "config"
BENCHMARK_REPORTS_DIR = Path(__file__).parent / "reports"


# =============================================================================
# CONFIGURATION FIXTURES
# =============================================================================

@pytest.fixture(scope="session")
def benchmark_config() -> Dict[str, Any]:
    """
    Load benchmark configuration.

    Returns:
        Parsed benchmark_config.json
    """
    config_path = BENCHMARK_CONFIG_DIR / "benchmark_config.json"
    with open(config_path, 'r') as f:
        return json.load(f)


@pytest.fixture(scope="session")
def reference_systems() -> Dict[str, Any]:
    """
    Load reference systems configuration.

    Returns:
        Parsed reference_systems.json
    """
    config_path = BENCHMARK_CONFIG_DIR / "reference_systems.json"
    with open(config_path, 'r') as f:
        return json.load(f)


# =============================================================================
# SYSTEM VALIDATION FIXTURES
# =============================================================================

@pytest.fixture(scope="session")
def system_fingerprint() -> SystemFingerprint:
    """
    Get current system fingerprint.

    Returns:
        SystemFingerprint with hardware details
    """
    return get_system_fingerprint()


@pytest.fixture(scope="session")
def debug_mode_detected() -> bool:
    """
    Check if debugger is attached.

    This fixture runs early to detect debug mode before
    expensive benchmark operations.

    Returns:
        True if debugger detected, False otherwise
    """
    return is_debugger_attached()


@pytest.fixture(scope="session")
def validated_system(
    system_fingerprint: SystemFingerprint,
    reference_systems: Dict[str, Any]
) -> str:
    """
    Validate current system against registered systems.

    This fixture FAILS if the system is not registered.
    All benchmark tests depend on this fixture.

    Returns:
        system_id of the matched registered system

    Raises:
        pytest.fail if system is not registered
    """
    system_id, error_msg = find_matching_system(
        system_fingerprint,
        reference_systems
    )

    if system_id is None:
        pytest.fail(
            f"\n{'='*60}\n"
            f"BENCHMARK ABORTED: Unregistered System\n"
            f"{'='*60}\n\n"
            f"{error_msg}\n"
        )

    return system_id


@pytest.fixture(scope="session")
def baseline_metrics(
    validated_system: str,
    reference_systems: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Get baseline metrics for the validated system.

    Args:
        validated_system: System ID that passed validation
        reference_systems: Reference systems config

    Returns:
        Baseline metrics dict
    """
    system_config = reference_systems["systems"][validated_system]
    return system_config["baseline"]["metrics"]


# =============================================================================
# BENCHMARK EXECUTION FIXTURE
# =============================================================================

@pytest.fixture(scope="session")
def benchmark_execution_summary(
    validated_system: str,
    benchmark_config: Dict[str, Any]
) -> BatchExecutionSummary:
    """
    Execute the benchmark scenario.

    Only runs after system validation passes.

    Args:
        validated_system: Ensures system check passed
        benchmark_config: Benchmark configuration

    Returns:
        BatchExecutionSummary with execution results
    """
    scenario_name = benchmark_config["scenario"]

    config_loader = ScenarioConfigLoader()
    scenario_config = config_loader.load_config(scenario_name)

    app_config = AppConfigManager()
    scenario_set = ScenarioSet(scenario_config, app_config)

    orchestrator = BatchOrchestrator(scenario_set, app_config)
    summary = orchestrator.run()

    return summary


@pytest.fixture(scope="session")
def benchmark_metrics(
    benchmark_execution_summary: BatchExecutionSummary
) -> Dict[str, float]:
    """
    Extract benchmark metrics from execution summary.

    Args:
        benchmark_execution_summary: Executed benchmark results

    Returns:
        Dict with measured metrics
    """
    summary = benchmark_execution_summary

    # Calculate total ticks
    total_ticks = sum(
        r.tick_loop_results.coordination_statistics.ticks_processed
        for r in summary.process_result_list
        if r.tick_loop_results and r.tick_loop_results.coordination_statistics
    )

    # Calculate ticks per second
    tickrun_time = summary.batch_tickrun_time
    ticks_per_second = total_ticks / tickrun_time if tickrun_time > 0 else 0

    return {
        "ticks_per_second": ticks_per_second,
        "tickrun_time_s": summary.batch_tickrun_time,
        "warmup_time_s": summary.batch_warmup_time,
        "total_ticks": total_ticks,
        "scenarios_count": len(summary.process_result_list)
    }


# =============================================================================
# REPORT GENERATION
# =============================================================================

@pytest.fixture(scope="session")
def benchmark_report(
    validated_system: str,
    benchmark_config: Dict[str, Any],
    baseline_metrics: Dict[str, Any],
    benchmark_metrics: Dict[str, float],
    system_fingerprint: SystemFingerprint,
    debug_mode_detected: bool
) -> Dict[str, Any]:
    """
    Generate benchmark report with all metrics and deviations.

    This fixture is evaluated after all tests run.

    Returns:
        Complete benchmark report dict
    """
    now = datetime.now(timezone.utc)
    validity_days = benchmark_config["certificate"]["validity_days"]
    valid_until = now + timedelta(days=validity_days)

    tolerances = benchmark_config["tolerances"]

    # Build metrics array with deviations
    metrics_list = []
    warnings = []
    overall_status = "PASSED"

    # Check debug mode FIRST - invalidates entire benchmark
    if debug_mode_detected:
        overall_status = "FAILED"
        warnings.append(
            "DEBUGGER DETECTED: Benchmark results are INVALID. "
            "Run without debugger for valid measurements."
        )

    for metric_name in ["ticks_per_second", "tickrun_time_s", "warmup_time_s"]:
        measured = benchmark_metrics.get(metric_name, 0)
        reference = baseline_metrics.get(metric_name, 0)
        tolerance = tolerances.get(metric_name, {}).get("percent", 10.0)

        # Calculate deviation
        if reference > 0:
            deviation = ((measured - reference) / reference) * 100
        else:
            deviation = 0.0

        # Determine status
        abs_deviation = abs(deviation)
        if abs_deviation <= tolerance:
            status = "PASSED"
        else:
            if deviation > 0:
                # Faster than baseline
                status = "PASSED"
                warnings.append(
                    f"Performance {abs_deviation:.1f}% BETTER than baseline for {metric_name}. "
                    f"Consider updating baseline if code was optimized."
                )
            else:
                # Slower than baseline
                status = "FAILED"
                overall_status = "FAILED"

        metrics_list.append({
            "name": metric_name,
            "measured": round(measured, 2),
            "reference": reference,
            "deviation_percent": round(deviation, 2),
            "tolerance_percent": tolerance,
            "status": status
        })

    # Add informational metrics (no tolerance check)
    for metric_name in ["total_ticks", "scenarios_count"]:
        measured = benchmark_metrics.get(metric_name, 0)
        reference = baseline_metrics.get(metric_name, 0)
        metrics_list.append({
            "name": metric_name,
            "measured": measured,
            "reference": reference,
            "deviation_percent": None,
            "tolerance_percent": None,
            "status": "INFO"
        })

    report = {
        "timestamp": now.isoformat(),
        "valid_until": valid_until.isoformat(),
        "git_commit": get_git_commit(),
        "system_id": validated_system,
        "system_details": {
            "cpu_model": system_fingerprint.cpu_model,
            "cpu_cores": system_fingerprint.cpu_cores,
            "ram_total_gb": round(system_fingerprint.ram_total_gb, 1),
            "platform": system_fingerprint.platform
        },
        "scenario": benchmark_config["scenario"],
        "debug_mode_detected": debug_mode_detected,
        "overall_status": overall_status,
        "metrics": metrics_list,
        "warnings": warnings
    }

    return report


def _save_benchmark_report(report: Dict[str, Any]) -> Path:
    """
    Save benchmark report to reports directory.

    Args:
        report: Complete benchmark report

    Returns:
        Path to saved report file
    """
    BENCHMARK_REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.fromisoformat(report["timestamp"])
    date_str = timestamp.strftime("%Y-%m-%d_%H%M%S")

    filename = f"benchmark_report_{date_str}.json"
    filepath = BENCHMARK_REPORTS_DIR / filename

    with open(filepath, 'w') as f:
        json.dump(report, f, indent=2)

    return filepath


# =============================================================================
# SESSION FINISH HOOK - SAVE REPORT
# =============================================================================

# =============================================================================
# REPORT SAVING (Called explicitly by test_zz_save_report)
# =============================================================================

# NOTE: We intentionally do NOT use autouse=True here.
# Reason: When running only test_benchmark_certificate.py, we don't want
# to trigger the full benchmark execution just to save a report.
# The report is saved explicitly by test_zz_save_report in
# test_throughput_regression.py which runs last (alphabetically).
