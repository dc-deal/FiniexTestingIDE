"""
Scenario Generator Test Fixtures
==================================
Shared fixtures and helpers for scenario generator tests.

No file I/O, no external data — pure mock-based testing.
"""

import pytest
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from unittest.mock import MagicMock

from python.framework.types.coverage_report_types import Gap, GapCategory
from python.framework.types.market_types.market_volatility_profile_types import (
    VolatilityPeriod,
    TradingSession,
    VolatilityRegime,
)
from python.framework.types.scenario_types.scenario_generator_types import (
    VolatilityProfileConfig,
    BlocksStrategyConfig,
    CrossInstrumentRankingConfig,
    GeneratorConfig,
)


# =============================================================================
# TIME HELPERS
# =============================================================================

def utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    """Create timezone-aware UTC datetime."""
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


# =============================================================================
# FACTORY HELPERS
# =============================================================================

def make_gap(
    start: datetime,
    end: datetime,
    category: GapCategory = GapCategory.WEEKEND
) -> Gap:
    """
    Create a Gap object for testing.

    Args:
        start: Gap start time
        end: Gap end time
        category: Gap category

    Returns:
        Gap instance
    """
    gap_seconds = (end - start).total_seconds()
    return Gap(
        gap_seconds=gap_seconds,
        category=category,
        reason=f'test_{category.value}_gap',
        gap_start=start,
        gap_end=end
    )


def make_period(
    start: datetime,
    regime: VolatilityRegime = VolatilityRegime.MEDIUM,
    session: TradingSession = TradingSession.LONDON,
    tick_count: int = 1000,
    bar_count: int = 12,
    real_bar_count: int = 12
) -> VolatilityPeriod:
    """
    Create a VolatilityPeriod object for testing. Periods are 1h blocks.

    Args:
        start: Period start time
        regime: Volatility regime
        session: Trading session
        tick_count: Number of ticks
        bar_count: Total bar count
        real_bar_count: Real (non-synthetic) bar count

    Returns:
        VolatilityPeriod instance
    """
    return VolatilityPeriod(
        start_time=start,
        end_time=start + timedelta(hours=1),
        session=session,
        atr=0.5,
        atr_percentile=0.5,
        regime=regime,
        tick_count=tick_count,
        tick_density=float(tick_count),
        activity=float(tick_count),
        bar_count=bar_count,
        real_bar_count=real_bar_count,
        synthetic_bar_count=bar_count - real_bar_count,
        high=1.1,
        low=1.0,
        range_pips=100.0
    )


def make_continuous_periods(
    start: datetime,
    hours: int,
    regime: VolatilityRegime = VolatilityRegime.MEDIUM,
    session: TradingSession = TradingSession.LONDON,
    tick_count: int = 1000,
    bar_count: int = 12,
    real_bar_count: int = 12
) -> List[VolatilityPeriod]:
    """
    Create a list of continuous hourly periods.

    Args:
        start: First period start time
        hours: Number of consecutive hours
        regime: Volatility regime for all periods
        session: Trading session for all periods
        tick_count: Ticks per period
        bar_count: Bars per period
        real_bar_count: Real bars per period

    Returns:
        List of continuous VolatilityPeriod
    """
    return [
        make_period(
            start + timedelta(hours=h),
            regime=regime,
            session=session,
            tick_count=tick_count,
            bar_count=bar_count,
            real_bar_count=real_bar_count
        )
        for h in range(hours)
    ]


# =============================================================================
# MOCK FACTORIES
# =============================================================================

def mock_coverage_report(
    start: datetime,
    end: datetime,
    gaps: Optional[List[Gap]] = None
) -> MagicMock:
    """
    Create a mock DataCoverageReport.

    Args:
        start: Data start time
        end: Data end time
        gaps: List of gaps (empty if None)

    Returns:
        Configured MagicMock
    """
    report = MagicMock()
    report.start_time = start
    report.end_time = end
    report.gaps = gaps or []
    report.gap_counts = {
        'weekend': sum(1 for g in report.gaps if g.category == GapCategory.WEEKEND),
        'holiday': sum(1 for g in report.gaps if g.category == GapCategory.HOLIDAY),
        'moderate': sum(1 for g in report.gaps if g.category == GapCategory.MODERATE),
        'large': sum(1 for g in report.gaps if g.category == GapCategory.LARGE),
    }
    return report


# =============================================================================
# CONFIG FIXTURES
# =============================================================================

@pytest.fixture
def generator_config() -> GeneratorConfig:
    """Generator config with short durations for fast tests."""
    return GeneratorConfig(
        volatility_profile=VolatilityProfileConfig(),
        blocks=BlocksStrategyConfig(
            default_block_hours=4,
            min_block_hours=1,
            extend_blocks_beyond_session=True,
            min_real_bar_ratio=0.5
        ),
        cross_instrument_ranking=CrossInstrumentRankingConfig()
    )
