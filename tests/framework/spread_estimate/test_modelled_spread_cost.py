"""
Modelled Spread Cost Tests (#602).

A position whose fills priced against a modelled quote has crossed the modelled spread: the money
is in its fill prices like any spread, and the record names it `modelled_spread_cost` rather than
`spread_cost`, which stays what was measured against a recorded quote. These tests hold that in
both account models, for a full and a partial close: the split moves attribution and never money,
and the exit is booked at the fill because the mark closes at the same modelled side — the reason
the partial and the margin close, which book from the mark, need nothing of their own.

Driven against the PortfolioManager directly: the arithmetic is the subject.
"""

from datetime import datetime, timezone

import pytest

from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.trading_env.portfolio_manager import PortfolioManager
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason
from python.framework.types.portfolio_types.portfolio_types import Position
from python.framework.types.trading_env_types.order_types import OrderDirection
from python.framework.utils.trading_math.pnl_math import gross_pnl_from_price_diff

_MT5_CONFIG = 'configs/brokers/mt5/mt5_broker_config.json'
_SYMBOL = 'EURUSD'
_LOTS = 2.0
_DIGITS = 5
# Traded at 100.00 on entry and 101.00 on exit; the modelled quote is 0.02 either side
_ENTRY_BID, _ENTRY_ASK = 99.98, 100.02
_EXIT_BID, _EXIT_ASK = 100.98, 101.02
_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


class _NullLogger:
    """Minimal duck-typed logger — the arithmetic under test does not log."""
    def verbose(self, *args, **kwargs): pass
    def debug(self, *args, **kwargs): pass
    def info(self, *args, **kwargs): pass
    def warning(self, *args, **kwargs): pass
    def error(self, *args, **kwargs): pass


def _close(spot_mode: bool, partial: bool, modelled: bool):
    """
    Open a LONG at the ask of a quote, mark it at the exit quote and close it at its bid.

    Args:
        spot_mode: The asset-inventory account instead of the margin one
        partial: Close half instead of all of it
        modelled: Whether both quotes were modelled for ticks without one

    Returns:
        (trade record, cost breakdown)
    """
    portfolio = PortfolioManager(
        logger=_NullLogger(), initial_balance=10_000.0, account_currency='USD',
        broker_config=BrokerConfigFactory.build_broker_config(_MT5_CONFIG),
        leverage=500, margin_call_level=50.0, stop_out_level=20.0,
        spot_mode=spot_mode, clock_fn=lambda: _NOW,
        initial_balances={'USD': 10_000.0, 'EUR': _LOTS} if spot_mode else None)
    position = Position(
        position_id='p1', symbol=_SYMBOL, direction=OrderDirection.LONG, lots=_LOTS,
        original_lots=_LOTS, entry_price=_ENTRY_ASK,
        entry_time=datetime(2026, 9, 8, 11, 0, tzinfo=timezone.utc),
        entry_tick_value=1.0, entry_bid=_ENTRY_BID, entry_ask=_ENTRY_ASK,
        digits=_DIGITS, contract_size=1, entry_quote_modelled=modelled)
    portfolio.open_positions['p1'] = position
    portfolio.mark_dirty(TickData(timestamp=_NOW, symbol=_SYMBOL, bid=_EXIT_BID, ask=_EXIT_ASK))
    exit_book = dict(exit_price=_EXIT_BID, exit_tick_value=1.0, exit_tick_index=10,
                     close_reason=CloseReason.MANUAL, exit_bid=_EXIT_BID, exit_ask=_EXIT_ASK,
                     exit_quote_modelled=modelled)
    if partial:
        portfolio.partial_close_position(position_id='p1', close_lots=_LOTS / 2, **exit_book)
    else:
        portfolio.close_position_portfolio(position_id='p1', **exit_book)
    return portfolio.get_trade_history()[-1], portfolio.get_cost_breakdown()


def _leg(distance: float, lots: float) -> float:
    return gross_pnl_from_price_diff(distance, _DIGITS, 1.0, lots)


@pytest.mark.parametrize('spot_mode', [False, True], ids=['margin', 'spot'])
@pytest.mark.parametrize('partial', [False, True], ids=['full', 'partial'])
class TestTheModelledSpreadIsItsOwnFigure:

    def test_it_lands_in_the_modelled_figure_and_not_the_measured_one(self, spot_mode, partial):
        record, costs = _close(spot_mode, partial, modelled=True)
        closed = _LOTS / 2 if partial else _LOTS
        # Entry leg pro-rated by the closed share, exit leg on the closed lots: 0.02 each way
        expected = _leg(0.02, _LOTS) * (closed / _LOTS) + _leg(0.02, closed)

        assert record.spread_cost == 0.0
        assert record.modelled_spread_cost == pytest.approx(expected)
        assert (record.entry_quote_modelled, record.exit_quote_modelled) == (True, True)
        assert costs.total_modelled_spread_cost == pytest.approx(expected)
        assert costs.total_spread_cost == 0.0

    def test_the_split_moves_attribution_never_money(self, spot_mode, partial):
        modelled, _ = _close(spot_mode, partial, modelled=True)
        recorded, _ = _close(spot_mode, partial, modelled=False)

        assert modelled.net_pnl == pytest.approx(recorded.net_pnl)
        assert modelled.modelled_spread_cost == pytest.approx(recorded.spread_cost)
        assert recorded.modelled_spread_cost == 0.0

    def test_the_exit_is_booked_at_the_fill_in_both_account_models(self, spot_mode, partial):
        """The mark closes at the same modelled bid the close fills at — nothing books apart."""
        record, _ = _close(spot_mode, partial, modelled=True)
        closed = _LOTS / 2 if partial else _LOTS

        assert record.gross_pnl == pytest.approx(gross_pnl_from_price_diff(
            _EXIT_BID - _ENTRY_ASK, _DIGITS, record.exit_tick_value, closed))
        assert record.net_pnl == pytest.approx(record.gross_pnl - record.total_fees)
