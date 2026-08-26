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

import json
import re
import shutil
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.framework.batch.batch_orchestrator import BatchOrchestrator
from python.framework.batch.batch_report_coordinator import BatchReportCoordinator
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.scenario_types.scenario_set_types import ScenarioSet
from python.scenario.scenario_config_loader import ScenarioConfigLoader
from tests.simulation.benchmark.system_fingerprint import (
    SystemFingerprint,
    find_matching_system,
    get_git_commit,
    get_system_fingerprint,
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

def _read_persisted_profiling(log_dir: Path) -> Dict[str, Any]:
    """
    Read one run's persisted profiling section.

    Reads the ARTIFACT rather than re-aggregating: the reporting pipeline already derives this
    once and persists it, and re-deriving it here would be a second computation that can drift
    from the one the operator reads in the summary.

    Args:
        log_dir: The run's log directory

    Returns:
        The parsed profiling section, or an empty dict when the run did not write one
    """
    path = Path(log_dir) / 'io' / 'profiling.json'
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return {}


def _median_by_key(per_run: List[Dict[str, float]]) -> Dict[str, float]:
    """
    Median per key across runs, over the keys every run reported.

    Intersecting rather than unioning is deliberate: a key that only some runs produced has no
    median worth printing, and silently filling it from fewer samples is how a breakdown starts
    lying about its own basis.

    Args:
        per_run: One mapping per run

    Returns:
        Median value per shared key
    """
    if not per_run:
        return {}
    shared = set(per_run[0])
    for mapping in per_run[1:]:
        shared &= set(mapping)
    return {key: statistics.median([mapping[key] for mapping in per_run])
            for key in sorted(shared)}


def _spread_percent(values: List[float]) -> Optional[float]:
    """
    Spread of a run series, as a percentage of its smallest value.

    Args:
        values: The raw measurements of one metric

    Returns:
        The spread, or None when there is nothing to compare (fewer than two runs)
    """
    usable = [v for v in values if isinstance(v, (int, float)) and v > 0]
    if len(usable) < 2:
        return None
    return (max(usable) - min(usable)) / min(usable) * 100.0


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

BENCHMARK_CONFIG_DIR = Path(__file__).parent / 'config'
BENCHMARK_REPORTS_DIR = Path(__file__).parent / 'reports'


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

@pytest.fixture(scope='session')
def benchmark_config() -> Dict[str, Any]:
    """
    Load benchmark configuration.

    Returns:
        Parsed benchmark_config.json
    """
    config_path = BENCHMARK_CONFIG_DIR / 'benchmark_config.json'
    with open(config_path, 'r') as f:
        return json.load(f)


@pytest.fixture(scope='session')
def reference_systems() -> Dict[str, Any]:
    """
    Load reference systems configuration.

    Returns:
        Parsed reference_systems.json
    """
    config_path = BENCHMARK_CONFIG_DIR / 'reference_systems.json'
    with open(config_path, 'r') as f:
        return json.load(f)


# =============================================================================
# SYSTEM VALIDATION FIXTURES
# =============================================================================

@pytest.fixture(scope='session')
def system_fingerprint() -> SystemFingerprint:
    """
    Get current system fingerprint.

    Returns:
        SystemFingerprint with hardware details
    """
    return get_system_fingerprint()


@pytest.fixture(scope='session')
def debug_mode_detected() -> bool:
    """
    Check if debugger is attached.

    This fixture runs early to detect debug mode before
    expensive benchmark operations.

    Returns:
        True if debugger detected, False otherwise
    """
    return is_debugger_attached()


@pytest.fixture(scope='session')
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


@pytest.fixture(scope='session')
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
    system_config = reference_systems['systems'][validated_system]
    return system_config['baseline']['metrics']


# =============================================================================
# BENCHMARK EXECUTION FIXTURE
# =============================================================================

@pytest.fixture(scope='session')
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
        print(f'🔄 Benchmark Run {i + 1}/{num_runs}')
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

        print(f'✅ Run {i + 1} complete — tickrun: {summary.batch_tickrun_time:.1f}s, warmup: {summary.batch_warmup_time:.1f}s')

    return results


@pytest.fixture(scope='session')
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

    # Per-stage breakdown, read from each run's persisted profiling artifact. INFO only: it
    # exists so a future drift has an ADDRESS, not so a pipeline change can turn a release red.
    profilings = [_read_persisted_profiling(r.log_dir) for r in runs]
    operations = _median_by_key([
        {row['operation']: row.get('avg_time_ms', 0.0)
         for row in (prof.get('aggregate') or {}).get('avg_operation_times', [])}
        for prof in profilings])
    warmup_phases = _median_by_key([
        {row['name']: row.get('duration_s', 0.0)
         for row in prof.get('warmup_phases', [])}
        for prof in profilings])

    return {
        'ticks_per_second': statistics.median(tps_values),
        'tickrun_time_s': statistics.median(tickrun_times),
        'warmup_time_s': statistics.median(warmup_times),
        'summary_generation_time_s': statistics.median(summary_times),
        'operation_avg_ms': operations,
        'warmup_phase_s': warmup_phases,
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

@pytest.fixture(scope='session')
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
    # The version the TREE says, beside the one the operator typed. `--release-version` is a
    # label; app_config is a measurement. Recording only the label is how a certificate ends
    # up naming a release it was not taken from — the same distinction as a producer's commit
    # against its version string.
    app_version = AppConfigManager().get_version()
    now = datetime.now(timezone.utc)
    validity_days = benchmark_config['certificate']['validity_days']
    valid_until = now + timedelta(days=validity_days)

    tolerances = benchmark_config['tolerances']

    # Build metrics array with deviations
    metrics_list: List[Dict[str, Any]] = []
    warnings: List[str] = []
    overall_status = 'PASSED'

    # A DECLARED release must match the tree. 'dev' declares nothing, so it is exempt.
    if release_version != 'dev' and release_version != app_version:
        overall_status = 'FAILED'
        warnings.append(
            f"VERSION MISMATCH: certifying '{release_version}' from a tree that says "
            f"'{app_version}' (configs/app_config.json). Bump the version before taking the "
            f'certificate, or the artifact names a release it did not measure.')

    # Check debug mode FIRST - invalidates entire benchmark
    if debug_mode_detected:
        overall_status = 'FAILED'
        warnings.append(
            'DEBUGGER DETECTED: Benchmark results are INVALID. '
            'Run without debugger for valid measurements.'
        )

    # Stability BEFORE tolerance: a median computed from runs that disagree is not a
    # measurement, so believing or disbelieving it is equally unfounded. Same lesson as the
    # F401 sweep — an instrument that could not answer produces a clean-looking result.
    raw = benchmark_metrics.get('raw_measurements', {})
    throughput_runs = raw.get('ticks_per_second') or []
    max_spread = benchmark_config.get('stability', {}).get('max_spread_percent', 15.0)
    spread = _spread_percent(throughput_runs)
    if spread is not None:
        stable = spread <= max_spread
        if not stable:
            overall_status = 'FAILED'
            warnings.append(
                f'UNSTABLE MEASUREMENT: the {len(throughput_runs)} raw throughput runs span '
                f'{spread:.1f}% (limit {max_spread:.1f}%). The median is not a measurement of '
                f'the code. Re-run on an idle machine before reading any deviation below.')
        metrics_list.append({
            'name': 'throughput_spread_percent',
            'measured': round(spread, 1),
            'reference': None,
            'deviation_percent': None,
            'tolerance_percent': max_spread,
            'status': 'PASSED' if stable else 'FAILED'
        })

    for metric_name in ['ticks_per_second', 'tickrun_time_s', 'warmup_time_s']:
        measured = benchmark_metrics.get(metric_name, 0)
        reference = baseline_metrics.get(metric_name, 0)
        tolerance = tolerances.get(metric_name, {}).get('percent', 10.0)

        # Calculate deviation
        if reference > 0:
            deviation = ((measured - reference) / reference) * 100
        else:
            deviation = 0.0

        # Determine status — direction-aware, mirroring the gate in
        # test_throughput_regression.py: ticks_per_second is higher-is-better, the
        # *_time_s metrics are lower-is-better (a longer time is a regression, not a gain).
        abs_deviation = abs(deviation)
        higher_is_better = metric_name == 'ticks_per_second'
        improved = deviation > 0 if higher_is_better else deviation < 0
        if abs_deviation <= tolerance:
            status = 'PASSED'
        elif improved:
            # Better than baseline
            status = 'PASSED'
            warnings.append(
                f'Performance {abs_deviation:.1f}% BETTER than baseline for {metric_name}. '
                f'Consider updating baseline if code was optimized.'
            )
        else:
            # Worse than baseline — regression
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

    # Summary generation: a CEILING, not a tolerance. It has no per-system baseline, and the
    # point is not a performance target — it is that an untoleranced INFO metric grew 28x
    # (0.12s -> 3.4s) without anyone noticing.
    summary_seconds = benchmark_metrics.get('summary_generation_time_s', 0)
    summary_ceiling = (benchmark_config.get('ceilings', {})
                       .get('summary_generation_time_s', {}).get('max_seconds'))
    summary_status = 'INFO'
    if summary_ceiling is not None:
        summary_status = 'PASSED' if summary_seconds <= summary_ceiling else 'FAILED'
        if summary_status == 'FAILED':
            overall_status = 'FAILED'
            warnings.append(
                f'Report generation took {summary_seconds:.2f}s, above the {summary_ceiling:.1f}s '
                f'ceiling. Once per batch, not in the tick loop — so this is a reporting-pipeline '
                f'cost, not a throughput regression. Raise the ceiling deliberately or find it.')
    metrics_list.append({
        'name': 'summary_generation_time_s',
        'measured': round(summary_seconds, 2),
        'reference': None,
        'deviation_percent': None,
        'tolerance_percent': summary_ceiling,
        'status': summary_status
    })

    # The two anchors. Chosen because their MEANING survives a refactor and each dominates
    # its phase: worker_decision is ~79% of tick cost, tick-parquet loading ~81% of warmup.
    # Recorded, never gated — a pipeline change must not turn a release red.
    operations = benchmark_metrics.get('operation_avg_ms', {})
    warmup_phases = benchmark_metrics.get('warmup_phase_s', {})
    for label, value in (
        ('worker_decision_avg_ms', operations.get('worker_decision')),
        ('warmup_ticks_parquet_s', next(
            (v for k, v in warmup_phases.items() if 'Ticks' in k), None)),
    ):
        if value is None:
            continue
        metrics_list.append({
            'name': label,
            'measured': round(value, 4),
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
        'app_version': app_version,
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
        # The breakdown, with the SHAPE it was measured in. The shape is not decoration: the
        # operation names are free strings, and `worker_decision` has already absorbed another
        # operation once (see process_tick_loop.py). A comparison across reports must check the
        # name sets FIRST and report +new / -gone, because diffing values across two different
        # definitions produces a number that looks like a regression and is not.
        'breakdown': {
            'operation_avg_ms': {k: round(v, 4) for k, v in
                                 benchmark_metrics.get('operation_avg_ms', {}).items()},
            'warmup_phase_s': {k: round(v, 4) for k, v in
                               benchmark_metrics.get('warmup_phase_s', {}).items()},
            'shape': {
                'operations': sorted(benchmark_metrics.get('operation_avg_ms', {})),
                'warmup_phases': sorted(benchmark_metrics.get('warmup_phase_s', {})),
            },
        },
        'artifacts': artifacts,
        'warnings': warnings
    }

    return report


def _copy_benchmark_logs(runs: List[BenchmarkRunResult]) -> List[Dict[str, str]]:
    """
    Copy benchmark log files to reports directory for archival.

    Copies scenario_summary.log and scenario_global_log.log from each run
    into tests/simulation/benchmark/reports/logs/run_N/.

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
