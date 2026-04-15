"""
FiniexTestingIDE - Market Compatibility Tests — Mandatory Classmethod

Verifies the contract established by AbstractWorker.get_required_activity_metric():
- Every CORE worker must override the classmethod (no NotImplementedError)
- A subclass that does NOT override must raise NotImplementedError with an
  actionable message so the factory/validator can surface it pre-flight.
"""

from typing import Dict, List

import pytest

from python.framework.types.market_types.market_data_types import Bar, TickData
from python.framework.types.worker_types import WorkerResult, WorkerType
from python.framework.workers.abstract_worker import AbstractWorker
from python.framework.workers.core.backtesting.backtesting_sample_worker import (
    BacktestingSampleWorker,
)
from python.framework.workers.core.backtesting.heavy_rsi_worker import HeavyRsiWorker
from python.framework.workers.core.envelope_worker import EnvelopeWorker
from python.framework.workers.core.macd_worker import MacdWorker
from python.framework.workers.core.obv_worker import ObvWorker
from python.framework.workers.core.rsi_worker import RsiWorker


CORE_WORKERS_EXPECTED_METRIC = {
    RsiWorker: None,
    EnvelopeWorker: None,
    MacdWorker: None,
    HeavyRsiWorker: None,
    BacktestingSampleWorker: None,
    ObvWorker: 'volume',
}


@pytest.mark.parametrize('worker_class,expected', CORE_WORKERS_EXPECTED_METRIC.items())
def test_core_workers_declare_activity_metric(worker_class, expected):
    """Every CORE worker must return its declared metric without raising."""
    actual = worker_class.get_required_activity_metric()
    assert actual == expected, (
        f"{worker_class.__name__}.get_required_activity_metric() "
        f"returned {actual!r}, expected {expected!r}"
    )


class _IncompleteWorker(AbstractWorker):
    """Intentionally omits get_required_activity_metric() override."""

    @classmethod
    def get_worker_type(cls) -> WorkerType:
        return WorkerType.INDICATOR

    def get_warmup_requirements(self) -> Dict[str, int]:
        return {}

    def get_required_timeframes(self) -> List[str]:
        return []

    def should_recompute(self, tick: TickData, bar_updated: bool) -> bool:
        return False

    def compute(self, tick, bar_history, current_bars) -> WorkerResult:
        return WorkerResult(outputs={})


def test_missing_override_raises_not_implemented():
    """Subclass without override must raise NotImplementedError."""
    with pytest.raises(NotImplementedError) as exc_info:
        _IncompleteWorker.get_required_activity_metric()

    message = str(exc_info.value)
    assert '_IncompleteWorker' in message
    assert 'get_required_activity_metric' in message
    assert 'volume' in message
    assert 'tick_count' in message
