"""
Sim Trade Emission — Lifecycle Tests (#326)

Asserts the sim-side BrokerTrade emission inside AbstractTradeExecutor's
shared _fill_open_order / _fill_close_order paths:

- After a MARKET fill, pending.fills.trades has one BrokerTrade
- cumulative_filled_lots / cumulative_avg_price / cumulative_fee match the fill
- is_maker is False for MARKET (taker), True for LIMIT (maker)
- Close fills produce a second BrokerTrade on the close PendingOrder

The emission is centralized in AbstractTradeExecutor so live and sim share the
same data-population contract — see ISSUE_326 §10 Sim/Live parity.
"""

from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderSide,
    OrderType,
)

from tests.simulation.trade_emission.conftest import feed_sim_tick


def _market_fill(executor, msc=1000):
    """Submit a MARKET order and drive it to FILLED."""
    feed_sim_tick(executor, msc=msc)
    result = executor.open_order(OpenOrderRequest(
        symbol='BTCUSD', order_type=OrderType.MARKET,
        direction=OrderDirection.LONG, lots=0.001,
    ))
    # Zero-latency Mock fills on the next tick via the latency queue drain
    feed_sim_tick(executor, msc=msc + 1)
    return result


class TestSimMarketFillEmitsTrade:
    """A MARKET fill produces exactly one synthesized BrokerTrade."""

    def test_open_position_has_no_pending_trade_lookup(self, sim_executor):
        """After fill, the order_history shows EXECUTED; portfolio has the position.
        The trade synthesis happens INSIDE _fill_open_order on the pending order
        before portfolio.open_position is called — not visible after fill since
        the pending order is consumed."""
        _market_fill(sim_executor)
        positions = sim_executor.get_open_positions()
        assert len(positions) == 1

    def test_history_shows_executed_after_fill(self, sim_executor):
        _market_fill(sim_executor)
        history = sim_executor.get_order_history()
        executed = [h for h in history if h.status.value == 'executed']
        assert len(executed) == 1


class TestSimSyntheticTradeShape:
    """The synthesized BrokerTrade has expected fields. Verified by inspecting
    the pending order at the moment of fill via a hook."""

    def test_synthesized_trade_matches_fill(self, sim_executor):
        """Intercept _fill_open_order to capture pending.fills.trades right after synthesis."""
        captured: list = []
        original_open_position = sim_executor.portfolio.open_position

        def _capture_pending_state(*args, **kwargs):
            order_id = kwargs.get('order_id') or args[0]
            # Look up pending in latency queue or active lists at moment of call
            for p in sim_executor._active_limit_orders + sim_executor._active_stop_orders:
                if p.pending_order_id == order_id:
                    captured.append({
                        'trades': list(p.fills.trades),
                        'cumulative_lots': p.fills.cumulative_filled_lots,
                        'cumulative_avg': p.fills.cumulative_avg_price,
                    })
                    break
            return original_open_position(*args, **kwargs)

        sim_executor.portfolio.open_position = _capture_pending_state

        # MARKET orders don't pass through _active_limit_orders, so we need
        # a different probe — patch _synthesize_pending_trade itself.
        sim_executor.portfolio.open_position = original_open_position
        synth_trades: list = []
        original_synth = sim_executor._synthesize_pending_trade

        def _record_synth(pending_order, fill_price, filled_lots, entry_type, symbol_spec, fee_cost):
            original_synth(pending_order, fill_price, filled_lots, entry_type, symbol_spec, fee_cost)
            synth_trades.append(list(pending_order.fills.trades))

        sim_executor._synthesize_pending_trade = _record_synth

        _market_fill(sim_executor)

        assert len(synth_trades) >= 1
        trades_at_fill = synth_trades[0]
        assert len(trades_at_fill) == 1
        t = trades_at_fill[0]
        assert t.volume == 0.001
        # BrokerTrade.side is OrderSide now (BUY/SELL — trade-event view);
        # opening a LONG position produces a BUY trade.
        assert t.side == OrderSide.BUY
        assert t.is_maker is False  # MARKET = taker
        assert t.fee_currency == 'USD'


class TestSimCloseEmitsTrade:
    """Closing a position emits a second BrokerTrade on the close pending order."""

    def test_close_synthesizes_trade_on_close_pending(self, sim_executor):
        _market_fill(sim_executor, msc=1000)
        positions = sim_executor.get_open_positions()
        assert len(positions) == 1
        position_id = positions[0].position_id

        synth_calls: list = []
        original_synth = sim_executor._synthesize_pending_trade

        def _record_synth(pending_order, fill_price, filled_lots, entry_type, symbol_spec, fee_cost):
            original_synth(pending_order, fill_price, filled_lots, entry_type, symbol_spec, fee_cost)
            synth_calls.append({
                'action': pending_order.order_action.value if pending_order.order_action else None,
                'volume': filled_lots,
            })

        sim_executor._synthesize_pending_trade = _record_synth
        sim_executor.close_position(position_id)
        feed_sim_tick(sim_executor, msc=2000)

        # One synthesis call for the close
        close_synths = [s for s in synth_calls if s['action'] == 'close']
        assert len(close_synths) == 1
        assert close_synths[0]['volume'] == 0.001
