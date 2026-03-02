"""
Market Analysis Types
=====================
Type definitions for market analysis results: volatility regimes,
trading sessions, period analysis, and symbol-level analysis summaries.
"""

from dataclasses import dataclass
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


class TradingSession(Enum):
    """Trading session identifiers."""
    SYDNEY_TOKYO = "sydney_tokyo"
    LONDON = "london"
    NEW_YORK = "new_york"
    TRANSITION = "transition"

    def __str__(self) -> str:
        """String representation returns the enum value"""
        return self.value


# =============================================================================
# ANALYSIS RESULTS
# =============================================================================

@dataclass
class PeriodAnalysis:
    """
    Analysis results for a single time period.

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
class SymbolAnalysis:
    """
    Complete market analysis for a symbol.
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

    # All period analyses
    periods: List[PeriodAnalysis]
