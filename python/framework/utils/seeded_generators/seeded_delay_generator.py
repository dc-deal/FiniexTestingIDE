# ============================================
# python/framework/utils/seeded_generators/seeded_delay_generator.py
# ============================================
"""
FiniexTestingIDE - Seeded Delay Generator
Deterministic random delay generation with seeded randomness.

Used by OrderLatencySimulator for reproducible API latency
and market execution delays.
"""

import random


class SeededDelayGenerator:
    """
    Generate deterministic random delays using seeds.

    Uses Python's random.Random with explicit seed for reproducibility.
    Every run with same seed produces identical delay sequence.

    NOTE: Currently tick-based (ticks to wait).
    Post-MVP: Will be MS-based with tick→timestamp mapping.

    Args:
        seed: Random seed for reproducibility
        min_delay: Minimum delay in ticks (MVP) / ms (Post-MVP)
        max_delay: Maximum delay in ticks (MVP) / ms (Post-MVP)
    """

    def __init__(self, seed: int, min_delay: int, max_delay: int):
        self.rng = random.Random(seed)
        self.min_delay = min_delay
        self.max_delay = max_delay

    def next(self) -> int:
        """
        Generate next delay value.

        Returns:
            Random delay between min_delay and max_delay (inclusive)
        """
        return self.rng.randint(self.min_delay, self.max_delay)
