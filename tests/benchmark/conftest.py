"""
FiniexTestingIDE - Benchmark Test Fixtures
System validation and benchmark execution fixtures

Provides:
- System fingerprint validation
- Debug mode detection
- Benchmark scenario execution
- Report generation
- Configuration loading
"""

import sys
import re
import json
import time
import shutil
import statistics
import pytest
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List

from python.configuration.app_config_manager import AppConfigManager
from python.scenario.scenario_config_loader import ScenarioConfigLoader
from python.framework.types.scenario_types.scenario_set_types import ScenarioSet
from python.framework.batch.batch_orchestrator import BatchOrchestrator
from python.framework.batch.batch_report_coordinator import BatchReportCoordinator
from python.framework.types.batch_execution_types import BatchExecutionSummary

from tests.benchmark.system_fingerprint import (
    get_system_fingerprint,
    find_matching_system,
    get_git_commit,
    SystemFingerprint
)


# =============================================================================
# BENCHMARK RUN RESULT
# =============================================================================

@dataclass
class BenchmarkRunResult:
    """Result of a single benchmark run."""
    summary: BatchExecutionSummary
    summary_generation_time: float
    log_dir: Path
    run_index: int


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
# CLI OPTIONS
# =============================================================================

def pytest_addoption(parser):
    """Register custom CLI options for benchmark tests."""
    parser.addoption(
        '--release-version',
        action='store',
        default='dev',
        help='Release version for benchmark report (e.g. 1.2.0). Defaults to "dev" (invalid for releases).'
    )
    parser.addoption(
        '--comment',
        action='store',
        default=None,
        help='Optional tester comment stored in the report (e.g. "laptop performance mode: ultra").'
    )


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
def benchmark_execution_runs(
    validated_system: str,
    benchmark_config: Dict[str, Any]
) -> List[BenchmarkRunResult]:
    """
    Execute the benchmark scenario multiple times for statistical stability.

    Each run creates a fresh orchestrator and generates a summary report.
    The number of runs is configured in benchmark_config.json.

    Args:
        validated_system: Ensures system check passed
        benchmark_config: Benchmark configuration

    Returns:
        List of BenchmarkRunResult with execution results per run
    """
    scenario_name = benchmark_config['scenario']
    num_runs = benchmark_config.get('runs', 3)
    results: List[BenchmarkRunResult] = []

    for i in range(num_runs):
        print(f"\n{'='*60}")
        print(f"🔄 Benchmark Run {i + 1}/{num_runs}")
        print(f"{'='*60}")

        config_loader = ScenarioConfigLoader()
        scenario_config = config_loader.load_config(scenario_name)
        app_config = AppConfigManager()
        scenario_set = ScenarioSet(scenario_config, app_config)

        orchestrator = BatchOrchestrator(scenario_set, app_config)
        summary = orchestrator.run()

        # Generate summary report and measure generation time
        summary_start = time.time()
        report_coordinator = BatchReportCoordinator(
            batch_execution_summary=summary,
            scenario_set=scenario_set,
            app_config=app_config
        )
        report_coordinator.generate_and_log()
        summary_generation_time = time.time() - summary_start

        log_dir = Path(scenario_set.logger.get_log_dir()).resolve()
        results.append(BenchmarkRunResult(
            summary=summary,
            summary_generation_time=summary_generation_time,
            log_dir=log_dir,
            run_index=i + 1
        ))

        print(f"✅ Run {i + 1} complete — tickrun: {summary.batch_tickrun_time:.1f}s, warmup: {summary.batch_warmup_time:.1f}s")

    return results


@pytest.fixture(scope="session")
def benchmark_metrics(
    benchmark_execution_runs: List[BenchmarkRunResult]
) -> Dict[str, Any]:
    """
    Extract benchmark metrics from multiple runs using median.

    Args:
        benchmark_execution_runs: Results from all benchmark runs

    Returns:
        Dict with median metrics and raw measurements
    """
    runs = benchmark_execution_runs

    # Extract per-run values
    warmup_times = [r.summary.batch_warmup_time for r in runs]
    tickrun_times = [r.summary.batch_tickrun_time for r in runs]
    summary_times = [r.summary_generation_time for r in runs]

    # Calculate total ticks (should be identical across runs)
    total_ticks = sum(
        r.tick_loop_results.coordination_statistics.ticks_processed
        for r in runs[0].summary.process_result_list
        if r.tick_loop_results and r.tick_loop_results.coordination_statistics
    )

    tps_values = [
        total_ticks / r.summary.batch_tickrun_time
        for r in runs
        if r.summary.batch_tickrun_time > 0
    ]

    return {
        'ticks_per_second': statistics.median(tps_values),
        'tickrun_time_s': statistics.median(tickrun_times),
        'warmup_time_s': statistics.median(warmup_times),
        'summary_generation_time_s': statistics.median(summary_times),
        'total_ticks': total_ticks,
        'scenarios_count': len(runs[0].summary.process_result_list),
        'runs': len(runs),
        'raw_measurements': {
            'ticks_per_second': [round(v, 2) for v in tps_values],
            'tickrun_time_s': [round(v, 2) for v in tickrun_times],
            'warmup_time_s': [round(v, 2) for v in warmup_times],
            'summary_generation_time_s': [round(v, 2) for v in summary_times]
        }
    }


# =============================================================================
# REPORT GENERATION
# =============================================================================

@pytest.fixture(scope="session")
def benchmark_report(
    request,
    validated_system: str,
    benchmark_config: Dict[str, Any],
    baseline_metrics: Dict[str, Any],
    benchmark_metrics: Dict[str, Any],
    benchmark_execution_runs: List[BenchmarkRunResult],
    system_fingerprint: SystemFingerprint,
    debug_mode_detected: bool
) -> Dict[str, Any]:
    """
    Generate benchmark report with all metrics, deviations, and artifacts.

    Includes 3-run median values, raw measurements, and copied log artifacts.

    Returns:
        Complete benchmark report dict
    """
    release_version = request.config.getoption('release_version')
    tester_comment = request.config.getoption('comment')
    now = datetime.now(timezone.utc)
    validity_days = benchmark_config['certificate']['validity_days']
    valid_until = now + timedelta(days=validity_days)

    tolerances = benchmark_config['tolerances']

    # Build metrics array with deviations
    metrics_list: List[Dict[str, Any]] = []
    warnings: List[str] = []
    overall_status = 'PASSED'

    # Check debug mode FIRST - invalidates entire benchmark
    if debug_mode_detected:
        overall_status = 'FAILED'
        warnings.append(
            'DEBUGGER DETECTED: Benchmark results are INVALID. '
            'Run without debugger for valid measurements.'
        )

    for metric_name in ['ticks_per_second', 'tickrun_time_s', 'warmup_time_s']:
        measured = benchmark_metrics.get(metric_name, 0)
        reference = baseline_metrics.get(metric_name, 0)
        tolerance = tolerances.get(metric_name, {}).get('percent', 10.0)

        # Calculate deviation
        if reference > 0:
            deviation = ((measured - reference) / reference) * 100
        else:
            deviation = 0.0

        # Determine status
        abs_deviation = abs(deviation)
        if abs_deviation <= tolerance:
            status = 'PASSED'
        else:
            if deviation > 0:
                # Faster than baseline
                status = 'PASSED'
                warnings.append(
                    f"Performance {abs_deviation:.1f}% BETTER than baseline for {metric_name}. "
                    f"Consider updating baseline if code was optimized."
                )
            else:
                # Slower than baseline
                status = 'FAILED'
                overall_status = 'FAILED'

        metrics_list.append({
            'name': metric_name,
            'measured': round(measured, 2),
            'reference': reference,
            'deviation_percent': round(deviation, 2),
            'tolerance_percent': tolerance,
            'status': status
        })

    # Add summary generation time as INFO metric
    metrics_list.append({
        'name': 'summary_generation_time_s',
        'measured': round(benchmark_metrics.get('summary_generation_time_s', 0), 2),
        'reference': None,
        'deviation_percent': None,
        'tolerance_percent': None,
        'status': 'INFO'
    })

    # Add informational metrics (no tolerance check)
    for metric_name in ['total_ticks', 'scenarios_count']:
        measured = benchmark_metrics.get(metric_name, 0)
        reference = baseline_metrics.get(metric_name, 0)
        metrics_list.append({
            'name': metric_name,
            'measured': measured,
            'reference': reference,
            'deviation_percent': None,
            'tolerance_percent': None,
            'status': 'INFO'
        })

    # Copy log artifacts
    artifacts = _copy_benchmark_logs(benchmark_execution_runs)

    report = {
        'release_version': release_version,
        'timestamp': now.isoformat(),
        'valid_until': valid_until.isoformat(),
        'git_commit': get_git_commit(),
        'system_id': validated_system,
        'system_details': {
            'cpu_model': system_fingerprint.cpu_model,
            'cpu_cores': system_fingerprint.cpu_cores,
            'ram_total_gb': round(system_fingerprint.ram_total_gb, 1),
            'platform': system_fingerprint.platform
        },
        'scenario': benchmark_config['scenario'],
        'runs': benchmark_metrics.get('runs', 1),
        'comment': tester_comment,
        'debug_mode_detected': debug_mode_detected,
        'overall_status': overall_status,
        'metrics': metrics_list,
        'raw_measurements': benchmark_metrics.get('raw_measurements', {}),
        'artifacts': artifacts,
        'warnings': warnings
    }

    return report


def _copy_benchmark_logs(runs: List[BenchmarkRunResult]) -> List[Dict[str, str]]:
    """
    Copy benchmark log files to reports directory for archival.

    Copies scenario_summary.log and scenario_global_log.log from each run
    into tests/benchmark/reports/logs/run_N/.

    Args:
        runs: List of benchmark run results with log directories

    Returns:
        List of artifact dicts with source, destination, copied_at
    """
    artifacts: List[Dict[str, str]] = []
    logs_dir = BENCHMARK_REPORTS_DIR / 'logs'

    for run in runs:
        dest_dir = logs_dir / f'run_{run.run_index}'
        dest_dir.mkdir(parents=True, exist_ok=True)

        for log_file in ['scenario_summary.log', 'scenario_global_log.log']:
            src = Path(run.log_dir) / log_file
            if src.exists():
                dst = dest_dir / log_file
                shutil.copy2(src, dst)
                artifacts.append({
                    'source': str(src),
                    'destination': str(dst),
                    'copied_at': datetime.now(timezone.utc).isoformat()
                })

    return artifacts


def _save_benchmark_report(report: Dict[str, Any]) -> Path:
    """
    Save benchmark report to reports directory.

    Filename includes release version for traceability.

    Args:
        report: Complete benchmark report

    Returns:
        Path to saved report file
    """
    BENCHMARK_REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.fromisoformat(report['timestamp'])
    date_str = timestamp.strftime('%Y-%m-%d_%H%M%S')

    version_str = re.sub(r'[^a-zA-Z0-9._-]', '_', report['release_version'])
    filename = f'benchmark_report_{version_str}_{date_str}.json'
    filepath = BENCHMARK_REPORTS_DIR / filename

    with open(filepath, 'w') as f:
        json.dump(report, f, indent=2)

    return filepath


# =============================================================================
# REPORT SAVING (Called explicitly by test_zz_save_report)
# =============================================================================

# NOTE: We intentionally do NOT use autouse=True here.
# Reason: When running only test_benchmark_certificate.py, we don't want
# to trigger the full benchmark execution just to save a report.
# The report is saved explicitly by test_zz_save_report in
# test_throughput_regression.py which runs last (alphabetically).
