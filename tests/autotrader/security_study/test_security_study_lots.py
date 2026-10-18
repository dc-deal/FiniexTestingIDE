"""
FiniexTestingIDE - Security Study Order Sizes (#358)

A phase states its order's size as a rule, resolved the moment the order is sent. The rules decide
whether an order is refused as intended or costs money, so each is held to what it promises: the
smallest order, half of it (refused as an invalid lot), twice what the quote balance buys (refused
for funds), a sale of more base than is held (refused for funds) — every size on the venue's grid
of the minimum plus whole steps.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from python.framework.decision_logic.core.security_cert_study.security_cert_study import (
    SecurityCertStudy,
    _on_step,
)
from python.framework.types.acceptance_study_types.security_cert_types import LotRule

_BTCUSD = SimpleNamespace(volume_min=6e-05, volume_step=1e-08, volume_max=10000.0,
                          base_currency='BTC', quote_currency='USD')


def _study(quote: float, base: float, mid: float = 100_000.0) -> SecurityCertStudy:
    """A study with only what sizing an order reads — its account and the last mid."""
    study = object.__new__(SecurityCertStudy)
    study.trading_api = MagicMock()
    study.trading_api.get_asset_balance.side_effect = lambda ccy: {'USD': quote, 'BTC': base}[ccy]
    study._last_mid = mid
    return study


class TestTheVenuesGrid:

    @pytest.mark.parametrize('wanted, expected', [
        (6e-05, 6e-05), (0.0001234567891, 0.00012346), (1e-06, 6e-05)])
    def test_a_size_is_rounded_up_onto_the_minimum_plus_whole_steps(self, wanted, expected):
        assert _on_step(wanted, _BTCUSD) == pytest.approx(expected)

    def test_a_coarse_grid_rounds_to_whole_steps(self):
        coarse = SimpleNamespace(volume_min=20.0, volume_step=1.0, volume_max=1e6)
        assert (_on_step(35.2, coarse), _on_step(20.0, coarse)) == (36.0, 20.0)


class TestEachRuleMeansWhatItSays:

    def test_the_minimum_is_the_venue_s_smallest_order(self):
        assert _study(23.72, 0.0)._lots_for(LotRule.MIN_LOT, _BTCUSD) == 6e-05

    def test_below_the_minimum_is_under_it(self):
        assert _study(23.72, 0.0)._lots_for(LotRule.BELOW_MIN, _BTCUSD) < _BTCUSD.volume_min

    def test_beyond_funds_costs_more_than_the_quote_balance(self):
        lots = _study(23.72, 0.0)._lots_for(LotRule.BEYOND_FUNDS, _BTCUSD)
        assert lots * 100_000.0 > 23.72 and lots <= _BTCUSD.volume_max

    def test_beyond_funds_stays_under_the_venue_s_maximum(self):
        lots = _study(5e9, 0.0)._lots_for(LotRule.BEYOND_FUNDS, _BTCUSD)
        assert lots == _BTCUSD.volume_max

    def test_beyond_the_holding_sells_more_base_than_is_held(self):
        assert _study(23.72, 0.0002)._lots_for(LotRule.BEYOND_HOLDING, _BTCUSD) > 0.0002

    def test_beyond_an_empty_holding_is_still_a_valid_size(self):
        """Refused for funds — not as an invalid lot, which would not count toward the cooldown."""
        lots = _study(23.72, 0.0)._lots_for(LotRule.BEYOND_HOLDING, _BTCUSD)
        assert lots >= _BTCUSD.volume_min
