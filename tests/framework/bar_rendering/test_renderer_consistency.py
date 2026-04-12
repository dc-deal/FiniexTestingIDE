"""
Bar Renderer Consistency Tests.

Verifies that BarRenderer (tick-by-tick, production path) and
VectorizedBarRenderer (pandas batch) produce identical output
for the same input ticks.

Both renderers MUST agree on:
- Bar boundaries (timestamps)
- OHLC values
- Tick counts
- Volume aggregation
- Gap handling (no synthetic bars)
"""

from datetime import datetime, timezone
from typing import Dict, List, Set

import pandas as pd
import pytest

from python.data_management.importers.vectorized_bar_renderer import VectorizedBarRenderer
from python.framework.bars.bar_renderer import BarRenderer
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.market_types.market_data_types import Bar, TickData
from python.framework.utils.timeframe_config_utils import TimeframeConfig

from tests.framework.bar_rendering.conftest import (
    generate_boundary_ticks,
    generate_ticks,
    generate_ticks_with_gap,
    ticks_to_dataframe,
)


# =============================================================================
# HELPERS
# =============================================================================

def _create_bar_renderer() -> BarRenderer:
    """Create a BarRenderer with a test logger."""
    logger = ScenarioLogger(
        scenario_set_name='test_consistency',
        scenario_name='renderer_comparison',
        run_timestamp=datetime.now(tz=timezone.utc),
    )
    return BarRenderer(logger=logger)


def _render_with_bar_renderer(
    ticks: List[TickData], timeframe: str
) -> List[Dict]:
    """
    Render bars using BarRenderer.update_current_bars() — the production path.

    Feeds ticks one-by-one and collects completed bars from history,
    exactly as process_tick_loop.py does it.

    Args:
        ticks: List of TickData objects
        timeframe: Single timeframe to render

    Returns:
        List of dicts with normalized bar data for comparison
    """
    renderer = _create_bar_renderer()
    required_timeframes: Set[str] = {timeframe}
    symbol = ticks[0].symbol

    for tick in ticks:
        renderer.update_current_bars(tick, required_timeframes)

    # Collect completed (archived) bars
    completed = renderer.get_bar_history(symbol, timeframe)

    # Also include the current (potentially incomplete) bar — VectorizedBarRenderer
    # includes it as the last bar since it processes all ticks at once
    current = renderer.get_current_bar(symbol, timeframe)
    all_bars = list(completed)
    if current is not None:
        all_bars.append(current)

    return _bars_to_comparable(all_bars)


def _render_with_vectorized(
    ticks: List[TickData], symbol: str, timeframe: str
) -> List[Dict]:
    """
    Render bars using VectorizedBarRenderer for a single timeframe.

    Args:
        ticks: List of TickData objects
        symbol: Trading symbol
        timeframe: Single timeframe to render

    Returns:
        List of dicts with normalized bar data for comparison
    """
    renderer = VectorizedBarRenderer(symbol=symbol, broker_type='kraken_spot')
    df = ticks_to_dataframe(ticks)
    all_bars = renderer.render_all_timeframes(df)
    bars_df = all_bars[timeframe]
    return _dataframe_to_comparable(bars_df)


def _bars_to_comparable(bars: List[Bar]) -> List[Dict]:
    """
    Normalize BarRenderer output for comparison.

    Returns:
        List of dicts with: timestamp, open, high, low, close, volume, tick_count
    """
    result = []
    for bar in bars:
        result.append({
            'timestamp': bar.timestamp,
            'open': bar.open,
            'high': bar.high,
            'low': bar.low,
            'close': bar.close,
            'volume': bar.volume,
            'tick_count': bar.tick_count,
        })
    return result


def _dataframe_to_comparable(df: pd.DataFrame) -> List[Dict]:
    """
    Normalize VectorizedBarRenderer output for comparison.

    Returns:
        List of dicts with: timestamp, open, high, low, close, volume, tick_count
    """
    result = []
    for _, row in df.iterrows():
        # VectorizedBarRenderer timestamp is pandas Timestamp → normalize to ISO
        ts = row['timestamp']
        if isinstance(ts, pd.Timestamp):
            ts_iso = ts.isoformat()
        else:
            ts_iso = str(ts)

        result.append({
            'timestamp': ts_iso,
            'open': float(row['open']),
            'high': float(row['high']),
            'low': float(row['low']),
            'close': float(row['close']),
            'volume': float(row['volume']),
            'tick_count': int(row['tick_count']),
        })
    return result


def _assert_bars_equal(
    tick_bars: List[Dict],
    vec_bars: List[Dict],
    timeframe: str,
) -> None:
    """
    Assert that two lists of normalized bars are identical.

    Args:
        tick_bars: Bars from BarRenderer
        vec_bars: Bars from VectorizedBarRenderer
        timeframe: Timeframe label for error messages
    """
    assert len(tick_bars) == len(vec_bars), (
        f"[{timeframe}] Bar count mismatch: "
        f"BarRenderer={len(tick_bars)}, Vectorized={len(vec_bars)}"
    )

    for i, (tb, vb) in enumerate(zip(tick_bars, vec_bars)):
        assert tb['timestamp'] == vb['timestamp'], (
            f"[{timeframe}] Bar {i} timestamp mismatch: "
            f"BarRenderer={tb['timestamp']}, Vectorized={vb['timestamp']}"
        )
        assert tb['open'] == pytest.approx(vb['open'], rel=1e-10), (
            f"[{timeframe}] Bar {i} open mismatch: "
            f"BarRenderer={tb['open']}, Vectorized={vb['open']}"
        )
        assert tb['high'] == pytest.approx(vb['high'], rel=1e-10), (
            f"[{timeframe}] Bar {i} high mismatch: "
            f"BarRenderer={tb['high']}, Vectorized={vb['high']}"
        )
        assert tb['low'] == pytest.approx(vb['low'], rel=1e-10), (
            f"[{timeframe}] Bar {i} low mismatch: "
            f"BarRenderer={tb['low']}, Vectorized={vb['low']}"
        )
        assert tb['close'] == pytest.approx(vb['close'], rel=1e-10), (
            f"[{timeframe}] Bar {i} close mismatch: "
            f"BarRenderer={tb['close']}, Vectorized={vb['close']}"
        )
        assert tb['volume'] == pytest.approx(vb['volume'], rel=1e-10), (
            f"[{timeframe}] Bar {i} volume mismatch: "
            f"BarRenderer={tb['volume']}, Vectorized={vb['volume']}"
        )
        assert tb['tick_count'] == vb['tick_count'], (
            f"[{timeframe}] Bar {i} tick_count mismatch: "
            f"BarRenderer={tb['tick_count']}, Vectorized={vb['tick_count']}"
        )


# =============================================================================
# TESTS
# =============================================================================

class TestRendererConsistency:
    """Verify BarRenderer and VectorizedBarRenderer produce identical bars."""

    @pytest.mark.parametrize('timeframe', ['M1', 'M5', 'M15', 'M30'])
    def test_standard_ticks_short_timeframes(self, timeframe: str) -> None:
        """Both renderers agree on M1-M30 for a standard tick sequence."""
        ticks = generate_ticks(count=200, interval_seconds=3)
        symbol = ticks[0].symbol

        tick_bars = _render_with_bar_renderer(ticks, timeframe)
        vec_bars = _render_with_vectorized(ticks, symbol, timeframe)

        assert len(tick_bars) > 0, f"No bars produced for {timeframe}"
        _assert_bars_equal(tick_bars, vec_bars, timeframe)

    @pytest.mark.parametrize('timeframe', ['H1', 'H4'])
    def test_standard_ticks_long_timeframes(self, timeframe: str) -> None:
        """Both renderers agree on H1/H4 with enough ticks to span multiple bars."""
        # H1 needs >3600s of ticks, H4 needs >14400s
        # 5000 ticks * 3s = 15000s = ~4.2 hours → covers H1 + H4
        ticks = generate_ticks(count=5000, interval_seconds=3)
        symbol = ticks[0].symbol

        tick_bars = _render_with_bar_renderer(ticks, timeframe)
        vec_bars = _render_with_vectorized(ticks, symbol, timeframe)

        assert len(tick_bars) > 0, f"No bars produced for {timeframe}"
        _assert_bars_equal(tick_bars, vec_bars, timeframe)

    def test_gap_handling_m5(self) -> None:
        """Both renderers skip gaps identically (no synthetic bars)."""
        ticks = generate_ticks_with_gap(
            ticks_before_gap=60,
            gap_minutes=15,
            ticks_after_gap=60,
            interval_seconds=3,
        )
        symbol = ticks[0].symbol
        timeframe = 'M5'

        tick_bars = _render_with_bar_renderer(ticks, timeframe)
        vec_bars = _render_with_vectorized(ticks, symbol, timeframe)

        assert len(tick_bars) > 0
        _assert_bars_equal(tick_bars, vec_bars, timeframe)

    def test_gap_handling_m1(self) -> None:
        """Gap test for M1 — finest granularity, most bars affected."""
        ticks = generate_ticks_with_gap(
            ticks_before_gap=30,
            gap_minutes=5,
            ticks_after_gap=30,
            interval_seconds=2,
        )
        symbol = ticks[0].symbol
        timeframe = 'M1'

        tick_bars = _render_with_bar_renderer(ticks, timeframe)
        vec_bars = _render_with_vectorized(ticks, symbol, timeframe)

        assert len(tick_bars) > 0
        _assert_bars_equal(tick_bars, vec_bars, timeframe)

    @pytest.mark.parametrize('timeframe_minutes,timeframe', [
        (1, 'M1'), (5, 'M5'), (15, 'M15'),
    ])
    def test_boundary_ticks(
        self, timeframe_minutes: int, timeframe: str
    ) -> None:
        """Ticks exactly on bar boundaries are assigned correctly by both."""
        ticks = generate_boundary_ticks(
            timeframe_minutes=timeframe_minutes,
            start=datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        )
        symbol = ticks[0].symbol

        tick_bars = _render_with_bar_renderer(ticks, timeframe)
        vec_bars = _render_with_vectorized(ticks, symbol, timeframe)

        assert len(tick_bars) > 0
        _assert_bars_equal(tick_bars, vec_bars, timeframe)

    def test_single_tick_per_bar(self) -> None:
        """Bars with exactly one tick produce identical OHLC (all same price)."""
        # One tick per minute → one tick per M1 bar
        ticks = generate_ticks(count=5, interval_seconds=60)
        symbol = ticks[0].symbol

        tick_bars = _render_with_bar_renderer(ticks, 'M1')
        vec_bars = _render_with_vectorized(ticks, symbol, 'M1')

        assert len(tick_bars) > 0
        _assert_bars_equal(tick_bars, vec_bars, 'M1')

        # Each bar should have tick_count == 1
        for bar in tick_bars:
            assert bar['tick_count'] == 1

    def test_all_timeframes_bar_count(self) -> None:
        """Sanity check: both renderers produce same number of bars per timeframe."""
        # Large dataset: 10000 ticks * 3s = 30000s ≈ 8.3 hours
        ticks = generate_ticks(count=10000, interval_seconds=3)
        symbol = ticks[0].symbol

        renderer_vec = VectorizedBarRenderer(symbol=symbol, broker_type='kraken_spot')
        df = ticks_to_dataframe(ticks)
        all_vec_bars = renderer_vec.render_all_timeframes(df)

        # Skip D1 — 8.3 hours only produces 1 bar, not meaningful for count comparison
        for timeframe in ['M1', 'M5', 'M15', 'M30', 'H1', 'H4']:
            br_bars = _render_with_bar_renderer(ticks, timeframe)
            vec_count = len(all_vec_bars[timeframe])

            assert len(br_bars) == vec_count, (
                f"[{timeframe}] Bar count mismatch: "
                f"BarRenderer={len(br_bars)}, Vectorized={vec_count}"
            )

    def test_volume_aggregation(self) -> None:
        """Volume is summed identically across both renderers."""
        ticks = generate_ticks(count=100, interval_seconds=3, volume_per_tick=0.25)
        symbol = ticks[0].symbol
        timeframe = 'M5'

        tick_bars = _render_with_bar_renderer(ticks, timeframe)
        vec_bars = _render_with_vectorized(ticks, symbol, timeframe)

        _assert_bars_equal(tick_bars, vec_bars, timeframe)

        # Verify total volume matches
        total_tick_volume = sum(t.volume for t in ticks)
        total_br_volume = sum(b['volume'] for b in tick_bars)
        total_vec_volume = sum(b['volume'] for b in vec_bars)

        assert total_br_volume == pytest.approx(total_tick_volume, rel=1e-10)
        assert total_vec_volume == pytest.approx(total_tick_volume, rel=1e-10)

    def test_forex_zero_volume(self) -> None:
        """Forex ticks with zero volume are handled consistently."""
        ticks = generate_ticks(
            symbol='EURUSD',
            count=100,
            interval_seconds=3,
            bid_start=1.08000,
            spread=0.00010,
            price_step=0.00001,
            volume_per_tick=0.0,
        )
        symbol = ticks[0].symbol
        timeframe = 'M5'

        tick_bars = _render_with_bar_renderer(ticks, timeframe)
        vec_bars = _render_with_vectorized(ticks, symbol, timeframe)

        _assert_bars_equal(tick_bars, vec_bars, timeframe)

        # All volumes should be zero
        for bar in tick_bars:
            assert bar['volume'] == 0.0