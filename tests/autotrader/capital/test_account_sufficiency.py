"""
FiniexTestingIDE - Account Sufficiency Tests (#489)

A bot whose account can fund no order at all has nothing to do, and it should say so at boot
rather than at its first signal.

The criterion is the AND of both sides, and that is the part worth testing: a spot bot may
legitimately start holding only the BASE asset and open by SELLING — the Field Study funds both
sides on purpose — so an empty quote balance alone must not refuse a boot. And the BUY side
needs a price the boot does not always have, which is why an unjudgeable side can never complete
the AND.
"""

import pytest

from python.framework.testing.mock_broker_adapter import MockBrokerAdapter
from python.framework.validators.capital_validator import (
    check_account_sufficiency,
    describe_missing_reference_price,
)

# BTCUSD from the real Kraken config: volume_min 0.00005 BTC.
_PRICE = 104350.0
# 0.00005 * 104350 = 5.2175 USD for one minimum-volume BUY.
_MINIMUM_COST = 5.2175


@pytest.fixture(scope='module')
def symbol_spec():
    """The real BTCUSD specification the mock carries. Returns: the spec."""
    return MockBrokerAdapter().get_symbol_specification('BTCUSD')


class TestTheRefusal:
    """Only an account that can do neither is refused."""

    def test_an_account_that_can_neither_buy_nor_sell_is_refused(self, symbol_spec):
        error = check_account_sufficiency(
            {'USD': 3.00, 'BTC': 0.00001}, symbol_spec, _PRICE)

        assert error is not None
        # Both sides' numbers, so an operator can see WHICH one was short.
        assert '3.00000000' in error and '0.00001000' in error
        # Never scientific notation: an operator should not have to decode `5e-05`.
        assert 'e-0' not in error
        assert 'BTC' in error and 'USD' in error

    def test_an_account_that_can_buy_passes(self, symbol_spec):
        assert check_account_sufficiency(
            {'USD': 6.20, 'BTC': 0.0}, symbol_spec, _PRICE) is None

    def test_an_account_holding_only_base_passes(self, symbol_spec):
        """The Field Study shape: no quote at all, but it can open by selling."""
        assert check_account_sufficiency(
            {'USD': 0.0, 'BTC': 0.05}, symbol_spec, _PRICE) is None

    def test_exactly_the_minimum_is_enough_on_either_side(self, symbol_spec):
        """The boundary is `>=`, not `>` — an account that can place exactly one order may."""
        assert check_account_sufficiency(
            {'USD': _MINIMUM_COST, 'BTC': 0.0}, symbol_spec, _PRICE) is None
        assert check_account_sufficiency(
            {'USD': 0.0, 'BTC': symbol_spec.volume_min}, symbol_spec, _PRICE) is None

    def test_a_missing_currency_reads_as_zero(self, symbol_spec):
        """A balances dict that never mentions an asset holds none of it."""
        assert check_account_sufficiency({}, symbol_spec, _PRICE) is not None


class TestTheUnjudgeableBuySide:
    """No price means the buy side is UNKNOWN, and an unknown side cannot complete the AND."""

    def test_without_a_price_a_short_account_is_not_refused(self, symbol_spec):
        """
        The boot proceeds rather than refusing over an invented number.

        Guessing a price here would refuse a live session on arithmetic nobody supplied; the
        first order attempt refuses it instead, where a real price exists.
        """
        assert check_account_sufficiency(
            {'USD': 3.00, 'BTC': 0.0}, symbol_spec, None) is None

    def test_a_nonsense_price_counts_as_no_price(self, symbol_spec):
        assert check_account_sufficiency(
            {'USD': 3.00, 'BTC': 0.0}, symbol_spec, 0.0) is None
        assert check_account_sufficiency(
            {'USD': 3.00, 'BTC': 0.0}, symbol_spec, -1.0) is None

    def test_the_sell_side_still_decides_without_a_price(self, symbol_spec):
        """`volume_min` is quoted in base units, so that half needs no price at all."""
        assert check_account_sufficiency(
            {'USD': 0.0, 'BTC': 0.05}, symbol_spec, None) is None

    def test_the_absence_is_spoken_not_swallowed(self):
        """
        A check that silently does not run reads exactly like one that ran and passed.

        The caller says this into the session channel whenever the price was missing, so the
        operator never carries a boot guard that guards nothing.
        """
        message = describe_missing_reference_price('BTCUSD')

        assert 'BTCUSD' in message
        assert 'SELL' in message and 'BUY' in message
