"""
A NaN must never reach a decision.

The ATR is undefined for the first `period` bars of any series, and the indicator library
says so honestly by returning NaN there (§45's sibling rule: not knowing is a state, and it
must be said out loud). Everything that CONSUMES an ATR therefore has to survive that
warmup — and two places did not.

Measured 2026-09-14, and it produced no error anywhere:

    a period lying entirely inside the ATR warmup averages to NaN
      -> np.mean over the period averages does NOT skip it
      -> every period's ATR ratio becomes NaN
      -> the percentile threshold becomes NaN, so `p.atr <= threshold` is False everywhere
      -> the splitter finds no candidate, falls into its forced branch,
         and cuts at the minimum block size from end to end

The visible result was a generator profile with 87 four-hour blocks instead of 14 meaningful
ones, every block labelled `very_high`, every `atr_at_split` NaN. Nothing raised, nothing
warned, and the file looked structurally valid.

These tests run on a synthetic frame in a temporary directory — no archive, no parquet from
/app, and therefore no bridged-mount cost (§42).
"""

import numpy as np
import pandas as pd
import pytest

from python.framework.discoveries.extreme_move_scanner import ExtremeMoveScanner
from python.framework.discoveries.volatility_profile_analyzer.volatility_profile_analyzer import (
    VolatilityProfileAnalyzer,
)

_BARS = 600          # enough for several one-hour periods at M5
_PERIOD_M5 = 5


@pytest.fixture(scope='module')
def synthetic_bar_file(tmp_path_factory):
    """
    A well-formed M5 series whose FIRST bars necessarily sit inside the ATR warmup.

    Deterministic — a fixed generator seed, so a failure is reproducible rather than a
    rumour about one unlucky run.
    """
    rng = np.random.default_rng(4242)
    closes = 100.0 + np.cumsum(rng.normal(0, 0.15, _BARS))
    frame = pd.DataFrame({
        'timestamp': pd.date_range('2026-03-01', periods=_BARS, freq=f'{_PERIOD_M5}min', tz='UTC'),
        'open': closes,
        'high': closes + np.abs(rng.normal(0, 0.08, _BARS)),
        'low': closes - np.abs(rng.normal(0, 0.08, _BARS)),
        'close': closes,
        'volume': rng.integers(1, 100, _BARS).astype(float),
        'tick_count': rng.integers(5, 60, _BARS).astype(int),
    })
    path = tmp_path_factory.mktemp('bars') / 'synthetic_M5.parquet'
    frame.to_parquet(path)
    return str(path)


@pytest.fixture
def analyzer(synthetic_bar_file, monkeypatch):
    """An analyzer pointed at the synthetic file instead of the archive."""
    instance = VolatilityProfileAnalyzer()
    monkeypatch.setattr(
        instance._bar_index, 'get_bar_file',
        lambda broker_type, symbol, timeframe: synthetic_bar_file,
    )
    return instance


class TestTheVolatilityProfileRefusesToCarryANaN:

    def test_no_period_reports_an_undefined_atr(self, analyzer):
        periods = analyzer.get_periods('kraken_spot', 'BTCUSD', 'M5')

        assert periods, 'the fixture must produce periods, or this proves nothing'
        undefined = [p for p in periods if not np.isfinite(p.atr)]
        assert not undefined, (
            f'{len(undefined)} of {len(periods)} periods carry a non-finite ATR — '
            f'one of them is enough to turn every ratio into NaN'
        )

    def test_the_regime_classification_still_separates(self, analyzer):
        """
        The symptom that made the defect visible in a file: EVERY block came out
        `very_high`, because a NaN ratio falls through every threshold comparison.
        """
        periods = analyzer.get_periods('kraken_spot', 'BTCUSD', 'M5')
        regimes = {p.regime for p in periods}

        assert len(regimes) > 1, (
            f'all {len(periods)} periods landed in one regime ({regimes}) — the ratios '
            f'they are classified by are not separating'
        )

    def test_the_profiles_own_atr_statistics_are_numbers(self, analyzer):
        profile = analyzer.build_profile('kraken_spot', 'BTCUSD', 'M5')

        for name in ('atr_min', 'atr_max', 'atr_avg', 'atr_std', 'atr_percent'):
            value = getattr(profile, name)
            assert np.isfinite(value), f'{name} is {value}'


class TestTheExtremeMoveScannerRefusesAWarmupWindow:

    def test_a_window_with_no_defined_atr_is_skipped_not_measured(self):
        """
        The guard was `if window_atr <= 0: continue`, and NaN <= 0 is False — so a window
        still inside the warmup passed the guard and carried its NaN into every
        measurement below it. Pinned on the arithmetic rather than on a full scan,
        because that is the whole of the bug.
        """
        # A partly-warm window still yields a usable number from the bars that HAVE one,
        # which is why nanmean is the right averaging and a plain mean is not: the latter
        # returns NaN, and NaN survives the `<= 0` guard.
        partial_window = np.array([np.nan, 2.0, 4.0])

        assert float(np.nanmean(partial_window)) == pytest.approx(3.0)
        assert not np.isfinite(float(np.mean(partial_window)))
        # ...and a NaN would indeed slip past the guard that was there before.
        assert not (float(np.mean(partial_window)) <= 0)

    def test_a_scan_over_a_short_series_produces_no_undefined_move(
        self, synthetic_bar_file, monkeypatch
    ):
        scanner = ExtremeMoveScanner()
        monkeypatch.setattr(
            scanner._get_bar_index(), 'get_bar_file',
            lambda broker_type, symbol, timeframe: synthetic_bar_file,
        )
        result = scanner.scan('kraken_spot', 'BTCUSD', 'M5')

        for move in list(result.longs) + list(result.shorts):
            assert np.isfinite(move.move_atr_multiple), f'{move} carries an undefined ATR multiple'
