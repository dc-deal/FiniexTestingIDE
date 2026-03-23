"""
Tick Processing Budget Test Fixtures
======================================
Shared fixtures and helpers for tick processing budget unit tests.

No file I/O, no subprocesses — pure synthetic tick data.
"""

import pytest
from unittest.mock import MagicMock
from typing import Dict, Any, List, Tuple

from python.framework.data_preparation.shared_data_preparator import SharedDataPreparator
from python.framework.types.process_data_types import ClippingStats


# =============================================================================
# TICK HELPERS
# =============================================================================

def make_tick(collected_msc: int, bid: float = 1.0, ask: float = 1.1) -> dict:
    """
    Create a minimal tick dict with collected_msc.

    Args:
        collected_msc: Device-side collection timestamp (ms)
        bid: Bid price
        ask: Ask price

    Returns:
        Tick dict matching transport format
    """
    return {
        'collected_msc': collected_msc,
        'bid': bid,
        'ask': ask,
        'time_msc': collected_msc,
    }


def make_scenario_ticks(symbol: str, ticks: List[dict]) -> Dict[str, Any]:
    """
    Build scenario_ticks dict from tick list.

    Args:
        symbol: Trading symbol
        ticks: List of tick dicts

    Returns:
        Dict matching SharedDataPreparator's internal format
    """
    return {
        'ticks': {symbol: tuple(ticks)},
        'counts': {symbol: len(ticks)},
        'ranges': {symbol: None}
    }


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def mock_logger():
    """Mock ScenarioLogger for SharedDataPreparator."""
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    return logger


@pytest.fixture
def preparator(mock_logger) -> SharedDataPreparator:
    """
    SharedDataPreparator with mocked logger.

    Index managers are initialized in __init__ but not used by _apply_tick_budget.
    """
    prep = object.__new__(SharedDataPreparator)
    prep._logger = mock_logger
    return prep


@pytest.fixture
def regular_ticks() -> List[dict]:
    """10 ticks with 1ms spacing (integer-ms granularity)."""
    return [make_tick(1000 + i) for i in range(10)]


@pytest.fixture
def sparse_ticks() -> List[dict]:
    """6 ticks with known spacing for budget=2ms test."""
    return [
        make_tick(1000),
        make_tick(1001),
        make_tick(1002),
        make_tick(1003),
        make_tick(1005),
        make_tick(1008),
    ]


@pytest.fixture
def pre_v13_ticks() -> List[dict]:
    """Ticks with collected_msc=0 (pre-V1.3.0 data)."""
    return [
        {'collected_msc': 0, 'bid': 1.0, 'ask': 1.1, 'time_msc': 1000},
        {'collected_msc': 0, 'bid': 1.0, 'ask': 1.1, 'time_msc': 1001},
        {'collected_msc': 0, 'bid': 1.0, 'ask': 1.1, 'time_msc': 1002},
    ]
