"""
FiniexTestingIDE - Latency Determinism Tests
Validates seeded delay generators produce consistent results

Tests:
- Same seed produces same sequence
- Different seeds produce different sequences
- Delay values within configured bounds
"""

from tests.shared.shared_latency import TestLatencyDeterminism

# All test classes imported from shared module.
# Pytest discovers them via this import.
