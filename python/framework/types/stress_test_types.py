# ============================================
# python/framework/types/stress_test_types.py
# ============================================
"""
FiniexTestingIDE - Stress Test Configuration Types
Type definitions for config-driven stress test injection.

Each stress test type has its own config dataclass.
StressTestConfig is the top-level container, parsed from scenario JSON.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class StressTestRejectOrderConfig:
    """
    Configuration for order rejection stress test.

    Rejects open orders with seeded probability.
    Same seed + same order sequence = identical rejection pattern.

    Args:
        enabled: Whether this stress test is active
        seed: Random seed for deterministic rejection sequence
        probability: Rejection probability (0.0 = never, 1.0 = always)
    """
    enabled: bool = False
    seed: int = 42
    probability: float = 0.0

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'StressTestRejectOrderConfig':
        """
        Parse from config dict.

        Args:
            data: Dict with keys: enabled, seed, probability

        Returns:
            StressTestRejectOrderConfig instance
        """
        return StressTestRejectOrderConfig(
            enabled=data.get('enabled', False),
            seed=data.get('seed', 42),
            probability=data.get('probability', 0.0)
        )


@dataclass
class StressTestConfig:
    """
    Top-level stress test configuration container.

    Holds configs for all stress test types.
    Parsed from 'stress_test_config' section in scenario JSON.

    Args:
        reject_open_order: Order rejection stress test config
    """
    reject_open_order: Optional[StressTestRejectOrderConfig] = None
    # Future: reject_close_order, timeout_simulation, slippage_injection, etc.

    @staticmethod
    def from_dict(data: Optional[Dict[str, Any]]) -> 'StressTestConfig':
        """
        Parse from config dict.

        Args:
            data: Dict from JSON stress_test_config section (or None)

        Returns:
            StressTestConfig instance (all disabled if data is None)
        """
        if not data:
            return StressTestConfig.disabled()

        reject_open_order = None
        if 'reject_open_order' in data:
            reject_open_order = StressTestRejectOrderConfig.from_dict(
                data['reject_open_order']
            )

        return StressTestConfig(
            reject_open_order=reject_open_order
        )

    @staticmethod
    def disabled() -> 'StressTestConfig':
        """
        Create config with all stress tests disabled.

        Returns:
            StressTestConfig with no active stress tests
        """
        return StressTestConfig()

    def has_any_enabled(self) -> bool:
        """
        Check if any stress test is enabled.

        Returns:
            True if at least one stress test is active
        """
        if self.reject_open_order and self.reject_open_order.enabled:
            return True
        return False
