"""
Worker include_current_bar Option Tests (#387).

Verifies the per-instance current-bar option:
- resolution: config 'include_current_bar' overrides the class default (True),
- preflight validation of the reserved key (must be bool),
- effective_bars(): appends the current (forming) bar by default, excludes it when
  completed-bar-only,
- a completed-bar-only worker's output is independent of the current bar (the
  property that makes ON_BAR_CLOSE recompute determinism-safe on any finer grid).
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from python.framework.types.market_types.market_data_types import Bar, TickData
from python.framework.workers.core.bollinger_worker import BollingerWorker


# =============================================================================
# HELPERS
# =============================================================================

def _bollinger(params: dict) -> BollingerWorker:
    return BollingerWorker(name='bb', parameters=params, logger=MagicMock())


def _bar(close: float, complete: bool = True, tf: str = 'M5') -> Bar:
    return Bar(
        symbol='EURUSD', timeframe=tf, timestamp='2025-10-01T00:00:00+00:00',
        open=close, high=close, low=close, close=close, volume=1.0,
        is_complete=complete,
    )


def _tick(mid: float) -> TickData:
    return TickData(
        timestamp=datetime(2025, 10, 1, 12, 0, 0, tzinfo=timezone.utc),
        symbol='EURUSD', bid=mid, ask=mid, volume=0.0,
    )


# Five completed M5 bars + one current (forming) bar far above the history
_HISTORY = {'M5': [_bar(100), _bar(101), _bar(102), _bar(103), _bar(104)]}
_CURRENT = {'M5': _bar(110, complete=False)}
_BASE = {'periods': {'M5': 5}, 'deviation': 2.0}


# =============================================================================
# TESTS — resolution
# =============================================================================

class TestCurrentBarResolution:
    """config 'include_current_bar' overrides the class default."""

    def test_default_includes(self):
        assert _bollinger(_BASE).includes_current_bar() is True

    def test_class_default_is_true(self):
        assert BollingerWorker.get_default_includes_current_bar() is True

    def test_config_false_overrides(self):
        assert _bollinger({**_BASE, 'include_current_bar': False}).includes_current_bar() is False

    def test_config_true_explicit(self):
        assert _bollinger({**_BASE, 'include_current_bar': True}).includes_current_bar() is True


class TestValidation:
    """The reserved 'include_current_bar' key must be a bool."""

    def test_non_bool_raises(self):
        with pytest.raises(ValueError, match='include_current_bar'):
            BollingerWorker.validate_config({'periods': {'M5': 5}, 'include_current_bar': 'yes'})

    def test_bool_ok(self):
        BollingerWorker.validate_config({'periods': {'M5': 5}, 'include_current_bar': False})

    def test_absent_ok(self):
        BollingerWorker.validate_config({'periods': {'M5': 5}})


# =============================================================================
# TESTS — effective_bars
# =============================================================================

class TestEffectiveBars:
    """history + current bar by default; history only when completed-bar-only."""

    def test_default_appends_current(self):
        bars = _bollinger(_BASE).effective_bars('M5', _HISTORY, _CURRENT)
        assert len(bars) == 6
        assert bars[-1].close == 110

    def test_completed_only_excludes_current(self):
        worker = _bollinger({**_BASE, 'include_current_bar': False})
        bars = worker.effective_bars('M5', _HISTORY, _CURRENT)
        assert len(bars) == 5
        assert all(b.close != 110 for b in bars)

    def test_no_current_bar_present(self):
        bars = _bollinger(_BASE).effective_bars('M5', _HISTORY, {})
        assert len(bars) == 5


# =============================================================================
# TESTS — compute independence (the determinism-relevant property)
# =============================================================================

class TestCompletedBarOnlyCompute:
    """A completed-bar-only worker ignores the current bar entirely."""

    def test_bands_independent_of_current_bar(self):
        worker = _bollinger({**_BASE, 'include_current_bar': False})
        with_current = worker.compute(_tick(110), _HISTORY, _CURRENT)
        without_current = worker.compute(_tick(110), _HISTORY, {})
        for band in ('upper', 'middle', 'lower'):
            assert with_current.outputs[band] == without_current.outputs[band]

    def test_default_bands_react_to_current_bar(self):
        worker = _bollinger(_BASE)  # include_current_bar defaults True
        with_current = worker.compute(_tick(110), _HISTORY, _CURRENT)
        without_current = worker.compute(_tick(110), _HISTORY, {})
        assert with_current.outputs['middle'] != without_current.outputs['middle']
