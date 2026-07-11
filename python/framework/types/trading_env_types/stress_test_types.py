"""
FiniexTestingIDE - Stress Test Configuration Types
Type definitions for config-driven stress test injection.

Each stress test type has its own config dataclass.
StressTestConfig is the top-level container, parsed from scenario JSON.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from python.framework.utils.time_utils import ensure_utc_aware, parse_datetime


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
class StaleDataEvent:
    """
    One planned stale window (#436 stale-data stress).

    An event blocks a DATA SOURCE the scenario binds — never bars or single
    workers: an outage hits a feed, so EVERY consumer of that source sees it.
    The source kind decides the injection plane:
    - data_source == the scenario's data_sentiment_type → data-plane cut: the
      window is carved out of the refined signal series (StaleDataSlicer), so
      the real #434 chain fires for all subscribed SIGNAL workers (age grows
      → is_stale flip → on_signal_stale).
    - data_source == the scenario's data_broker_type → status-plane: ticks
      keep flowing; status + on_market_data_stale + guard entry-block are
      driven inside the window (a dead FEED does not freeze the market).

    Args:
        label: Human-readable event name (logs / episode protocol)
        data_source: The scenario data source this outage hits
        stale_start_date: Window start (UTC, inclusive)
        stale_end_date: Window end (UTC, exclusive)
    """
    label: str
    data_source: str
    stale_start_date: datetime
    stale_end_date: datetime

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'StaleDataEvent':
        """
        Parse + validate one event from config (datetimes parsed ONCE here —
        never on the tick path).

        Args:
            data: Dict with keys: label, data_source, stale_start_date, stale_end_date

        Returns:
            StaleDataEvent instance
        """
        data_source = data.get('data_source', '')
        if not data_source:
            raise ValueError(
                "stale_data_stress event: 'data_source' is required (the "
                "scenario's data_broker_type or data_sentiment_type)")
        start = ensure_utc_aware(parse_datetime(data['stale_start_date']))
        end = ensure_utc_aware(parse_datetime(data['stale_end_date']))
        if start >= end:
            raise ValueError(
                f"stale_data_stress event '{data.get('label', '')}': "
                f"stale_start_date must be before stale_end_date")
        return StaleDataEvent(
            label=data.get('label', ''),
            data_source=data_source,
            stale_start_date=start,
            stale_end_date=end,
        )


@dataclass
class StressTestStaleDataConfig:
    """
    Configuration for planned stale-data windows (#436).

    Args:
        enabled: Whether this stress test is active
        events: Planned stale windows (see StaleDataEvent)
    """
    enabled: bool = False
    events: List[StaleDataEvent] = field(default_factory=list)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'StressTestStaleDataConfig':
        """
        Parse from config dict.

        Args:
            data: Dict with keys: enabled, events

        Returns:
            StressTestStaleDataConfig instance
        """
        return StressTestStaleDataConfig(
            enabled=data.get('enabled', False),
            events=[StaleDataEvent.from_dict(e) for e in data.get('events', [])],
        )

    def get_windows_for_source(self, data_source: str) -> List[tuple]:
        """
        Stale windows of the events hitting one data source.

        Args:
            data_source: Scenario data source (broker or sentiment type)

        Returns:
            List of (start, end) datetime tuples, sorted
        """
        if not self.enabled:
            return []
        return sorted(
            (e.stale_start_date, e.stale_end_date)
            for e in self.events
            if e.data_source == data_source
        )

    def get_events_for_source(self, data_source: str) -> List[StaleDataEvent]:
        """
        Events hitting one data source, sorted by window start.

        Args:
            data_source: Scenario data source (broker or sentiment type)

        Returns:
            List of StaleDataEvent for that source
        """
        if not self.enabled:
            return []
        return sorted(
            (e for e in self.events if e.data_source == data_source),
            key=lambda e: e.stale_start_date,
        )

    def get_referenced_sources(self) -> List[str]:
        """
        Distinct data sources referenced by the events (validation input).

        Returns:
            Sorted list of data_source values
        """
        if not self.enabled:
            return []
        return sorted({e.data_source for e in self.events})


@dataclass
class StressTestConfig:
    """
    Top-level stress test configuration container.

    Holds configs for all stress test types.
    Parsed from 'stress_test_config' section in scenario JSON.

    Args:
        reject_open_order: Order rejection stress test config
        stale_data_stress: Planned stale-data windows (#436)
    """
    reject_open_order: Optional[StressTestRejectOrderConfig] = None
    stale_data_stress: Optional[StressTestStaleDataConfig] = None
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

        stale_data_stress = None
        if 'stale_data_stress' in data:
            stale_data_stress = StressTestStaleDataConfig.from_dict(
                data['stale_data_stress']
            )

        return StressTestConfig(
            reject_open_order=reject_open_order,
            stale_data_stress=stale_data_stress,
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
        if self.stale_data_stress and self.stale_data_stress.enabled:
            return True
        return False
