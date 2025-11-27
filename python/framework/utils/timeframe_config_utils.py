"""
Central Timeframe Registry
Unified source of truth for all timeframe definitions.

All modules (renderer, importer, workers, indicators, strategies)
must access timeframe-related information exclusively through this unit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Iterable

from python.framework.exceptions.timeframe_exceptions import (
    UnsupportedTimeframeError,
    TimeframeConfigError,
)


@dataclass(frozen=True)
class Timeframe:
    """
    Immutable timeframe configuration data.

    Args:
        name: Timeframe ID (e.g., "M5", "H1").
        minutes: Duration in minutes.
        resample_rule: Pandas-compatible resample rule.
        sort_index: Integer sort index for stable ordering.
    """

    name: str
    minutes: int
    resample_rule: str
    sort_index: int


class TimeframeConfig:
    """
    Central registry for all timeframe definitions.

    Provides:
    - Validation helpers
    - Lookup functions
    - Duration lookups
    - Resample-rule access
    - Canonical ordering
    """

    # Base registry entries (extendable later)
    _REGISTRY: Dict[str, Dict[str, object]] = {
        "M1":  {"minutes": 1,    "resample": "1min"},
        "M5":  {"minutes": 5,    "resample": "5min"},
        "M15": {"minutes": 15,   "resample": "15min"},
        "M30": {"minutes": 30,   "resample": "30min"},
        "H1":  {"minutes": 60,   "resample": "1h"},
        "H4":  {"minutes": 240,  "resample": "4h"},
        "D1":  {"minutes": 1440, "resample": "1D"},
    }

    _OBJECTS: Dict[str, Timeframe] = {}
    _SORTED: List[str] = []

    @classmethod
    def initialize(cls) -> None:
        """
        Initialize registry objects and verify internal consistency.

        Args:
            None

        Returns:
            None
        """
        sort_index = 0

        for name, entry in cls._REGISTRY.items():
            minutes = entry.get("minutes")
            resample = entry.get("resample")

            if not isinstance(minutes, int) or minutes <= 0:
                raise TimeframeConfigError(
                    f"Invalid minute mapping for timeframe {name}."
                )

            if not isinstance(resample, str) or len(resample) == 0:
                raise TimeframeConfigError(
                    f"Invalid resample rule for timeframe {name}."
                )

            tf_obj = Timeframe(
                name=name,
                minutes=minutes,
                resample_rule=resample,
                sort_index=sort_index,
            )

            cls._OBJECTS[name] = tf_obj
            sort_index += 1

        # Precompute sorted order
        cls._SORTED = sorted(
            cls._OBJECTS.keys(),
            key=lambda k: cls._OBJECTS[k].sort_index
        )

    @classmethod
    def exists(cls, timeframe: str) -> bool:
        """
        Check if timeframe exists.

        Args:
            timeframe: Timeframe name.

        Returns:
            True if exists, otherwise False.
        """
        return timeframe in cls._OBJECTS

    @classmethod
    def get(cls, timeframe: str) -> Timeframe:
        """
        Fetch validated timeframe object.

        Args:
            timeframe: Timeframe name.

        Returns:
            Timeframe object.

        Raises:
            UnsupportedTimeframeError: When timeframe does not exist.
        """
        obj = cls._OBJECTS.get(timeframe)
        if obj is None:
            raise UnsupportedTimeframeError(timeframe)
        return obj

    @classmethod
    def get_minutes(cls, timeframe: str) -> int:
        """
        Return minute duration for timeframe.

        Args:
            timeframe: Timeframe name.

        Returns:
            Number of minutes.
        """
        return cls.get(timeframe).minutes

    @classmethod
    def get_resample_rule(cls, timeframe: str) -> str:
        """
        Return pandas resample rule.

        Args:
            timeframe: Timeframe name.

        Returns:
            Pandas rule string.
        """
        return cls.get(timeframe).resample_rule

    @classmethod
    def validate_many(cls, timeframes: Iterable[str]) -> List[str]:
        """
        Validate iterable of timeframe strings.

        Args:
            timeframes: Iterable of timeframe names.

        Returns:
            List of validated timeframe names (original order).
        """
        validated: List[str] = []
        for tf in timeframes:
            if tf not in cls._OBJECTS:
                raise UnsupportedTimeframeError(tf)
            validated.append(tf)
        return validated

    @classmethod
    def sorted(cls) -> List[str]:
        """
        Return globally sorted timeframe list.

        Args:
            None

        Returns:
            List of sorted timeframe names.
        """
        return cls._SORTED.copy()

    @classmethod
    def normalize(cls, timeframe: str) -> str:
        """
        Normalize user-input timeframe.

        Args:
            timeframe: Input string.

        Returns:
            Validated canonical timeframe name.
        """
        tf = timeframe.strip().upper()
        if tf not in cls._OBJECTS:
            raise UnsupportedTimeframeError(tf)
        return tf


# Initialize registry once at import time
TimeframeConfig.initialize()
