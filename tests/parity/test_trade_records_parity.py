"""
Sim/Live Parity — BrokerTrade Records Algo-Facing Contract (#326)

The order ↔ executions pairing model is broker-agnostic. After a fill, the
algo-facing contract is identical in both pipelines:

  - position exists in portfolio
  - order_history shows EXECUTED
  - per-execution data flowed through pending.trades + cumulative_*

The sim and live emission mechanisms differ (sim: shared _fill_open_order
synthesis on the next tick; live: same _fill_open_order called from the
polling drain), but the final outcome shape — what the algo and downstream
consumers observe — must match.

Per #326 §10 Sim/Live Parity.
"""

from datetime import datetime, timezone

from python.framework.logging.global_logger import GlobalLogger
from python.framework.testing.mock_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.trading_env.simulation.trade_simulator import TradeSimulator
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderSide,
    OrderStatus,
    OrderType,
)


# =============================================================================
# Pipeline-specific setup helpers
# =============================================================================

def _build_sim_executor() -> TradeSimulator:
    """Sim executor with zero-latency INSTANT_FILL Mock."""
    adapter = MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL)
    broker_config = BrokerConfig(BrokerType.KRAKEN_SPOT, adapter)
    return TradeSimulator(
        broker_config=broker_config,
        initial_balance=10000.0,
        account_currency='USD',
        logger=GlobalLogger('TradeRecordsParitySim'),
        seeds={'inbound_latency_seed': 42},
        inbound_latency_min_ms=0,
        inbound_latency_max_ms=0,
    )


def _feed_sim_tick(executor: TradeSimulator, msc: int) -> TickData:
    """Direct tick feed for sim — controls msc explicitly."""
    tick = TickData(
        timestamp=datetime.fromtimestamp(msc / 1000.0, tz=timezone.utc),
        symbol='BTCUSD', bid=49999.0, ask=50001.0,
        collected_msc=msc, time_msc=msc,
    )
    executor.on_tick(tick)
    return tick


def _drive_sim_market_fill(sim: TradeSimulator) -> str:
    """Submit MARKET on sim and drive to FILLED — return order_id."""
    _feed_sim_tick(sim, msc=1000)
    result = sim.open_order(OpenOrderRequest(
        symbol='BTCUSD', order_type=OrderType.MARKET,
        direction=OrderDirection.LONG, lots=0.001,
    ))
    _feed_sim_tick(sim, msc=1001)  # drain latency, fill
    return result.order_id


def _drive_live_market_fill(mock_exec: MockOrderExecution,
                            live: LiveTradeExecutor) -> str:
    """Submit MARKET on live and drive to FILLED — return order_id."""
    mock_exec.feed_tick(live, bid=49999.0, ask=50001.0)
    result = live.open_order(OpenOrderRequest(
        symbol='BTCUSD', order_type=OrderType.MARKET,
        direction=OrderDirection.LONG, lots=0.001,
    ))
    mock_exec.feed_tick(live, bid=49999.0, ask=50001.0)  # drain → fill
    return result.order_id


# =============================================================================
# Parity assertions
# =============================================================================

class TestPostFillStateParity:
    """After a MARKET fill, sim and live agree on position + history shape."""

    def test_position_count_matches(self):
        sim = _build_sim_executor()
        _drive_sim_market_fill(sim)

        live_mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        live = live_mock.create_executor()
        _drive_live_market_fill(live_mock, live)

        assert len(sim.get_open_positions()) == len(live.get_open_positions()) == 1

    def test_order_history_has_executed_in_both(self):
        sim = _build_sim_executor()
        _drive_sim_market_fill(sim)

        live_mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        live = live_mock.create_executor()
        _drive_live_market_fill(live_mock, live)

        sim_executed = [h for h in sim.get_order_history() if h.status == OrderStatus.EXECUTED]
        live_executed = [h for h in live.get_order_history() if h.status == OrderStatus.EXECUTED]
        assert len(sim_executed) == len(live_executed) == 1


class TestTradeSynthesisParity:
    """Both pipelines call _synthesize_pending_trade with matching shape."""

    def _capture_synth(self, executor):
        captured: list = []
        original = executor._synthesize_pending_trade

        def _record(pending_order, fill_price, filled_lots, entry_type, symbol_spec, fee_cost):
            original(pending_order, fill_price, filled_lots, entry_type, symbol_spec, fee_cost)
            # Capture from the just-appended BrokerTrade so `side` reflects
            # the new OrderSide (BUY/SELL) typing — not the OrderDirection
            # of the pending order (which is the position view).
            last_trade = pending_order.trades[-1] if pending_order.trades else None
            captured.append({
                'order_id': pending_order.pending_order_id,
                'volume': filled_lots,
                'price': fill_price,
                'entry_type': entry_type.value,
                'trades_count_after': len(pending_order.trades),
                'cumulative_lots': pending_order.cumulative_filled_lots,
                'side': last_trade.side if last_trade else None,
            })

        executor._synthesize_pending_trade = _record
        return captured

    def test_synthesis_invoked_once_per_market_open_in_both(self):
        sim = _build_sim_executor()
        sim_captured = self._capture_synth(sim)
        _drive_sim_market_fill(sim)

        live_mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        live = live_mock.create_executor()
        live_captured = self._capture_synth(live)
        _drive_live_market_fill(live_mock, live)

        # Exactly one synthesis call per pipeline (the open fill)
        sim_opens = [s for s in sim_captured if s['entry_type'] in ('market', 'limit')]
        live_opens = [s for s in live_captured if s['entry_type'] in ('market', 'limit')]
        assert len(sim_opens) == len(live_opens) == 1

    def test_synthesis_volume_matches_in_both(self):
        sim = _build_sim_executor()
        sim_captured = self._capture_synth(sim)
        _drive_sim_market_fill(sim)

        live_mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        live = live_mock.create_executor()
        live_captured = self._capture_synth(live)
        _drive_live_market_fill(live_mock, live)

        sim_opens = [s for s in sim_captured if s['entry_type'] in ('market', 'limit')]
        live_opens = [s for s in live_captured if s['entry_type'] in ('market', 'limit')]
        assert sim_opens[0]['volume'] == live_opens[0]['volume'] == 0.001

    def test_cumulative_lots_match_volume_in_both(self):
        sim = _build_sim_executor()
        sim_captured = self._capture_synth(sim)
        _drive_sim_market_fill(sim)

        live_mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        live = live_mock.create_executor()
        live_captured = self._capture_synth(live)
        _drive_live_market_fill(live_mock, live)

        sim_opens = [s for s in sim_captured if s['entry_type'] in ('market', 'limit')]
        live_opens = [s for s in live_captured if s['entry_type'] in ('market', 'limit')]
        # cumulative_lots equals the trade volume (one trade per fill)
        assert sim_opens[0]['cumulative_lots'] == sim_opens[0]['volume']
        assert live_opens[0]['cumulative_lots'] == live_opens[0]['volume']

    def test_side_matches_in_both(self):
        sim = _build_sim_executor()
        sim_captured = self._capture_synth(sim)
        _drive_sim_market_fill(sim)

        live_mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        live = live_mock.create_executor()
        live_captured = self._capture_synth(live)
        _drive_live_market_fill(live_mock, live)

        sim_opens = [s for s in sim_captured if s['entry_type'] in ('market', 'limit')]
        live_opens = [s for s in live_captured if s['entry_type'] in ('market', 'limit')]
        # BrokerTrade.side is now OrderSide (BUY/SELL — trade-event view)
        # rather than OrderDirection (LONG/SHORT — position view). Open LONG
        # produces a BUY trade in both pipelines.
        assert sim_opens[0]['side'] == live_opens[0]['side'] == OrderSide.BUY
