"""
FiniexTestingIDE - MVP Benchmark Throughput Tests
Performance regression tests against registered system baselines

Tests:
- Ticks per second within tolerance
- Tick run time within tolerance  
- Warmup time within tolerance (larger tolerance for IO variance)

IMPORTANT: These tests only run on registered systems.
Unregistered systems will cause pytest.fail() in the validated_system fixture.
"""

import pytest
from typing import Dict, Any


class TestThroughputRegression:
    """
    Performance regression tests comparing against baseline.

    All tests depend on validated_system fixture which ensures
    the current system is registered in reference_systems.json.
    """

    def test_ticks_per_second(
        self,
        benchmark_metrics: Dict[str, float],
        baseline_metrics: Dict[str, Any],
        benchmark_config: Dict[str, Any],
        benchmark_report: Dict[str, Any]
    ):
        """
        Ticks per second should be within tolerance of baseline.

        This is the primary throughput metric - CPU bound.
        """
        measured = benchmark_metrics["ticks_per_second"]
        reference = baseline_metrics["ticks_per_second"]
        tolerance = benchmark_config["tolerances"]["ticks_per_second"]["percent"]

        deviation = ((measured - reference) / reference) * \
            100 if reference > 0 else 0

        # Check if within tolerance (slower = fail, faster = pass with warning)
        if deviation < -tolerance:
            pytest.fail(
                f"Throughput regression detected!\n"
                f"Measured: {measured:,.0f} ticks/s\n"
                f"Baseline: {reference:,.0f} ticks/s\n"
                f"Deviation: {deviation:+.1f}% (tolerance: ±{tolerance}%)\n"
                f"\n"
                f"Possible causes:\n"
                f"- CPU throttling or background load\n"
                f"- Algorithm regression in worker/decision logic\n"
                f"- Memory pressure"
            )

        # Pass - log result
        status = "✅ PASSED"
        if deviation > tolerance:
            status = "✅ PASSED (faster than baseline - consider updating)"

        print(f"\n{status}")
        print(f"Ticks/second: {measured:,.0f} (baseline: {reference:,.0f})")
        print(f"Deviation: {deviation:+.1f}%")

    def test_tickrun_time(
        self,
        benchmark_metrics: Dict[str, float],
        baseline_metrics: Dict[str, Any],
        benchmark_config: Dict[str, Any]
    ):
        """
        Tick run time should be within tolerance of baseline.

        Measures actual tick processing duration (excludes warmup).
        """
        measured = benchmark_metrics["tickrun_time_s"]
        reference = baseline_metrics["tickrun_time_s"]
        tolerance = benchmark_config["tolerances"]["tickrun_time_s"]["percent"]

        deviation = ((measured - reference) / reference) * \
            100 if reference > 0 else 0

        # For time: positive deviation means SLOWER (bad)
        if deviation > tolerance:
            hints = benchmark_config["failure_hints"]["tickrun_slow"]
            pytest.fail(
                f"Tick run time regression detected!\n"
                f"Measured: {measured:.1f}s\n"
                f"Baseline: {reference:.1f}s\n"
                f"Deviation: {deviation:+.1f}% (tolerance: ±{tolerance}%)\n"
                f"\n" + "\n".join(hints)
            )

        status = "✅ PASSED"
        if deviation < -tolerance:
            status = "✅ PASSED (faster than baseline)"

        print(f"\n{status}")
        print(f"Tick run time: {measured:.1f}s (baseline: {reference:.1f}s)")
        print(f"Deviation: {deviation:+.1f}%")

    def test_warmup_time(
        self,
        benchmark_metrics: Dict[str, float],
        baseline_metrics: Dict[str, Any],
        benchmark_config: Dict[str, Any]
    ):
        """
        Warmup time should be within tolerance of baseline.

        Larger tolerance (15%) due to IO variance (disk, caching).
        """
        measured = benchmark_metrics["warmup_time_s"]
        reference = baseline_metrics["warmup_time_s"]
        tolerance = benchmark_config["tolerances"]["warmup_time_s"]["percent"]

        deviation = ((measured - reference) / reference) * \
            100 if reference > 0 else 0

        # For time: positive deviation means SLOWER (bad)
        if deviation > tolerance:
            hints = benchmark_config["failure_hints"]["warmup_slow"]
            pytest.fail(
                f"Warmup time regression detected!\n"
                f"Measured: {measured:.1f}s\n"
                f"Baseline: {reference:.1f}s\n"
                f"Deviation: {deviation:+.1f}% (tolerance: ±{tolerance}%)\n"
                f"\n" + "\n".join(hints)
            )

        status = "✅ PASSED"
        if deviation < -tolerance:
            status = "✅ PASSED (faster than baseline)"

        print(f"\n{status}")
        print(f"Warmup time: {measured:.1f}s (baseline: {reference:.1f}s)")
        print(f"Deviation: {deviation:+.1f}%")


class TestBenchmarkExecution:
    """
    Basic execution validation tests.

    Ensures the benchmark ran successfully before checking performance.
    """

    def test_all_scenarios_successful(
        self,
        benchmark_execution_summary
    ):
        """All benchmark scenarios should complete successfully."""
        failed = [
            r.scenario_name
            for r in benchmark_execution_summary.process_result_list
            if not r.success
        ]

        assert len(failed) == 0, (
            f"{len(failed)} scenarios failed: {failed[:5]}..."
            if len(failed) > 5 else f"Scenarios failed: {failed}"
        )

    def test_tick_count_matches(
        self,
        benchmark_metrics: Dict[str, float],
        baseline_metrics: Dict[str, Any]
    ):
        """
        Total tick count should match baseline.

        This validates that the same data was processed.
        Mismatch indicates config or data change.
        """
        measured = benchmark_metrics["total_ticks"]
        reference = baseline_metrics["total_ticks"]

        assert measured == reference, (
            f"Tick count mismatch!\n"
            f"Measured: {measured:,}\n"
            f"Baseline: {reference:,}\n"
            f"This indicates the benchmark scenario or data has changed."
        )

    def test_scenario_count_matches(
        self,
        benchmark_metrics: Dict[str, float],
        baseline_metrics: Dict[str, Any]
    ):
        """
        Scenario count should match baseline.

        Ensures same number of scenarios were executed.
        """
        measured = benchmark_metrics["scenarios_count"]
        reference = baseline_metrics["scenarios_count"]

        assert measured == reference, (
            f"Scenario count mismatch!\n"
            f"Measured: {measured}\n"
            f"Baseline: {reference}\n"
            f"The benchmark scenario configuration has changed."
        )
