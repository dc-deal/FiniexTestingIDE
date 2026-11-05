"""
The Algo Position — what an algo receives for an open position (#653).

The mask is read-only, shows only values, computes what moves with the market from the latest
quote when read, and is reused from read to read. These tests hold each of those against a real
portfolio in both account models; the observer probe's suite holds the same against whole runs.
"""

from datetime import datetime, timedelta, timezone
from typing import List

import pytest

from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.trading_env.algo_masks.algo_position import AlgoPosition
from python.framework.trading_env.portfolio_manager import PortfolioManager
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.portfolio_types.portfolio_types import Position, PositionStatus
from python.framework.types.trading_env_types.order_types import OrderDirection
from tests.shared.null_logger import NullLogger

_START = datetime(2026, 1, 12, 10, tzinfo=timezone.utc)
_MARKETS = {
    True: {'config': 'configs/brokers/kraken/kraken_spot_broker_config.json', 'symbol': 'ETHUSD',
           'price': 2000.0, 'spread': 0.5},
    False: {'config': 'configs/brokers/mt5/mt5_broker_config.json', 'symbol': 'EURUSD',
            'price': 1.10, 'spread': 0.00012},
}


def _portfolio(spot_mode: bool, portfolio_cache: bool, clock: List[datetime]) -> PortfolioManager:
    """A portfolio of one market, holding one LONG and one SHORT position."""
    market = _MARKETS[spot_mode]
    broker_config = BrokerConfigFactory.build_broker_config(market['config'])
    spec = broker_config.get_symbol_specification(market['symbol'])
    portfolio = PortfolioManager(
        logger=NullLogger(), initial_balance=100_000.0, account_currency='USD',
        broker_config=broker_config, leverage=100, margin_call_level=50.0, stop_out_level=20.0,
        spot_mode=spot_mode, clock_fn=lambda: clock[0], portfolio_cache=portfolio_cache)
    for position_id, direction in (('long', OrderDirection.LONG), ('short', OrderDirection.SHORT)):
        portfolio.open_positions[position_id] = Position(
            position_id=position_id, symbol=market['symbol'], direction=direction, lots=0.01,
            original_lots=0.01, entry_price=market['price'], entry_time=_START,
            entry_tick_value=1.0, digits=spec.digits, contract_size=spec.contract_size)
    return portfolio


def _tick(spot_mode: bool, moved_by: float, seconds: int) -> TickData:
    """A quote of the portfolio's market, its mid moved from the entry price."""
    market = _MARKETS[spot_mode]
    mid = market['price'] + moved_by
    return TickData(timestamp=_START + timedelta(seconds=seconds), symbol=market['symbol'],
                    bid=mid - market['spread'] / 2, ask=mid + market['spread'] / 2)


def _feed(portfolio: PortfolioManager, clock: List[datetime], ticks: List[TickData]) -> None:
    """New prices as both tick loops give them: a new price, then the equity sample — no read."""
    for tick in ticks:
        clock[0] = tick.timestamp
        portfolio.mark_dirty(tick)
        portfolio.sample_equity()


def _moves(spot_mode: bool) -> List[float]:
    """A wander up, down and back, in the market's own price scale."""
    step = 5.0 if spot_mode else 0.0005
    return [step * factor for factor in (1, 3, -2, -6, 4, 0.5)]


@pytest.mark.parametrize('spot_mode', [True, False])
class TestTheMaskIsReadOnly:
    """An algo can read the mask and change nothing through it."""

    def test_every_assignment_is_refused(self, spot_mode):
        clock = [_START]
        mask = _portfolio(spot_mode, True, clock).get_algo_positions()[0]
        for name in AlgoPosition.get_mask_fields() + ('anything_new',):
            with pytest.raises(AttributeError):
                setattr(mask, name, 1.0)

    def test_it_has_no_instance_dictionary(self, spot_mode):
        clock = [_START]
        mask = _portfolio(spot_mode, True, clock).get_algo_positions()[0]
        assert not hasattr(mask, '__dict__')

    def test_it_hands_out_values_only(self, spot_mode):
        clock = [_START]
        portfolio = _portfolio(spot_mode, True, clock)
        _feed(portfolio, clock, [_tick(spot_mode, 0.0, 1)])
        mask = portfolio.get_algo_positions()[0]
        for name in AlgoPosition.get_mask_fields():
            value = getattr(mask, name)
            assert not isinstance(value, (Position, list, dict, set)), f'{name} hands out {type(value).__name__}'


@pytest.mark.parametrize('spot_mode', [True, False])
class TestTheMaskIsCurrentWhenRead:
    """What a lazy portfolio's mask shows equals what a portfolio marking at every event stores."""

    def test_after_every_quote(self, spot_mode):
        lazy_clock, eager_clock = [_START], [_START]
        lazy = _portfolio(spot_mode, True, lazy_clock)
        eager = _portfolio(spot_mode, False, eager_clock)
        for seconds, moved_by in enumerate(_moves(spot_mode), start=1):
            tick = _tick(spot_mode, moved_by, seconds)
            _feed(lazy, lazy_clock, [tick])
            _feed(eager, eager_clock, [tick])
            for mask in lazy.get_algo_positions():
                stored = eager.open_positions[mask.position_id]
                assert mask.current_price == stored.current_price
                assert mask.gross_pnl == stored.gross_pnl
                assert mask.unrealized_pnl == stored.unrealized_pnl
                assert (mask.mae_pnl, mask.mae_price) == (stored.mae_pnl, stored.mae_price)
                assert (mask.mfe_pnl, mask.mfe_price) == (stored.mfe_pnl, stored.mfe_price)

    def test_a_mask_kept_across_quotes_stays_current(self, spot_mode):
        clock = [_START]
        portfolio = _portfolio(spot_mode, True, clock)
        _feed(portfolio, clock, [_tick(spot_mode, 0.0, 1)])
        kept = portfolio.get_algo_position('long')
        before = kept.current_price
        _feed(portfolio, clock, [_tick(spot_mode, _moves(spot_mode)[1], 2)])
        assert kept.current_price != before
        assert kept.current_price == portfolio.value_position(portfolio.open_positions['long']).current_price


@pytest.mark.parametrize('spot_mode', [True, False])
class TestOneMaskPerPosition:
    """A mask is made on the first read and handed out again after it."""

    def test_the_same_mask_on_every_read(self, spot_mode):
        clock = [_START]
        portfolio = _portfolio(spot_mode, True, clock)
        first = portfolio.get_algo_positions()
        again = portfolio.get_algo_positions()
        assert all(a is b for a, b in zip(first, again))
        assert portfolio.get_algo_position('long') is first[0]

    def test_a_position_not_open_has_no_mask(self, spot_mode):
        clock = [_START]
        assert _portfolio(spot_mode, True, clock).get_algo_position('unknown') is None

    def test_the_symbol_filter(self, spot_mode):
        clock = [_START]
        portfolio = _portfolio(spot_mode, True, clock)
        assert len(portfolio.get_algo_positions(_MARKETS[spot_mode]['symbol'])) == 2
        assert portfolio.get_algo_positions('NOSUCHSYMBOL') == []


class TestAClosedPositionShowsWhatItStored:
    """A mask held past the close shows the values the position closed with, not the market's."""

    def test_stored_values_after_the_close(self):
        clock = [_START]
        portfolio = _portfolio(True, True, clock)
        _feed(portfolio, clock, [_tick(True, 0.0, 1)])
        portfolio.ensure_positions_updated()
        kept = portfolio.get_algo_position('long')
        closed_at = kept.current_price
        assert portfolio.retire_dust_position('long')
        _feed(portfolio, clock, [_tick(True, 50.0, 2)])
        assert kept.status is PositionStatus.CLOSED
        assert kept.current_price == closed_at
        assert portfolio.get_algo_position('long') is None


@pytest.mark.parametrize('spot_mode', [True, False])
class TestBeforeTheFirstQuote:
    """A price the market has not given yet is unknown, not zero."""

    def test_price_and_pnl_read_none(self, spot_mode):
        clock = [_START]
        mask = _portfolio(spot_mode, True, clock).get_algo_position('long')
        assert (mask.current_price, mask.gross_pnl, mask.unrealized_pnl) == (None, None, None)

    def test_the_history_and_the_fees_still_read(self, spot_mode):
        clock = [_START]
        mask = _portfolio(spot_mode, True, clock).get_algo_position('long')
        assert mask.mae_pnl == 0.0 and mask.mae_price == mask.entry_price
        assert mask.total_fees == 0.0


class TestAMaskOfAPositionThatLeftTheBook:
    """A mask kept past a reset values nothing and books nothing for the stale object."""

    def test_after_a_reset(self):
        clock = [_START]
        portfolio = _portfolio(False, True, clock)
        _feed(portfolio, clock, [_tick(False, 0.0, 1)])
        kept = portfolio.get_algo_position('long')
        portfolio.reset()
        _feed(portfolio, clock, [_tick(False, 0.0005, 2)])
        assert kept.current_price is None
        assert portfolio.get_algo_position('long') is None
