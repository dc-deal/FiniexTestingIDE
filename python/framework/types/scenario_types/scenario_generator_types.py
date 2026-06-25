"""
Scenario Generator Types
========================
Type definitions for scenario generation strategies and their config.

The generated windows themselves live in window_set_types.py (GeneratedWindow / WindowSet) —
this module holds the strategy enum and the per-strategy split configuration.
"""

from dataclasses import dataclass
from typing import Dict, Optional
from enum import StrEnum


# =============================================================================
# ENUMS
# =============================================================================

class GenerationStrategy(StrEnum):
    """Scenario generation strategies (resolved to a Splitter by SplitterFactory)."""
    BLOCKS = 'blocks'
    VOLATILITY_SPLIT = 'volatility_split'
    CONTINUOUS = 'continuous'
    WALK_FORWARD = 'walk_forward'


# =============================================================================
# GENERATION CONFIG
# =============================================================================

@dataclass
class BlocksStrategyConfig:
    """Configuration for chronological blocks strategy."""
    default_block_hours: int = 6
    min_block_hours: int = 1  # Minimum block duration to generate


@dataclass
class ProfileStrategyConfig:
    """Configuration for profile-based volatility splitting."""
    min_block_hours: int = 2
    max_block_hours: int = 24
    atr_percentile_threshold: int = 10
    split_algorithm: str = 'atr_minima'

    @classmethod
    def from_dict(cls, data: Dict) -> 'ProfileStrategyConfig':
        """
        Create from dictionary.

        Args:
            data: Configuration dictionary

        Returns:
            ProfileStrategyConfig instance
        """
        return cls(
            min_block_hours=data.get('min_block_hours', 2),
            max_block_hours=data.get('max_block_hours', 24),
            atr_percentile_threshold=data.get('atr_percentile_threshold', 10),
            split_algorithm=data.get('split_algorithm', 'atr_minima'),
        )


@dataclass
class GeneratorConfig:
    """
    Generator configuration.

    Contains strategy-specific settings for block generation.
    """
    blocks: BlocksStrategyConfig
    profile: Optional[ProfileStrategyConfig] = None

    @classmethod
    def from_dict(cls, data: Dict) -> 'GeneratorConfig':
        """
        Create config from dictionary (loaded from JSON).

        Args:
            data: Configuration dictionary

        Returns:
            GeneratorConfig instance
        """
        blocks_data = data.get('strategies', {}).get('blocks', {})
        profile_data = data.get('profile')

        return cls(
            blocks=BlocksStrategyConfig(
                default_block_hours=blocks_data.get('default_block_hours', 6),
                min_block_hours=blocks_data.get('min_block_hours', 1),
            ),
            profile=ProfileStrategyConfig.from_dict(profile_data) if profile_data else None,
        )
