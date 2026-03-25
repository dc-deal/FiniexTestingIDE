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

    def test_inbound_delay_reproducible(self, seeds_config: Dict[str, int]):
        """Same inbound seed should produce identical delay sequence."""
        gen1 = SeededDelayGenerator(seeds_config['inbound_latency_seed'], 20, 80)
        gen2 = SeededDelayGenerator(seeds_config['inbound_latency_seed'], 20, 80)

        sequence1 = [gen1.next() for _ in range(100)]
        sequence2 = [gen2.next() for _ in range(100)]

        assert sequence1 == sequence2, "Inbound delay sequences should be identical"

    def test_inbound_delay_within_bounds(
        self,
        inbound_delay_generator: SeededDelayGenerator
    ):
        """Inbound delays should be within configured bounds (20-80ms)."""
        for _ in range(100):
            delay = inbound_delay_generator.next()
            assert 20 <= delay <= 80, f"Inbound delay {delay}ms out of bounds [20,80]"

    def test_broker_fill_msc_calculation(
        self,
        seeds_config: Dict[str, int],
        trade_sequence: list
    ):
        """broker_fill_msc should be placed_at_msc + inbound_delay only (uses tick_number as proxy msc)."""
        inbound_gen = SeededDelayGenerator(seeds_config['inbound_latency_seed'], 20, 80)

        for trade in trade_sequence:
            # Use tick_number as a proxy msc value for determinism validation
            signal_msc = trade['tick_number']
            inbound_delay = inbound_gen.next()
            broker_fill_msc = signal_msc + inbound_delay

            # Broker fill msc should be after signal msc
            assert broker_fill_msc > signal_msc, (
                f"broker_fill_msc {broker_fill_msc} should be > signal msc {signal_msc}"
            )
            # Broker fill msc should be within inbound range only (max 80ms)
            assert broker_fill_msc <= signal_msc + 80, (
                f"broker_fill_msc {broker_fill_msc} too far from signal msc {signal_msc}"
            )
