"""
FiniexTestingIDE - OBV Worker Computation Tests

Tests the OBV (On-Balance Volume) compute() method.

Key implementation details (verified from source):
- _calculate_obv iterates from index 1:
    close[i] > close[i-1] → OBV += volume[i]
    close[i] < close[i-1] → OBV -= volume[i]
    close[i] == close[i-1] → OBV unchanged
- Returns WorkerResult with value = float (NOT dict!)
- Constructor accepts trading_context (optional, for Forex warning)
- Needs at least 2 bars, otherwise returns value=0.0, confidence=0.0

Volume matters here! Other workers use make_bars() with constant volume.
OBV tests use make_bars_with_volume() for explicit volume control.
"""

from unittest.mock import MagicMock

import pytest

from python.framework.workers.core.obv_worker import OBVWorker
from python.framework.types.worker_types import WorkerResult
from python.framework.types.market_config_types import MarketType

from conftest import make_bars_with_volume, make_tick


def _make_obv_worker(mock_logger, period=20, trading_context=None):
    """Helper: create OBV worker with standard config."""
    return OBVWorker(
        name="test_obv",
        parameters={"periods": {"M5": period}},
        logger=mock_logger,
        trading_context=trading_context,
    )


class TestOBVBasicComputation:
    """Test OBV calculation against hand-computed values."""

    def test_obv_mixed_direction(self, mock_logger):
        """
        OBV with mixed up/down moves.

        closes  = [100,  102,  101,   103,   102]
        volumes = [  0, 1000,  500,  1500,   800]

        i=1: 102 > 100 → +1000 → OBV = 1000
        i=2: 101 < 102 →  -500 → OBV =  500
        i=3: 103 > 101 → +1500 → OBV = 2000
        i=4: 102 < 103 →  -800 → OBV = 1200
        """
        worker = _make_obv_worker(mock_logger, period=20)

        bars = make_bars_with_volume(
            closes=[100, 102, 101, 103, 102],
            volumes=[0, 1000, 500, 1500, 800],
        )
        tick = make_tick(bid=102.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert isinstance(result, WorkerResult)
        assert isinstance(result.value, float)
        assert result.value == pytest.approx(1200.0, abs=0.01)

    def test_obv_all_up(self, mock_logger):
        """
        All prices rising → all volumes added.

        closes  = [100, 101, 102, 103, 104]
        volumes = [  0, 100, 200, 300, 400]

        OBV = +100 + 200 + 300 + 400 = 1000
        """
        worker = _make_obv_worker(mock_logger, period=20)

        bars = make_bars_with_volume(
            closes=[100, 101, 102, 103, 104],
            volumes=[0, 100, 200, 300, 400],
        )
        tick = make_tick(bid=104.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.value == pytest.approx(1000.0, abs=0.01)

    def test_obv_all_down(self, mock_logger):
        """
        All prices falling → all volumes subtracted.

        closes  = [104, 103, 102, 101, 100]
        volumes = [  0, 100, 200, 300, 400]

        OBV = -100 - 200 - 300 - 400 = -1000
        """
        worker = _make_obv_worker(mock_logger, period=20)

        bars = make_bars_with_volume(
            closes=[104, 103, 102, 101, 100],
            volumes=[0, 100, 200, 300, 400],
        )
        tick = make_tick(bid=100.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.value == pytest.approx(-1000.0, abs=0.01)

    def test_obv_flat_price(self, mock_logger):
        """
        All closes identical → no volume added/subtracted → OBV = 0.

        closes  = [100, 100, 100, 100, 100]
        volumes = [  0, 100, 200, 300, 400]

        Every close[i] == close[i-1] → OBV unchanged from 0
        """
        worker = _make_obv_worker(mock_logger, period=20)

        bars = make_bars_with_volume(
            closes=[100, 100, 100, 100, 100],
            volumes=[0, 100, 200, 300, 400],
        )
        tick = make_tick(bid=100.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.value == pytest.approx(0.0, abs=0.01)


class TestOBVEdgeCases:
    """Test OBV edge cases and insufficient data handling."""

    def test_obv_insufficient_bars(self, mock_logger):
        """
        Less than 2 bars → returns 0.0 with confidence=0.0.

        This is an explicit early-return in the code.
        """
        worker = _make_obv_worker(mock_logger, period=20)

        bars = make_bars_with_volume(closes=[100], volumes=[50])
        tick = make_tick(bid=100.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.value == 0.0
        assert result.confidence == 0.0
        assert result.metadata.get("error") == "insufficient_bars"

    def test_obv_zero_volume(self, mock_logger):
        """
        All volumes zero (Forex-like) → OBV = 0 regardless of price moves.

        closes  = [100, 102, 101, 103]
        volumes = [  0,   0,   0,   0]

        Prices move but volume is always 0 → OBV stays 0.
        """
        worker = _make_obv_worker(mock_logger, period=20)

        bars = make_bars_with_volume(
            closes=[100, 102, 101, 103],
            volumes=[0, 0, 0, 0],
        )
        tick = make_tick(bid=103.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.value == pytest.approx(0.0, abs=0.01)

    def test_obv_exactly_two_bars_up(self, mock_logger):
        """
        Minimum viable OBV: 2 bars, price up.

        closes  = [100, 105]
        volumes = [  0, 500]

        i=1: 105 > 100 → +500 → OBV = 500
        """
        worker = _make_obv_worker(mock_logger, period=20)

        bars = make_bars_with_volume(
            closes=[100, 105],
            volumes=[0, 500],
        )
        tick = make_tick(bid=105.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.value == pytest.approx(500.0, abs=0.01)


class TestOBVMetadata:
    """Test OBV metadata fields and Forex market warning."""

    def test_obv_metadata_fields(self, mock_logger):
        """Metadata must contain period, timeframe, bars_used, total_volume, trend."""
        worker = _make_obv_worker(mock_logger, period=20)

        bars = make_bars_with_volume(
            closes=[100, 102, 101, 103, 102],
            volumes=[0, 1000, 500, 1500, 800],
        )
        tick = make_tick(bid=102.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.metadata["period"] == 20
        assert result.metadata["timeframe"] == "M5"
        assert result.metadata["bars_used"] == 5
        assert result.metadata["total_volume"] == pytest.approx(3800.0, abs=0.01)
        assert result.metadata["has_volume"] is True
        assert result.metadata["trend"] in ("bullish", "bearish", "neutral")

    def test_obv_has_volume_false_when_zero(self, mock_logger):
        """When all volumes are 0, has_volume must be False."""
        worker = _make_obv_worker(mock_logger, period=20)

        bars = make_bars_with_volume(
            closes=[100, 102, 101],
            volumes=[0, 0, 0],
        )
        tick = make_tick(bid=101.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.metadata["has_volume"] is False

    def test_obv_forex_warning(self, mock_logger):
        """
        Forex market type → logger.warning about zero volume.

        OBV is meaningless for Forex CFDs because volume = 0.
        The worker should warn at construction time.
        """
        forex_context = MagicMock()
        forex_context.market_type = MarketType.FOREX

        _make_obv_worker(mock_logger, trading_context=forex_context)

        # Check that a warning was issued during __init__
        mock_logger.warning.assert_called()
        warning_text = mock_logger.warning.call_args[0][0]
        assert "OBV" in warning_text or "volume" in warning_text.lower()

    def test_obv_worker_name(self, mock_logger):
        """WorkerResult.worker_name must match the worker instance name."""
        worker = OBVWorker(
            name="my_obv_instance",
            parameters={"periods": {"M5": 20}},
            logger=mock_logger,
            trading_context=None,
        )

        bars = make_bars_with_volume(
            closes=[100, 102, 101],
            volumes=[0, 500, 300],
        )
        tick = make_tick(bid=101.0)

        result = worker.compute(tick=tick, bar_history={"M5": bars}, current_bars={})

        assert result.worker_name == "my_obv_instance"
