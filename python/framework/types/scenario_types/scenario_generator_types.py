"""
Scenario Generator Types
========================
Type definitions for scenario generation strategies and results.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional
from enum import Enum

from python.framework.types.market_types.market_volatility_profile_types import (
    TradingSession,
    VolatilityRegime,
)


# =============================================================================
# ENUMS
# =============================================================================

class GenerationStrategy(Enum):
    """Scenario generation strategies."""
    BLOCKS = 'blocks'

    def __str__(self) -> str:
        """String representation returns the enum value"""
        return self.value


# =============================================================================
# GENERATION CONFIG
# =============================================================================

@dataclass
class BlocksStrategyConfig:
    """Configuration for chronological blocks strategy."""
    default_block_hours: int = 6
    min_block_hours: int = 1  # Minimum block duration to generate
    min_real_bar_ratio: float = 0.5


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
                min_real_bar_ratio=blocks_data.get('min_real_bar_ratio', 0.5)
            ),
            profile=ProfileStrategyConfig.from_dict(profile_data) if profile_data else None,
        )


# =============================================================================
# SCENARIO SELECTION
# =============================================================================

@dataclass
class ScenarioCandidate:
    """
    A candidate time period for scenario generation.

    Selected by the generator based on analysis results.
    """
    symbol: str
    start_time: datetime
    end_time: datetime

    # Data source identifier
    broker_type: str  # e.g., 'mt5', 'kraken_spot'

    # Selection criteria
    regime: VolatilityRegime
    session: TradingSession

    # Metrics
    estimated_ticks: int
    atr: float
    tick_density: float
    real_bar_ratio: float

    # Scoring
    score: float = 0.0

    def to_scenario_dict(self, name: str, max_ticks: Optional[int] = None) -> Dict:
        """
        Convert to scenario dictionary for config output.

        Args:
            name: Scenario name
            max_ticks: Optional tick limit (None = time-based only, no tick limit)

        Returns:
            Scenario dictionary compatible with ScenarioConfigSaver
        """
        # Determine max_ticks: None means time-based (no tick limit)
        # Explicit None is preserved, otherwise use estimated_ticks as fallback
        effective_max_ticks = max_ticks if max_ticks is not None else self.estimated_ticks
        # But if estimated_ticks is 0, that means time-based → use None
        if effective_max_ticks == 0:
            effective_max_ticks = None

        return {
            'name': name,
            'symbol': self.symbol,
            'data_broker_type': self.broker_type,
            'start_date': self.start_time.isoformat(),
            'end_date': self.end_time.isoformat(),
            'max_ticks': effective_max_ticks,  # None → null in JSON
            'data_mode': 'realistic',
            'enabled': True,
            'strategy_config': {},
            'execution_config': {},
            'trade_simulator_config': {}
        }


@dataclass
class GenerationResult:
    """
    Result of scenario generation.
    """
    symbol: str
    strategy: GenerationStrategy
    scenarios: List[ScenarioCandidate]

    # Statistics
    total_estimated_ticks: int
    avg_ticks_per_scenario: float
    regime_coverage: Dict[VolatilityRegime, int]
    session_coverage: Dict[TradingSession, int]

    # Metadata
    generated_at: datetime
    config_used: GeneratorConfig
