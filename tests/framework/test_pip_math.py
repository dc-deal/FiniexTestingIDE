"""
FiniexTestingIDE - Pip-Size Derivation Tests (#167)

Tests the single authoritative pip-size derivation: the market-type-aware rule
(Forex pipette convention vs. crypto tick), its float-cleanliness, the config
resolution (MarketConfigManager.get_pip_mode), and the adapter end-to-end
(get_pip_size / get_pip_mode) on the real broker configs — including the
ETHUSD / ADAUSD cases that exposed the original spec inconsistency.
"""

import json

import pytest

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter
from python.framework.trading_env.adapters.mt5_adapter import Mt5Adapter
from python.framework.types.config_types.market_config_types import PipMode
from python.framework.utils.trading_math.pip_math import derive_pip_size


class TestDeriveFractionalPip:
    """FRACTIONAL_PIP (Forex): pipette brokers (5-/3-digit) → tick*10; whole-pip → tick."""

    def test_eurusd_5_digit(self):
        assert derive_pip_size(0.00001, 5, PipMode.FRACTIONAL_PIP) == pytest.approx(0.0001)

    def test_usdjpy_3_digit(self):
        assert derive_pip_size(0.001, 3, PipMode.FRACTIONAL_PIP) == pytest.approx(0.01)

    def test_4_digit_whole_pip_is_tick(self):
        # A true 4-digit FX broker has no pipette → pip = tick.
        assert derive_pip_size(0.0001, 4, PipMode.FRACTIONAL_PIP) == pytest.approx(0.0001)

    def test_2_digit_whole_pip_is_tick(self):
        assert derive_pip_size(0.01, 2, PipMode.FRACTIONAL_PIP) == pytest.approx(0.01)


class TestDeriveTick:
    """TICK (crypto / others): no pip concept → the broker tick IS the unit."""

    def test_btcusd(self):
        assert derive_pip_size(0.1, 1, PipMode.TICK) == 0.1

    def test_ethusd_even_digits(self):
        # The case that exposed the inconsistency: even digits, crypto → tick, not tick*10.
        assert derive_pip_size(0.01, 2, PipMode.TICK) == 0.01

    def test_adausd_float_clean(self):
        # tick*10 would yield 9.999999999999999e-06; the tick path stays exact (#368).
        assert derive_pip_size(0.000001, 6, PipMode.TICK) == 1e-06

    def test_xrpusd_odd_digits_still_tick(self):
        # digits=5 is only coincidentally 'odd' on crypto — TICK mode ignores it.
        assert derive_pip_size(0.00001, 5, PipMode.TICK) == 0.00001


class TestPipModeLabel:
    """The report unit label carried by each mode."""

    def test_fractional_pip_label(self):
        assert PipMode.FRACTIONAL_PIP.unit_label == 'pip'

    def test_tick_label(self):
        assert PipMode.TICK.unit_label == 'tick'


class TestMarketConfigResolution:
    """MarketConfigManager.get_pip_mode resolves market_type → pip_mode."""

    def test_forex_broker_is_fractional_pip(self):
        assert MarketConfigManager().get_pip_mode('mt5') is PipMode.FRACTIONAL_PIP

    def test_crypto_broker_is_tick(self):
        assert MarketConfigManager().get_pip_mode('kraken_spot') is PipMode.TICK


class TestAdapterEndToEnd:
    """get_pip_size / get_pip_mode on the real broker configs (authoritative path)."""

    def _kraken(self) -> KrakenAdapter:
        with open('configs/brokers/kraken/kraken_spot_broker_config.json') as f:
            return KrakenAdapter(json.load(f))

    def _mt5(self) -> Mt5Adapter:
        with open('configs/brokers/mt5/mt5_broker_config.json') as f:
            return Mt5Adapter(json.load(f))

    def test_kraken_crypto_pips_are_tick(self):
        k = self._kraken()
        assert k.get_pip_mode() is PipMode.TICK
        assert k.get_pip_mode().unit_label == 'tick'
        assert k.get_pip_size('BTCUSD') == 0.1
        assert k.get_pip_size('ETHUSD') == 0.01
        assert k.get_pip_size('ADAUSD') == 1e-06

    def test_mt5_forex_pips_are_fractional(self):
        m = self._mt5()
        assert m.get_pip_mode() is PipMode.FRACTIONAL_PIP
        assert m.get_pip_mode().unit_label == 'pip'
        assert m.get_pip_size('EURUSD') == pytest.approx(0.0001)
        assert m.get_pip_size('USDJPY') == pytest.approx(0.01)
