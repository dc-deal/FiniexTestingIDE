"""
FiniexTestingIDE - Shared Latency Determinism Tests
Reusable test classes for latency validation across test suites.

Used by: baseline, multi_position, margin_validation
Import these classes into suite-specific test_latency_determinism.py files.
"""

import pytest
from typing import Dict

from python.framework.utils.seeded_generators.seeded_delay_generator import SeededDelayGenerator


class TestLatencyDeterminism:
    """Tests for latency simulation determinism."""

    def test_api_delay_reproducible(self, seeds_config: Dict[str, int]):
        """Same API seed should produce identical delay sequence."""
        gen1 = SeededDelayGenerator(seeds_config['api_latency_seed'], 20, 80)
        gen2 = SeededDelayGenerator(seeds_config['api_latency_seed'], 20, 80)

        sequence1 = [gen1.next() for _ in range(100)]
        sequence2 = [gen2.next() for _ in range(100)]

        assert sequence1 == sequence2, "API delay sequences should be identical"

    def test_exec_delay_reproducible(self, seeds_config: Dict[str, int]):
        """Same execution seed should produce identical delay sequence."""
        gen1 = SeededDelayGenerator(
            seeds_config['market_execution_seed'], 30, 150)
        gen2 = SeededDelayGenerator(
            seeds_config['market_execution_seed'], 30, 150)

        sequence1 = [gen1.next() for _ in range(100)]
        sequence2 = [gen2.next() for _ in range(100)]

        assert sequence1 == sequence2, "Exec delay sequences should be identical"

    def test_different_seeds_different_sequences(self, seeds_config: Dict[str, int]):
        """Different seeds should produce different sequences."""
        gen1 = SeededDelayGenerator(seeds_config['api_latency_seed'], 20, 80)
        gen2 = SeededDelayGenerator(
            seeds_config['market_execution_seed'], 20, 80)

        sequence1 = [gen1.next() for _ in range(100)]
        sequence2 = [gen2.next() for _ in range(100)]

        assert sequence1 != sequence2, "Different seeds should produce different sequences"

    def test_api_delay_within_bounds(
        self,
        api_delay_generator: SeededDelayGenerator
    ):
        """API delays should be within configured bounds (20-80ms)."""
        for _ in range(100):
            delay = api_delay_generator.next()
            assert 20 <= delay <= 80, f"API delay {delay}ms out of bounds [20,80]"

    def test_exec_delay_within_bounds(
        self,
        exec_delay_generator: SeededDelayGenerator
    ):
        """Execution delays should be within configured bounds (30-150ms)."""
        for _ in range(100):
            delay = exec_delay_generator.next()
            assert 30 <= delay <= 150, f"Exec delay {delay}ms out of bounds [30,150]"

    def test_total_delay_calculation(self, seeds_config: Dict[str, int]):
        """Total delay should be api + exec delay."""
        api_gen = SeededDelayGenerator(seeds_config['api_latency_seed'], 20, 80)
        exec_gen = SeededDelayGenerator(
            seeds_config['market_execution_seed'], 30, 150)

        for _ in range(50):
            api_delay = api_gen.next()
            exec_delay = exec_gen.next()
            total = api_delay + exec_delay

            # Total should be between min_api+min_exec and max_api+max_exec
            assert 50 <= total <= 230, f"Total delay {total}ms out of bounds [50,230]"

    def test_fill_msc_calculation(
        self,
        seeds_config: Dict[str, int],
        trade_sequence: list
    ):
        """fill_at_msc should be placed_at_msc + total_delay (uses tick_number as proxy msc)."""
        api_gen = SeededDelayGenerator(seeds_config['api_latency_seed'], 20, 80)
        exec_gen = SeededDelayGenerator(
            seeds_config['market_execution_seed'], 30, 150)

        for trade in trade_sequence:
            # Use tick_number as a proxy msc value for determinism validation
            signal_msc = trade['tick_number']
            api_delay = api_gen.next()
            exec_delay = exec_gen.next()
            fill_at_msc = signal_msc + api_delay + exec_delay

            # Fill msc should be after signal msc
            assert fill_at_msc > signal_msc, (
                f"fill_at_msc {fill_at_msc} should be > signal msc {signal_msc}"
            )
            # Fill msc should be within reasonable range (max 230ms)
            assert fill_at_msc <= signal_msc + 230, (
                f"fill_at_msc {fill_at_msc} too far from signal msc {signal_msc}"
            )
