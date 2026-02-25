# ============================================
# python/framework/utils/seeded_generators/seeded_probability_filter.py
# ============================================
"""
FiniexTestingIDE - Seeded Probability Filter
Deterministic probability-based decision maker with seeded randomness.

Used by stress test modules to produce reproducible trigger sequences.
Same seed + same call sequence = identical outcomes across runs.
"""

import random


class SeededProbabilityFilter:
    """
    Deterministic probability filter using seeded randomness.

    Each call to should_trigger() advances the internal RNG state.
    Probability range: 0.0 (never) to 1.0 (always).

    Args:
        seed: Random seed for reproducibility
        probability: Trigger probability (0.0 = never, 1.0 = always)
    """

    def __init__(self, seed: int, probability: float):
        if not 0.0 <= probability <= 1.0:
            raise ValueError(
                f"Probability must be between 0.0 and 1.0, got {probability}"
            )
        self._rng = random.Random(seed)
        self._probability = probability

    def should_trigger(self) -> bool:
        """
        Check if event should trigger based on seeded probability.

        Returns:
            True if triggered, False otherwise
        """
        if self._probability == 0.0:
            return False
        if self._probability == 1.0:
            return True
        rng = self._rng.random()
        return rng < self._probability

    def get_probability(self) -> float:
        """
        Get configured probability.

        Returns:
            Probability value (0.0-1.0)
        """
        return self._probability
