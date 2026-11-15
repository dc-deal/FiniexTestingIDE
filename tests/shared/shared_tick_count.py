"""
FiniexTestingIDE - Shared Tick Count Tests
Reusable test classes for tick count validation across test suites.

Used by: baseline, multi_position, margin_validation
Import these classes into suite-specific test_<suite>_tick_count.py files.
"""

from typing import Dict

from python.framework.types.probe_metadata_types import ProbeMetadata
from python.framework.types.process_data_types import ProcessTickLoopResult


class TestTickCount:
    """Tests for tick count validation."""

    def test_tick_count_matches_config(
        self,
        tick_loop_results: ProcessTickLoopResult,
        scenario_config: Dict
    ):
        """Every tick the scenario configured reaches the algo path — the passes count the
        unclipped ticks of their slices, so a slice of several ticks counts each (#460)."""
        expected_ticks = scenario_config['scenarios'][0]['max_ticks']
        actual_ticks = tick_loop_results.coordination_statistics.ticks_processed

        assert actual_ticks == expected_ticks, (
            f'Expected {expected_ticks} ticks, got {actual_ticks}'
        )

    def test_the_probe_is_called_once_per_algo_pass(
        self,
        tick_loop_results: ProcessTickLoopResult,
        probe_metadata: ProbeMetadata
    ):
        """The probe counts its calls: one per algo pass, and a slice of ticks that arrived in
        one millisecond is one pass (#460)."""
        algo_passes = tick_loop_results.coordination_statistics.algo_passes

        assert probe_metadata.tick_count == algo_passes, (
            f'Probe calls {probe_metadata.tick_count} != algo passes {algo_passes}'
        )

    def test_decision_count_matches_passes(
        self,
        tick_loop_results: ProcessTickLoopResult,
    ):
        """One decision per algo pass."""
        decision_count = tick_loop_results.decision_statistics.decision_count
        algo_passes = tick_loop_results.coordination_statistics.algo_passes

        assert decision_count == algo_passes, (
            f'Decision count {decision_count} != algo passes {algo_passes}'
        )

    def test_worker_call_count_matches_passes(
        self,
        tick_loop_results: ProcessTickLoopResult,
    ):
        """One worker call per algo pass."""
        worker_stats = tick_loop_results.worker_statistics[0]
        algo_passes = tick_loop_results.coordination_statistics.algo_passes

        assert worker_stats.worker_call_count == algo_passes, (
            f'Worker calls {worker_stats.worker_call_count} != algo passes {algo_passes}'
        )

    def test_tick_count_positive(self, probe_metadata: ProbeMetadata):
        """Tick count should be positive."""
        assert probe_metadata.tick_count > 0, 'Tick count should be positive'
