"""
Scenario Generator Types
========================
Type definitions for volatility profiling and scenario generation.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from enum import Enum

from python.framework.types.market_types.market_config_types import MarketType


# =============================================================================
# ENUMS
# =============================================================================

class VolatilityRegime(Enum):
    """Volatility regime classification."""
    VERY_LOW = "very_low"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"

    def __str__(self) -> str:
        """String representation returns the enum value"""
        return self.value


class TradingSession(Enum):
    """Trading session identifiers."""
    SYDNEY_TOKYO = "sydney_tokyo"
    LONDON = "london"
    NEW_YORK = "new_york"
    TRANSITION = "transition"

    def __str__(self) -> str:
        """String representation returns the enum value"""
        return self.value


class GenerationStrategy(Enum):
    """Scenario generation strategies."""
    BLOCKS = 'blocks'

    def __str__(self) -> str:
        """String representation returns the enum value"""
        return self.value


# =============================================================================
# VOLATILITY PROFILE RESULTS
# =============================================================================

@dataclass
class VolatilityPeriod:
    """
    Volatility profile for a single time period.

    Represents one hour of market data with volatility and activity metrics.
    """
    start_time: datetime
    end_time: datetime
    session: TradingSession

    # Volatility metrics
    atr: float
    atr_percentile: float
    regime: VolatilityRegime

    # Activity metrics
    tick_count: int
    tick_density: float  # ticks per hour

    # Unified activity metric (tick_count for forex, volume for crypto)
    activity: float

    # Bar statistics
    bar_count: int
    real_bar_count: int
    synthetic_bar_count: int

    # Price range
    high: float
    low: float
    range_pips: float


@dataclass
class SessionSummary:
    """
    Aggregated statistics for a trading session.
    """
    session: TradingSession
    period_count: int

    # Volatility
    avg_atr: float
    min_atr: float
    max_atr: float

    # Activity
    total_ticks: int
    avg_tick_density: float
    min_tick_density: float
    max_tick_density: float

    # Unified activity metric (sum of activity for all periods)
    total_activity: float

    # Regime distribution
    regime_distribution: Dict[VolatilityRegime, int]


@dataclass
class SymbolVolatilityProfile:
    """
    Complete volatility profile for a symbol.
    """
    symbol: str
    timeframe: str
    market_type: MarketType
    data_source: str

    # Time range
    start_time: datetime
    end_time: datetime
    total_days: int

    # Overall statistics
    total_bars: int
    total_ticks: int
    real_bar_ratio: float

    # ATR statistics
    atr_min: float
    atr_max: float
    atr_avg: float
    atr_std: float

    # Cross-instrument comparison (ATR as percentage of price)
    atr_percent: float

    # Unified activity metric (tick_count for forex, volume for crypto)
    total_activity: float

    # Average pips per day (None if symbol spec not available)
    avg_pips_per_day: Optional[float]

    # Regime distribution
    regime_distribution: Dict[VolatilityRegime, int]
    regime_percentages: Dict[VolatilityRegime, float]

    # Session summaries
    session_summaries: Dict[TradingSession, SessionSummary]

    # All volatility periods
    periods: List[VolatilityPeriod]


# =============================================================================
# GENERATION CONFIG
# =============================================================================

@dataclass
class VolatilityProfileConfig:
    """
    Configuration for volatility profiling.
    """
    # Volatility profile parameters
    timeframe: str = "M5"
    atr_period: int = 14
    regime_granularity_hours: int = 1

    # Regime thresholds (percentiles)
    regime_thresholds: List[int] = field(
        default_factory=lambda: [20, 40, 60, 80]
    )


@dataclass
class CrossInstrumentRankingConfig:
    """Configuration for cross-instrument comparison ranking."""
    top_count: int = 3


@dataclass
class BlocksStrategyConfig:
    """Configuration for chronological blocks strategy."""
    default_block_hours: int = 6
    min_block_hours: int = 1  # Minimum block duration to generate
    # Allow blocks to extend past session boundaries
    extend_blocks_beyond_session: bool = True
    min_real_bar_ratio: float = 0.5


@dataclass
class GeneratorConfig:
    """
    Complete generator configuration.

    Combines volatility profile config with strategy-specific settings.
    """
    volatility_profile: VolatilityProfileConfig
    blocks: BlocksStrategyConfig
    cross_instrument_ranking: CrossInstrumentRankingConfig

    @classmethod
    def from_dict(cls, data: Dict) -> 'GeneratorConfig':
        """
        Create config from dictionary (loaded from JSON).

        Args:
            data: Configuration dictionary

        Returns:
            GeneratorConfig instance
        """
        profile_data = data.get('volatility_profile', {})
        blocks_data = data.get('strategies', {}).get('blocks', {})
        ranking_data = data.get('cross_instrument_ranking', {})

        return cls(
            volatility_profile=VolatilityProfileConfig(
                timeframe=profile_data.get('timeframe', 'M5'),
                atr_period=profile_data.get('atr_period', 14),
                regime_granularity_hours=profile_data.get(
                    'regime_granularity_hours', 1
                ),
                regime_thresholds=profile_data.get(
                    'regime_thresholds', [0.5, 0.8, 1.2, 1.8]
                ),
            ),
            blocks=BlocksStrategyConfig(
                default_block_hours=blocks_data.get('default_block_hours', 6),
                min_block_hours=blocks_data.get('min_block_hours', 1),
                extend_blocks_beyond_session=blocks_data.get(
                    'extend_blocks_beyond_session', True),
                min_real_bar_ratio=blocks_data.get('min_real_bar_ratio', 0.5)
            ),
            cross_instrument_ranking=CrossInstrumentRankingConfig(
                top_count=ranking_data.get('top_count', 3)
            )
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
