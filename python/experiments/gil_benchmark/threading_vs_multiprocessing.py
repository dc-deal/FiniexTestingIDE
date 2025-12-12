"""
FiniexTestingIDE - Extended GIL Benchmark Experiment
====================================================
Scientific Field Study: Threading vs Multiprocessing for CPU-bound Work

EXTENDED VERSION:
- Variable Workload Sizes (5ms to 50ms)
- Shared State Contention Simulation
- Realistic bar_history Structures
- Multiple Workers per Scenario
- Comprehensive Environment Detection
- Automatic Log File with Intelligent Naming

Goal: Realistic Simulation of FiniexTestingIDE Scenario Parallelization
"""

import time
import numpy as np
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from typing import List, Dict, Any, Tuple
import multiprocessing
from dataclasses import dataclass
import platform
import sys
import os
import threading
import psutil
from datetime import datetime, timezone
import re


class LogWriter:
    """
    Writes output to both console and log file.
    Automatically creates intelligent filenames.
    """

    def __init__(self, script_dir: str):
        self.script_dir = script_dir
        self.log_file = None
        self.log_path = None

    def start_logging(self, platform_name: str, execution_mode: str):
        """
        Starts logging to file with intelligent name.

        Format: gil_benchmark_<platform>_<mode>_<date>.log
        Example: gil_benchmark_windows_native_20250118-1430.log
        """
        # Sanitize platform name
        safe_platform = re.sub(r'[^\w\-]', '_', platform_name.lower())
        safe_mode = re.sub(r'[^\w\-]', '_', execution_mode.lower())

        # Timestamp
        timestamp = datetime.now(timezone.utc) .strftime('%Y%m%d-%H%M%S')

        # Filename
        filename = f"gil_benchmark_{safe_platform}_{safe_mode}_{timestamp}.log"
        self.log_path = os.path.join(self.script_dir, filename)

        # Open file
        self.log_file = open(self.log_path, 'w', encoding='utf-8')

        # Write header
        self.write(f"GIL Benchmark Log File")
        self.write(
            f"Generated: {datetime.now(timezone.utc) .strftime('%Y-%m-%d %H:%M:%S')}")
        self.write(f"Platform: {platform_name}")
        self.write(f"Execution Mode: {execution_mode}")
        self.write(f"Log File: {filename}")
        self.write(f"{'='*80}\n")

    def write(self, message: str):
        """Writes message to console and file"""
        print(message)
        if self.log_file:
            self.log_file.write(message + '\n')
            self.log_file.flush()

    def close(self):
        """Closes log file"""
        if self.log_file:
            self.write(f"\n{'='*80}")
            self.write(f"📁 Full log saved to:")
            self.write(f"   {self.log_path}")
            self.write(f"{'='*80}")
            self.log_file.close()


@dataclass
class BenchmarkResult:
    """Single benchmark result - compact for clear logging"""
    method: str
    workload_ms: float
    scenario_type: str
    total_time: float
    speedup: float
    efficiency: float


class EnvironmentDetector:
    """Detects and logs the complete execution environment"""

    @staticmethod
    def detect_execution_context() -> Dict[str, Any]:
        """Detects how the script was invoked"""
        context = {
            'execution_mode': 'unknown',
            'is_docker': False,
            'is_vscode_remote': False,
            'is_devcontainer': False,
        }

        # Check for Docker
        if os.path.exists('/.dockerenv'):
            context['is_docker'] = True
            context['execution_mode'] = 'docker_container'

        # Check for VS Code Remote/Devcontainer
        if 'VSCODE_IPC_HOOK_CLI' in os.environ or 'REMOTE_CONTAINERS' in os.environ:
            context['is_vscode_remote'] = True
            if 'REMOTE_CONTAINERS' in os.environ:
                context['is_devcontainer'] = True
                context['execution_mode'] = 'vscode_devcontainer'

        # Plain Python execution detection
        if not context['is_docker'] and not context['is_vscode_remote']:
            context['execution_mode'] = 'native_python'

        return context

    @staticmethod
    def get_system_info() -> Dict[str, Any]:
        """Collects detailed system information"""
        info = {
            'platform': platform.platform(),
            'system': platform.system(),
            'python_version': platform.python_version(),
            'cpu_count_logical': os.cpu_count(),
            'cpu_count_physical': psutil.cpu_count(logical=False),
            'memory_total_gb': round(psutil.virtual_memory().total / (1024**3), 2),
            'numpy_version': np.__version__,
        }
        return info

    @staticmethod
    def print_environment_report(logger):
        """Prints compact environment report"""
        logger.write("="*80)
        logger.write("ENVIRONMENT REPORT")
        logger.write("="*80)

        context = EnvironmentDetector.detect_execution_context()
        sys_info = EnvironmentDetector.get_system_info()

        logger.write(f"Execution Mode:    {context['execution_mode']}")
        logger.write(f"Docker Container:  {context['is_docker']}")
        logger.write(f"Platform:          {sys_info['platform']}")
        logger.write(f"Python:            {sys_info['python_version']}")
        logger.write(f"NumPy:             {sys_info['numpy_version']}")
        logger.write(
            f"CPU Cores:         {sys_info['cpu_count_physical']} physical / {sys_info['cpu_count_logical']} logical")
        logger.write(f"RAM:               {sys_info['memory_total_gb']} GB")
        logger.write("="*80)

        return context, sys_info


class RealisticScenarioSimulator:
    """Simulates a realistic FiniexTestingIDE scenario"""

    @staticmethod
    def simulate_scenario(
        scenario_id: int,
        workload_ms: float,
        num_workers: int = 2,
        bar_history_size: int = 200
    ) -> Dict[str, Any]:
        """Simulates complete scenario with multiple workers"""
        start = time.perf_counter()

        # Simulate bar_history for multiple timeframes
        bar_history = {
            'M5': np.random.uniform(1.0, 2.0, bar_history_size),
            'M15': np.random.uniform(1.0, 2.0, bar_history_size // 3),
            'H1': np.random.uniform(1.0, 2.0, bar_history_size // 12),
        }

        # Simulate multiple workers
        for worker_id in range(num_workers):
            RealisticScenarioSimulator._simulate_worker(
                bar_history=bar_history,
                workload_ms=workload_ms / num_workers
            )

        total_time = (time.perf_counter() - start) * 1000
        return {'scenario_id': scenario_id, 'total_time_ms': total_time}

    @staticmethod
    def _simulate_worker(bar_history: Dict[str, np.ndarray], workload_ms: float) -> None:
        """Simulates a worker with RSI-like calculations"""
        start = time.perf_counter()
        target_duration = workload_ms / 1000.0

        prices = bar_history['M5']

        while (time.perf_counter() - start) < target_duration:
            # RSI-like calculations (NumPy releases GIL)
            deltas = np.diff(prices)
            gains = np.where(deltas > 0, deltas, 0)
            losses = np.where(deltas < 0, -deltas, 0)

            avg_gain = np.mean(gains[-14:])
            avg_loss = np.mean(losses[-14:])

            if avg_loss != 0:
                rs = avg_gain / avg_loss
                _ = 100.0 - (100.0 / (1.0 + rs))

            # Additional NumPy ops
            matrix = np.random.rand(20, 20)
            result = np.dot(matrix, matrix.T)
            _ = np.linalg.eigvals(result)


class ExtendedGILBenchmark:
    """Extended GIL benchmark with realistic scenarios"""

    def __init__(self, logger, num_scenarios: int = 20):
        self.logger = logger
        self.num_scenarios = num_scenarios
        self.results: List[BenchmarkResult] = []

    def run_test_suite(self, workload_ms: float, scenario_type: str):
        """Runs complete test suite"""
        self.logger.write(f"\n{'='*80}")
        self.logger.write(
            f"TEST: {scenario_type} (Workload: {workload_ms}ms per scenario)")
        self.logger.write(f"{'='*80}")

        # Sequential
        seq = self._run_sequential(workload_ms, scenario_type)
        self.results.append(seq)

        # Threading
        thr = self._run_threading(workload_ms, scenario_type, seq.total_time)
        self.results.append(thr)

        # Multiprocessing
        mp = self._run_multiprocessing(
            workload_ms, scenario_type, seq.total_time)
        self.results.append(mp)

        # Compact summary
        self.logger.write(f"\nResults:")
        self.logger.write(f"  Sequential:      {seq.total_time:.3f}s")
        self.logger.write(
            f"  Threading:       {thr.total_time:.3f}s  ({thr.speedup:.2f}x speedup, {thr.efficiency:.0%} eff)")
        self.logger.write(
            f"  Multiprocessing: {mp.total_time:.3f}s  ({mp.speedup:.2f}x speedup, {mp.efficiency:.0%} eff)")

        # Quick analysis
        winner = "Threading" if thr.speedup > mp.speedup else "Multiprocessing"
        margin = abs(thr.speedup - mp.speedup) / max(thr.speedup, mp.speedup)

        if margin < 0.2:
            self.logger.write(f"  → COMPARABLE performance (< 20% difference)")
        else:
            self.logger.write(f"  → {winner} WINS by {margin:.0%}")

    def _run_sequential(self, workload_ms: float, scenario_type: str) -> BenchmarkResult:
        """Sequential execution"""
        start = time.perf_counter()
        for i in range(self.num_scenarios):
            _ = RealisticScenarioSimulator.simulate_scenario(i, workload_ms)
        total_time = time.perf_counter() - start

        return BenchmarkResult(
            method="Sequential",
            workload_ms=workload_ms,
            scenario_type=scenario_type,
            total_time=total_time,
            speedup=1.0,
            efficiency=1.0
        )

    def _run_threading(self, workload_ms: float, scenario_type: str, baseline_time: float) -> BenchmarkResult:
        """Threading execution"""
        max_workers = min(self.num_scenarios, multiprocessing.cpu_count())
        start = time.perf_counter()

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(
                    RealisticScenarioSimulator.simulate_scenario, i, workload_ms)
                for i in range(self.num_scenarios)
            ]
            _ = [f.result() for f in futures]

        total_time = time.perf_counter() - start
        speedup = baseline_time / total_time

        return BenchmarkResult(
            method=f"Threading-{max_workers}w",
            workload_ms=workload_ms,
            scenario_type=scenario_type,
            total_time=total_time,
            speedup=speedup,
            efficiency=speedup / max_workers
        )

    def _run_multiprocessing(self, workload_ms: float, scenario_type: str, baseline_time: float) -> BenchmarkResult:
        """Multiprocessing execution"""
        max_workers = min(self.num_scenarios, multiprocessing.cpu_count())
        start = time.perf_counter()

        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(
                    RealisticScenarioSimulator.simulate_scenario, i, workload_ms)
                for i in range(self.num_scenarios)
            ]
            _ = [f.result() for f in futures]

        total_time = time.perf_counter() - start
        speedup = baseline_time / total_time

        return BenchmarkResult(
            method=f"Multiprocessing-{max_workers}w",
            workload_ms=workload_ms,
            scenario_type=scenario_type,
            total_time=total_time,
            speedup=speedup,
            efficiency=speedup / max_workers
        )

    def print_final_summary(self):
        """Final compact summary"""
        self.logger.write(f"\n{'='*80}")
        self.logger.write("FINAL SUMMARY")
        self.logger.write(f"{'='*80}")

        # Group by workload
        workloads = sorted(set(r.workload_ms for r in self.results))

        self.logger.write(
            f"\n{'Workload':<12} {'Sequential':<12} {'Threading':<20} {'Multiproc':<20}")
        self.logger.write(f"{'-'*80}")

        for wl in workloads:
            results_wl = [r for r in self.results if r.workload_ms == wl]
            seq = [r for r in results_wl if "Sequential" in r.method][0]
            thr = [r for r in results_wl if "Threading" in r.method][0]
            mp = [r for r in results_wl if "Multiprocessing" in r.method][0]

            self.logger.write(
                f"{wl:>5.0f}ms     "
                f"{seq.total_time:>7.3f}s     "
                f"{thr.total_time:>7.3f}s ({thr.speedup:>4.1f}x)   "
                f"{mp.total_time:>7.3f}s ({mp.speedup:>4.1f}x)"
            )

        self.logger.write(f"\n{'='*80}")
        self.logger.write("RECOMMENDATION")
        self.logger.write(f"{'='*80}")

        # Find best approach across all workloads
        thr_results = [r for r in self.results if "Threading" in r.method]
        mp_results = [r for r in self.results if "Multiprocessing" in r.method]

        avg_thr_speedup = sum(
            r.speedup for r in thr_results) / len(thr_results)
        avg_mp_speedup = sum(r.speedup for r in mp_results) / len(mp_results)

        self.logger.write(f"Average Speedup:")
        self.logger.write(f"  Threading:       {avg_thr_speedup:.2f}x")
        self.logger.write(f"  Multiprocessing: {avg_mp_speedup:.2f}x")

        if avg_thr_speedup > avg_mp_speedup * 1.2:
            self.logger.write(f"\n✅ RECOMMENDATION: Keep ThreadPoolExecutor")
            self.logger.write(f"   Threading performs better on this system")
            self.logger.write(
                f"   Likely due to: NumPy GIL release + low process spawn overhead")
        elif avg_mp_speedup > avg_thr_speedup * 1.2:
            self.logger.write(
                f"\n✅ RECOMMENDATION: Switch to ProcessPoolExecutor")
            self.logger.write(f"   Multiprocessing shows clear advantage")
            self.logger.write(
                f"   Expected improvement: {(avg_mp_speedup / avg_thr_speedup - 1) * 100:.0f}%")
        else:
            self.logger.write(f"\n⚖️  RECOMMENDATION: Results are comparable")
            self.logger.write(
                f"   Consider other factors: memory usage, code complexity")


def run_extended_benchmark():
    """Main function for extended benchmark"""

    # Initialize logger
    script_dir = os.path.dirname(os.path.abspath(__file__))
    logger = LogWriter(script_dir)

    logger.write("="*80)
    logger.write("EXTENDED GIL BENCHMARK - SCIENTIFIC FIELD STUDY")
    logger.write("="*80)
    logger.write(
        "\nRealistic Simulation of FiniexTestingIDE Scenario Parallelization")
    logger.write(
        f"Timestamp: {datetime.now(timezone.utc) .strftime('%Y-%m-%d %H:%M:%S')}\n")

    # Environment Detection
    context, sys_info = EnvironmentDetector.print_environment_report(logger)

    # Start File Logging
    logger.start_logging(
        platform_name=sys_info['system'],
        execution_mode=context['execution_mode']
    )

    logger.write("\nBENCHMARK CONFIGURATION")
    logger.write(f"{'='*80}")
    logger.write(f"Scenarios:            20")
    logger.write(f"Workers per Scenario: 2")
    logger.write(f"Bar History Size:     200 bars")
    logger.write(f"Test Variations:      4 workload sizes")
    logger.write(f"{'='*80}\n")

    input("Press ENTER to start benchmark (takes ~2-3 minutes)...")

    # Create benchmark
    benchmark = ExtendedGILBenchmark(logger, num_scenarios=20)

    # Run tests with different workloads
    benchmark.run_test_suite(workload_ms=5.0, scenario_type="Light-5ms")
    benchmark.run_test_suite(workload_ms=15.0, scenario_type="Medium-15ms")
    benchmark.run_test_suite(workload_ms=30.0, scenario_type="Heavy-30ms")
    benchmark.run_test_suite(workload_ms=50.0, scenario_type="VeryHeavy-50ms")

    # Final summary
    benchmark.print_final_summary()

    logger.write(f"\n{'='*80}")
    logger.write("BENCHMARK COMPLETE")
    logger.write(f"{'='*80}\n")

    # Close logger
    logger.close()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    run_extended_benchmark()
