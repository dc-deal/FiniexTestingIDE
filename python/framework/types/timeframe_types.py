"""
FiniexTestingIDE - Core Domain Types
Complete type system for blackbox framework

PERFORMANCE OPTIMIZED:
- TickData.timestamp is now datetime instead of str
- Eliminates 20,000+ pd.to_datetime() calls in bar rendering
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Tuple

from python.framework.types.scenario_types import ScenarioExecutionResult


class TimeframeConfig:
    """
    Timeframe configuration and utilities

    PERFORMANCE OPTIMIZED:
    - Added caching for get_bar_start_time()
    - Same minute always returns same bar start time
    """

    TIMEFRAME_MINUTES = {
        "M1": 1,
        "M5": 5,
        "M15": 15,
        "M30": 30,
        "H1": 60,
        "H4": 240,
        "D1": 1440,
    }

    # Cache for bar start times: (timestamp_minute_key, timeframe) -> bar_start_time
    _bar_start_cache: Dict[Tuple[str, str], datetime] = {}

    @classmethod
    def get_minutes(cls, timeframe: str) -> int:
        """Get minutes for timeframe"""
        return cls.TIMEFRAME_MINUTES.get(timeframe, 1)

    @classmethod
    def get_bar_start_time(cls, timestamp: datetime, timeframe: str) -> datetime:
        """
        Calculate bar start time for given timestamp.

        PERFORMANCE OPTIMIZED:
        - Results are cached per minute
        - Same minute + timeframe always returns cached result
        - Reduces 40,000 calculations to ~few hundred cache lookups
        """
        # Create cache key from minute precision (ignore seconds/microseconds)
        cache_key = (
            f"{timestamp.year}-{timestamp.month:02d}-{timestamp.day:02d}"
            f"T{timestamp.hour:02d}:{timestamp.minute:02d}",
            timeframe
        )

        # Check cache first
        if cache_key in cls._bar_start_cache:
            return cls._bar_start_cache[cache_key]

        # Calculate bar start time
        minutes = cls.get_minutes(timeframe)
        total_minutes = timestamp.hour * 60 + timestamp.minute
        bar_start_minute = (total_minutes // minutes) * minutes

        bar_start = timestamp.replace(
            minute=bar_start_minute % 60,
            hour=bar_start_minute // 60,
            second=0,
            microsecond=0,
        )

        # Cache result
        cls._bar_start_cache[cache_key] = bar_start

        # Limit cache size (keep last 10,000 entries)
        if len(cls._bar_start_cache) > 10000:
            # Remove oldest 5000 entries
            keys_to_remove = list(cls._bar_start_cache.keys())[:5000]
            for key in keys_to_remove:
                del cls._bar_start_cache[key]

        return bar_start

    @classmethod
    def is_bar_complete(
        cls, bar_start: datetime, current_time: datetime, timeframe: str
    ) -> bool:
        """Check if bar is complete"""
        from datetime import timedelta
        bar_duration = timedelta(minutes=cls.get_minutes(timeframe))
        bar_end = bar_start + bar_duration
        return current_time >= bar_end
