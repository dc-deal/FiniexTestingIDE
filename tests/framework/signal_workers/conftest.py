"""
FiniexTestingIDE - Signal Workers Test Fixtures
Shared helpers for the SIGNAL worker / provider / hybrid-decision tests (#141).

No tick loop, no batch. Builds SignalSeries / providers / workers in-process and
injects them directly (the same seam the framework uses at construction).
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from python.framework.signal_data.signal_data_provider import SignalDataProvider
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.signal_data_types import (
    RunError, SentimentResult, SignalSeries, SignalSnapshot)

SYMBOL = 'BTCUSD'

# tests/fixtures/signals/sentiment_sample.jsonl
FIXTURE_JSONL = (
    Path(__file__).resolve().parents[2] / 'fixtures' / 'signals' / 'sentiment_sample.jsonl'
)


@pytest.fixture(scope='session')
def mock_logger():
    """Minimal mock logger for worker / decision-logic instantiation."""
    return MagicMock()


def utc(year, month, day, hour, minute, second=0) -> datetime:
    """Build a UTC, tz-aware datetime."""
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def make_tick(timestamp: datetime, symbol: str = SYMBOL, mid: float = 100.0) -> TickData:
    """Build a minimal tick at a timestamp."""
    return TickData(timestamp=timestamp, symbol=symbol, bid=mid, ask=mid + 0.02, volume=0.1)


def snapshot(
    collected_msc: datetime,
    score: float,
    confidence: float,
    signal: str = 'HOLD',
    urgency: float = 0.0,
    is_breaking: bool = False,
    symbol: str = SYMBOL,
    status: str = 'success',
) -> SignalSnapshot:
    """Build one SignalSnapshot carrying a single per-symbol result."""
    return SignalSnapshot(
        collected_msc=collected_msc,
        schema_version='1.0',
        status=status,
        result=[SentimentResult(
            symbol=symbol, signal=signal, sentiment_score=score,
            confidence=confidence, urgency=urgency, is_breaking=is_breaking,
        )],
    )


def error_snapshot(collected_msc: datetime) -> SignalSnapshot:
    """Build a status='error' snapshot with empty result (no usable sentiment)."""
    return SignalSnapshot(
        collected_msc=collected_msc, schema_version='1.0', status='error',
        result=[], errors=[RunError(type='LLM_TIMEOUT')],
    )


def make_provider(*snapshots: SignalSnapshot) -> SignalDataProvider:
    """Build a provider over the given snapshots (any order)."""
    return SignalDataProvider(
        SignalSeries(source='llm_sentiment', snapshots=list(snapshots))
    )
