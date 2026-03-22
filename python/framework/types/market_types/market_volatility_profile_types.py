"""
Market Volatility Profile Types
================================
Type definitions for volatility profiling: configuration, volatility regimes,
trading sessions, volatility periods, and symbol-level profile summaries.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
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

    @property
    def short_label(self) -> str:
        """Short label for compact display (VL, L, M, H, VH)."""
        return _REGIME_SHORT_LABELS[self]


class TradingSession(Enum):
    """Trading session identifiers."""
    SYDNEY_TOKYO = "sydney_tokyo"
    LONDON = "london"
    NEW_YORK = "new_york"
    TRANSITION = "transition"

    def __str__(self) -> str:
        """String representation returns the enum value"""
        return self.value

    @property
    def display_name(self) -> str:
        """Human-readable session name."""
        return _SESSION_DISPLAY_NAMES[self]


_REGIME_SHORT_LABELS = {
    VolatilityRegime.VERY_LOW: 'VL',
    VolatilityRegime.LOW: 'L',
    VolatilityRegime.MEDIUM: 'M',
    VolatilityRegime.HIGH: 'H',
    VolatilityRegime.VERY_HIGH: 'VH',
}

_SESSION_DISPLAY_NAMES = {
    TradingSession.SYDNEY_TOKYO: 'Asian (Sydney/Tokyo)',
    TradingSession.LONDON: 'London',
    TradingSession.NEW_YORK: 'New York',
    TradingSession.TRANSITION: 'Transition',
}


# =============================================================================
# VOLATILITY PROFILE CONFIG
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
