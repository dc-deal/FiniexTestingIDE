"""
The one account-currency rule both pipelines share (#615).

The rule existed twice, and the copies differed at spot: the backtest normalised a currency other
than the quote to the quote, a session kept it. One profile run as a session and as a backtest
could then account in two currencies. These tests hold the shared rule in both account models, and
hold a session's phase 3 to it on the case the two copies answered differently.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from python.framework.autotrader.startup.autotrader_startup import _resolve_account_model
from python.framework.types.config_types.market_config_types import TradingModel
from python.framework.utils.account_currency_resolver import resolve_account_currency

_BOTH_MODELS = [TradingModel.SPOT, TradingModel.MARGIN]


class TestTheRule:
    """resolve_account_currency, in both account models."""

    @pytest.mark.parametrize('trading_model', _BOTH_MODELS)
    def test_the_quote_wins_when_the_balances_hold_it(self, trading_model):
        resolution = resolve_account_currency(
            None, {'USD': 1000.0, 'BTC': 0.0}, 'BTC', 'USD', trading_model)
        assert resolution.account_currency == 'USD'
        assert resolution.normalized_from is None

    @pytest.mark.parametrize('trading_model, expected, normalized_from', [
        (TradingModel.SPOT, 'USD', 'BTC'),
        (TradingModel.MARGIN, 'BTC', None),
    ])
    def test_a_configured_base_currency_is_replaced_at_spot_only(
            self, trading_model, expected, normalized_from):
        resolution = resolve_account_currency(
            'BTC', {'USD': 1000.0, 'BTC': 0.0}, 'BTC', 'USD', trading_model)
        assert resolution.account_currency == expected
        assert resolution.normalized_from == normalized_from

    @pytest.mark.parametrize('trading_model, expected, normalized_from', [
        (TradingModel.SPOT, 'USD', 'BTC'),
        (TradingModel.MARGIN, 'BTC', None),
    ])
    def test_a_base_only_balance_is_replaced_at_spot_only(
            self, trading_model, expected, normalized_from):
        resolution = resolve_account_currency(None, {'BTC': 1.0}, 'BTC', 'USD', trading_model)
        assert resolution.account_currency == expected
        assert resolution.normalized_from == normalized_from

    def test_the_only_balance_is_taken_when_it_names_neither(self):
        resolution = resolve_account_currency(
            None, {'GBP': 10000.0}, 'EUR', 'USD', TradingModel.MARGIN)
        assert resolution.account_currency == 'GBP'

    @pytest.mark.parametrize('trading_model', _BOTH_MODELS)
    def test_balances_that_name_neither_leave_it_open(self, trading_model):
        resolution = resolve_account_currency(
            None, {'GBP': 1.0, 'CHF': 1.0}, 'EUR', 'USD', trading_model)
        assert resolution.account_currency is None

    def test_a_configured_currency_wins_over_the_balances(self):
        resolution = resolve_account_currency(
            'GBP', {'USD': 1000.0}, 'EUR', 'USD', TradingModel.MARGIN)
        assert resolution.account_currency == 'GBP'


class TestTheSessionFollowsTheRule:
    """A session's phase 3 asks the same rule — the backtest's tests cover the other caller."""

    @staticmethod
    def _resolve(broker_type, symbol, base, quote, balances, explicit):
        config = SimpleNamespace(broker_type=broker_type, symbol=symbol, profile_name='probe_profile')
        spec = SimpleNamespace(base_currency=base, quote_currency=quote)
        broker_config = SimpleNamespace(
            adapter=SimpleNamespace(get_symbol_specification=lambda _symbol: spec))
        logger = MagicMock()
        account = _resolve_account_model(config, broker_config, balances, explicit, logger)
        return account, logger

    def test_a_spot_session_with_a_base_currency_settles_in_the_quote(self):
        # The case the two copies answered differently: the backtest said USD, a session BTC.
        account, logger = self._resolve(
            'kraken_spot', 'BTCUSD', 'BTC', 'USD', {'USD': 1000.0, 'BTC': 0.0}, 'BTC')
        assert account.account_currency == 'USD'
        logger.warning.assert_called_once()

    def test_a_margin_session_keeps_its_base_currency(self):
        account, logger = self._resolve('mt5', 'GBPUSD', 'GBP', 'USD', {'GBP': 10000.0}, 'GBP')
        assert account.account_currency == 'GBP'
        logger.warning.assert_not_called()

    def test_balances_that_name_neither_refuse_the_start(self):
        with pytest.raises(ValueError, match='declares no account currency'):
            self._resolve('mt5', 'EURUSD', 'EUR', 'USD', {'GBP': 1.0, 'CHF': 1.0}, None)
