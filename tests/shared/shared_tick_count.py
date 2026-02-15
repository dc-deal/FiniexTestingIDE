"""
FiniexTestingIDE - Shared Tick Count Tests
Reusable test classes for tick count validation across test suites.

Used by: mvp_baseline, multi_position, margin_validation
Import these classes into suite-specific test_tick_count.py files.
"""

import pytest
from typing import Dict

from python.framework.types.backtesting_metadata_types import BacktestingMetadata
from python.framework.types.process_data_types import ProcessTickLoopResult


class TestTickCount:
    """Tests for tick count validation."""

    def test_tick_count_matches_config(
        self,
        backtesting_metadata: BacktestingMetadata,
        scenario_config: Dict
    ):
        """Processed tick count should match config max_ticks."""
        expected_ticks = scenario_config['scenarios'][0]['max_ticks']
        actual_ticks = backtesting_metadata.tick_count

        assert actual_ticks == expected_ticks, (
            f"Expected {expected_ticks} ticks, got {actual_ticks}"
        )

    def test_decision_count_matches_ticks(
        self,
        tick_loop_results: ProcessTickLoopResult,
        backtesting_metadata: BacktestingMetadata
    ):
        """Decision count should equal tick count."""
        decision_count = tick_loop_results.decision_statistics.decision_count
        tick_count = backtesting_metadata.tick_count

        assert decision_count == tick_count, (
            f"Decision count {decision_count} != tick count {tick_count}"
        )

    def test_worker_call_count_matches_ticks(
        self,
        tick_loop_results: ProcessTickLoopResult,
        backtesting_metadata: BacktestingMetadata
    ):
        """Worker call count should equal tick count."""
        worker_stats = tick_loop_results.worker_statistics[0]

        assert worker_stats.worker_call_count == backtesting_metadata.tick_count, (
            f"Worker calls {worker_stats.worker_call_count} != "
            f"ticks {backtesting_metadata.tick_count}"
        )

    def test_tick_count_positive(self, backtesting_metadata: BacktestingMetadata):
        """Tick count should be positive."""
        assert backtesting_metadata.tick_count > 0, "Tick count should be positive"
