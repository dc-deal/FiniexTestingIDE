"""
Order Precision — Normalization Tests (#332)

Verifies the shared executor-layer PRICE normalization the first live Field
Study surfaced: a raw computed limit price (e.g. an offset-percentage price like
`1896.7294`) was sent to the broker unrounded and rejected. Prices now snap to
the symbol's `digits` before the local book records the order and before the
adapter submits it — identically in simulation and live.

Volume is intentionally NOT normalized: a step-misaligned lot is a position-size
change, left for validate_order to reject (broker-accurate) — see
docs/architecture/architecture_execution_layer.md.

Two layers:
- `_round_price` (exact math + edge cases).
- The public paths that call it — open_order, modify_limit_order — through a
  TradeSimulator with a real Kraken spec (BTCUSD: digits=1).

Parity note: the same helper runs identically in LiveTradeExecutor; the live
broker path cannot be unit-tested, so the shared logic is proven here and
inherited by live.
"""

from datetime import datetime, timezone

from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderStatus,
    OrderType,
)


def feed_sim_tick(executor, msc, bid=49999.0, ask=50001.0, symbol='BTCUSD'):
    """Feed one tick to advance the sim clock and drive pending-order processing."""
    tick = TickData(
        timestamp=datetime.fromtimestamp(msc / 1000.0, tz=timezone.utc),
        symbol=symbol,
        bid=bid,
        ask=ask,
        collected_msc=msc,
        time_msc=msc,
    )
    executor.on_tick(tick)
    return tick


class TestRoundPrice:
    """_round_price rounds to N decimals; None passes through."""

    def test_none_passes_through(self):
        assert AbstractTradeExecutor._round_price(None, 2) is None

    def test_rounds_to_two_decimals(self):
        assert AbstractTradeExecutor._round_price(1896.7294, 2) == 1896.73

    def test_btcusd_one_decimal(self):
        assert AbstractTradeExecutor._round_price(50000.37, 1) == 50000.4

    def test_already_precise_unchanged(self):
        assert AbstractTradeExecutor._round_price(1900.5, 2) == 1900.5


class TestOpenOrderNormalization:
    """open_order rounds the resting limit's price to the symbol's digits."""

    def test_limit_price_rounded_to_digits(self, sim_executor):
        feed_sim_tick(sim_executor, msc=1000)
        result = sim_executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.LIMIT,
            direction=OrderDirection.LONG, lots=0.001, price=49000.37))
        feed_sim_tick(sim_executor, msc=1001)

        target = next(p for p in sim_executor._active_limit_orders
                      if p.pending_order_id == result.order_id)
        assert target.entry_price == 49000.4

    def test_unknown_symbol_does_not_crash(self, sim_executor):
        # Normalization must not raise on an unknown symbol — open_order's
        # validation rejects it gracefully (regression for the #332 guard).
        feed_sim_tick(sim_executor, msc=1000)
        result = sim_executor.open_order(OpenOrderRequest(
            symbol='NOT_A_SYMBOL', order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.001))
        assert result.status == OrderStatus.REJECTED


class TestModifyLimitNormalization:
    """modify_limit_order rounds the new limit price to digits."""

    def test_modified_price_rounded(self, sim_executor):
        feed_sim_tick(sim_executor, msc=1000)
        result = sim_executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.LIMIT,
            direction=OrderDirection.LONG, lots=0.001, price=49000.0))
        feed_sim_tick(sim_executor, msc=1001)

        sim_executor.modify_limit_order(order_id=result.order_id, new_price=48000.37)
        feed_sim_tick(sim_executor, msc=2000)

        target = next(p for p in sim_executor._active_limit_orders
                      if p.pending_order_id == result.order_id)
        assert target.entry_price == 48000.4
