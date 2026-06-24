"""
Generator Balance Defaults Tests.

`ensure_quote_balance` seeds a generated scenario set with a starting balance in the symbol's
quote currency, so every generation path (blocks saver + profile loader) produces a set that
validates and runs out of the box.
"""
from python.scenario.generator.balance_defaults import DEFAULT_QUOTE_BALANCE, ensure_quote_balance


class TestEnsureQuoteBalance:
    def test_adds_quote_for_usd_pair(self):
        out = ensure_quote_balance({}, 'ETHUSD')
        assert out['balances'] == {'USD': DEFAULT_QUOTE_BALANCE}

    def test_adds_quote_for_jpy_pair(self):
        out = ensure_quote_balance({}, 'USDJPY')
        assert out['balances'] == {'JPY': DEFAULT_QUOTE_BALANCE}

    def test_none_config_creates_balances(self):
        assert ensure_quote_balance(None, 'BTCUSD')['balances'] == {'USD': DEFAULT_QUOTE_BALANCE}

    def test_does_not_clobber_existing_quote_balance(self):
        out = ensure_quote_balance({'balances': {'USD': 500.0}}, 'ETHUSD')
        assert out['balances']['USD'] == 500.0

    def test_adds_quote_alongside_other_currency(self):
        out = ensure_quote_balance({'balances': {'EUR': 200.0}}, 'ETHUSD')
        assert out['balances'] == {'EUR': 200.0, 'USD': DEFAULT_QUOTE_BALANCE}

    def test_preserves_other_keys(self):
        out = ensure_quote_balance({'account_currency': 'USD', 'balances': {}}, 'ETHUSD')
        assert out['account_currency'] == 'USD'
        assert out['balances'] == {'USD': DEFAULT_QUOTE_BALANCE}

    def test_does_not_mutate_input(self):
        original = {'balances': {'EUR': 1.0}}
        ensure_quote_balance(original, 'ETHUSD')
        assert original == {'balances': {'EUR': 1.0}}   # input untouched (new dict returned)
