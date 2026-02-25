"""
FiniexTestingIDE - Shared Latency Determinism Tests
Reusable test classes for latency validation across test suites.

Used by: mvp_baseline, multi_position, margin_validation
Import these classes into suite-specific test_latency_determinism.py files.
"""

import pytest
from typing import Dict

from python.framework.utils.seeded_generators.seeded_delay_generator import SeededDelayGenerator


class TestLatencyDeterminism:
    """Tests for latency simulation determinism."""

    def test_api_delay_reproducible(self, seeds_config: Dict[str, int]):
        """Same API seed should produce identical delay sequence."""
        gen1 = SeededDelayGenerator(seeds_config['api_latency_seed'], 1, 3)
        gen2 = SeededDelayGenerator(seeds_config['api_latency_seed'], 1, 3)

        sequence1 = [gen1.next() for _ in range(100)]
        sequence2 = [gen2.next() for _ in range(100)]

        assert sequence1 == sequence2, "API delay sequences should be identical"

    def test_exec_delay_reproducible(self, seeds_config: Dict[str, int]):
        """Same execution seed should produce identical delay sequence."""
        gen1 = SeededDelayGenerator(
            seeds_config['market_execution_seed'], 2, 5)
        gen2 = SeededDelayGenerator(
            seeds_config['market_execution_seed'], 2, 5)

        sequence1 = [gen1.next() for _ in range(100)]
        sequence2 = [gen2.next() for _ in range(100)]

        assert sequence1 == sequence2, "Exec delay sequences should be identical"

    def test_different_seeds_different_sequences(self, seeds_config: Dict[str, int]):
        """Different seeds should produce different sequences."""
        gen1 = SeededDelayGenerator(seeds_config['api_latency_seed'], 1, 3)
        gen2 = SeededDelayGenerator(
            seeds_config['market_execution_seed'], 1, 3)

        sequence1 = [gen1.next() for _ in range(100)]
        sequence2 = [gen2.next() for _ in range(100)]

        assert sequence1 != sequence2, "Different seeds should produce different sequences"

    def test_api_delay_within_bounds(
        self,
        api_delay_generator: SeededDelayGenerator
    ):
        """API delays should be within configured bounds (1-3)."""
        for _ in range(100):
            delay = api_delay_generator.next()
            assert 1 <= delay <= 3, f"API delay {delay} out of bounds [1,3]"

    def test_exec_delay_within_bounds(
        self,
        exec_delay_generator: SeededDelayGenerator
    ):
        """Execution delays should be within configured bounds (2-5)."""
        for _ in range(100):
            delay = exec_delay_generator.next()
            assert 2 <= delay <= 5, f"Exec delay {delay} out of bounds [2,5]"

    def test_total_delay_calculation(self, seeds_config: Dict[str, int]):
        """Total delay should be api + exec delay."""
        api_gen = SeededDelayGenerator(seeds_config['api_latency_seed'], 1, 3)
        exec_gen = SeededDelayGenerator(
            seeds_config['market_execution_seed'], 2, 5)

        for _ in range(50):
            api_delay = api_gen.next()
            exec_delay = exec_gen.next()
            total = api_delay + exec_delay

            # Total should be between min_api+min_exec and max_api+max_exec
            assert 3 <= total <= 8, f"Total delay {total} out of bounds [3,8]"

    def test_fill_tick_calculation(
        self,
        seeds_config: Dict[str, int],
        trade_sequence: list
    ):
        """Fill tick should be signal_tick + total_delay."""
        api_gen = SeededDelayGenerator(seeds_config['api_latency_seed'], 1, 3)
        exec_gen = SeededDelayGenerator(
            seeds_config['market_execution_seed'], 2, 5)

        for trade in trade_sequence:
            signal_tick = trade['tick_number']
            api_delay = api_gen.next()
            exec_delay = exec_gen.next()
            fill_tick = signal_tick + api_delay + exec_delay

            # Fill tick should be after signal tick
            assert fill_tick > signal_tick, (
                f"Fill tick {fill_tick} should be > signal tick {signal_tick}"
            )
            # Fill tick should be within reasonable range
            assert fill_tick <= signal_tick + 8, (
                f"Fill tick {fill_tick} too far from signal tick {signal_tick}"
            )
