"""
Resting orders read the trade where the venue does (#605), in the simulation executor.

Kraken triggers a stop on its last traded price and takes a public trade THROUGH a resting limit's
price as proof the order was met; the quote crossing the order fills it on every venue. Since
collector format 1.6.0 a tick carries both, and the venue's declaration on `OrderCapabilities` says
which decides. Driven against the real executor over hand-made ticks, in both account models —
a fill price reaches the portfolio, so it is a money path.

    quote 49,999 / 50,001 · last trade 50,000
    resting buy limit 49,990: a trade at 49,990 leaves it resting, one at 49,989 fills it at 49,990
    a long's stop loss 49,995: the bid below it does not fire it, a trade at 49,995 does

A trade counts only for a limit that already rests: the one its arrival tick carries printed
before the order stood in the book. The dry-run simulator plays the same venue, so it is driven
over the same ticks and has to fill on the same one, at the same price.
"""

import json
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import numpy as np
import pytest

from python.framework.data_preparation.modelled_quote import modelled_quote, modelled_runs
from python.framework.discoveries.spread_estimate.spread_estimator import MS_PER_DAY
from python.framework.logging.global_logger import GlobalLogger
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.trading_env.adapters.dry_run_simulator import DryRunOrderSimulator
from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.simulation.trade_simulator import TradeSimulator
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.order_types import (
    LimitFillEvidence,
    OpenOrderRequest,
    OrderDirection,
    OrderStatus,
    OrderType,
    StopTriggerBasis,
)

ACCOUNT_MODELS = pytest.mark.parametrize('spot_mode', [False, True], ids=['margin', 'spot'])

_BID = 49999.0
_ASK = 50001.0
_LIMIT = 49990.0
_STOP = 49995.0


def _simulator(spot_mode: bool, broker_type: BrokerType = BrokerType.KRAKEN_SPOT,
               first_tick: bool = True,
               modelled_quote_runs: Tuple[Tuple[int, int], ...] = ()) -> TradeSimulator:
    """
    The simulation executor on a venue's declared rule, one tick fed.

    Args:
        spot_mode: The account model
        broker_type: The venue the mock adapter stands in for — Kraken decides on its trades
        first_tick: False to leave the ticks to the caller
        modelled_quote_runs: The stretches whose quote was modelled, as the preparation hands them

    Returns:
        The simulator, ready to take orders
    """
    sim = TradeSimulator(
        broker_config=BrokerConfig(broker_type, MockBrokerAdapter(
            mode=MockExecutionMode.INSTANT_FILL, broker_type=broker_type)),
        initial_balance=100000.0,
        account_currency='USD',
        logger=GlobalLogger('RestingOrderVenueRule'),
        seeds={'inbound_latency_seed': 42},
        inbound_latency_min_ms=1,
        inbound_latency_max_ms=1,
        spot_mode=spot_mode,
        initial_balances={'USD': 100000.0, 'BTC': 0.0} if spot_mode else None,
        modelled_quote_runs=modelled_quote_runs,
    )
    if first_tick:
        _tick(sim, msc=1000)
    return sim


def _tick(sim: TradeSimulator, msc: int, bid: float = _BID, ask: float = _ASK,
          last: Optional[float] = 50000.0) -> None:
    """
    One BTCUSD tick with the quote and the trade it carries.

    Args:
        sim: The simulator
        msc: The tick's millisecond stamp
        bid: The bid
        ask: The ask
        last: The traded price, None for a tick that carries none
    """
    sim.on_tick(TickData(
        timestamp=datetime.fromtimestamp(msc / 1000.0, tz=timezone.utc),
        symbol='BTCUSD', bid=bid, ask=ask, last=last, collected_msc=msc, time_msc=msc,
    ))


def _limit_status(sim: TradeSimulator, order_id: str) -> Optional[OrderStatus]:
    """The newest status the order history holds for the order."""
    rows = [row for row in sim.get_order_history() if row.order_id == order_id]
    return rows[-1].status if rows else None


class TestTheVenueDeclaresItsRule:

    def test_the_mock_declares_what_kraken_declares_for_a_kraken_profile(self):
        """The mock mirrors Kraken's declaration; this keeps the mirror from drifting."""
        with open('configs/brokers/kraken/kraken_spot_broker_config.json', encoding='utf-8') as fh:
            kraken = KrakenAdapter(json.load(fh)).get_order_capabilities()
        mock = MockBrokerAdapter(broker_type=BrokerType.KRAKEN_SPOT).get_order_capabilities()

        assert (kraken.stop_trigger_basis, kraken.limit_fill_evidence) == (
            StopTriggerBasis.LAST, LimitFillEvidence.PUBLIC_TRADE)
        assert (mock.stop_trigger_basis, mock.limit_fill_evidence) == (
            kraken.stop_trigger_basis, kraken.limit_fill_evidence)

    def test_a_mock_of_a_quote_driven_venue_keeps_the_quote_rule(self):
        mock = MockBrokerAdapter(broker_type=BrokerType.MT5_FOREX).get_order_capabilities()
        assert (mock.stop_trigger_basis, mock.limit_fill_evidence) == (
            StopTriggerBasis.BID_ASK, LimitFillEvidence.QUOTE)


@ACCOUNT_MODELS
class TestARestingBuyLimitOnKraken:

    def test_a_trade_through_its_price_fills_it_at_its_own_price(self, spot_mode):
        sim = _simulator(spot_mode)
        order_id = sim.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.LIMIT, direction=OrderDirection.LONG,
            lots=0.01, price=_LIMIT)).order_id
        _tick(sim, msc=1001)                                   # it arrives, and rests

        _tick(sim, msc=1002, last=_LIMIT)                      # a trade only at its price
        assert sim.get_open_positions() == [], 'the queue ahead of it at that price filled first'

        _tick(sim, msc=1003, last=_LIMIT - 1.0)                # a seller sold below it
        position, = sim.get_open_positions()
        assert position.entry_price == pytest.approx(_LIMIT), 'a limit fills at its own price'
        assert _limit_status(sim, order_id) is OrderStatus.EXECUTED

    def test_on_a_quote_venue_the_same_trade_leaves_it_resting(self, spot_mode):
        sim = _simulator(spot_mode, BrokerType.MT5_FOREX)
        sim.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.LIMIT, direction=OrderDirection.LONG,
            lots=0.01, price=_LIMIT))
        _tick(sim, msc=1001)
        _tick(sim, msc=1002, last=_LIMIT - 1.0)

        assert sim.get_open_positions() == [], 'the ask never came down to it'


@ACCOUNT_MODELS
class TestALongsStopLossOnKraken:

    def test_it_waits_for_a_trade_at_the_stop_while_the_bid_is_below_it(self, spot_mode):
        sim = _simulator(spot_mode)
        sim.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET, direction=OrderDirection.LONG,
            lots=0.01, stop_loss=_STOP))
        _tick(sim, msc=1001)                                   # the entry fills
        assert len(sim.get_open_positions()) == 1

        _tick(sim, msc=1002, bid=_STOP - 1.0, ask=_STOP + 1.0, last=_STOP + 0.5)
        assert len(sim.get_open_positions()) == 1, 'the bid broke the stop, the last trade did not'

        _tick(sim, msc=1003, bid=_STOP - 1.0, ask=_STOP + 1.0, last=_STOP)
        assert sim.get_open_positions() == []
        trade, = sim.get_trade_history()
        assert trade.close_reason is CloseReason.SL_TRIGGERED
        assert trade.exit_price == pytest.approx(_STOP - 1.0), 'a triggered stop sells at the bid'

    def test_a_tick_without_a_traded_price_fires_it_on_the_bid(self, spot_mode):
        sim = _simulator(spot_mode)
        sim.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET, direction=OrderDirection.LONG,
            lots=0.01, stop_loss=_STOP))
        _tick(sim, msc=1001)

        _tick(sim, msc=1002, bid=_STOP - 1.0, ask=_STOP + 1.0, last=None)
        assert sim.get_open_positions() == [], 'without a trade the quote answers'


@ACCOUNT_MODELS
class TestALimitArrivingAtTheVenue:

    def test_the_trade_its_arrival_tick_carries_does_not_fill_it(self, spot_mode):
        """The trade printed before the order stood in the book; from the next tick on it counts."""
        sim = _simulator(spot_mode)
        sim.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.LIMIT, direction=OrderDirection.LONG,
            lots=0.01, price=_LIMIT))

        _tick(sim, msc=1001, last=_LIMIT - 1.0)                # it arrives with a trade through it
        assert sim.get_open_positions() == [], 'only the quote decides on arrival'

        _tick(sim, msc=1002, last=_LIMIT - 1.0)                # now it rests, and a trade fills it
        position, = sim.get_open_positions()
        assert position.entry_price == pytest.approx(_LIMIT)

    def test_a_quote_already_through_it_on_arrival_fills_it_at_once(self, spot_mode):
        sim = _simulator(spot_mode)
        sim.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.LIMIT, direction=OrderDirection.LONG,
            lots=0.01, price=_LIMIT))

        _tick(sim, msc=1001, bid=_LIMIT - 3.0, ask=_LIMIT - 1.0, last=_LIMIT - 2.0)
        position, = sim.get_open_positions()
        assert position.entry_price == pytest.approx(_LIMIT), 'a limit fills at its own price'


@ACCOUNT_MODELS
class TestARestingBuyStopOnKraken:

    def test_it_waits_for_a_trade_at_the_stop_while_the_ask_is_above_it(self, spot_mode):
        stop = 50005.0
        sim = _simulator(spot_mode)
        sim.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.STOP, direction=OrderDirection.LONG,
            lots=0.01, stop_price=stop))
        _tick(sim, msc=1001)                                   # it arrives, and rests

        _tick(sim, msc=1002, bid=stop - 1.0, ask=stop + 1.0, last=stop - 1.0)
        assert sim.get_open_positions() == [], 'the ask crossed the stop, the last trade did not'

        _tick(sim, msc=1003, bid=stop - 1.0, ask=stop + 1.0, last=stop)
        position, = sim.get_open_positions()
        assert position.entry_price == pytest.approx(stop + 1.0), 'a triggered stop buys at the ask'


@ACCOUNT_MODELS
class TestTicksWhoseQuoteIsTheirTrade:
    """
    Before collector format 1.6.0 a tick carries bid = ask = the traded price, and a modelled
    quote (#602) is that price with half the estimated spread on either side. On both the trade
    decides a Kraken limit: on a flat tick the quote at the limit is a trade AT it, which fills the
    queue ahead, so the order waits for a trade through it, as on every other tick.
    """

    def test_a_flat_tick_fills_a_resting_limit_on_kraken_only_on_a_trade_through_it(self, spot_mode):
        fills = {}
        for broker_type in (BrokerType.KRAKEN_SPOT, BrokerType.MT5_FOREX):
            sim = _simulator(spot_mode, broker_type, first_tick=False)
            _tick(sim, msc=1000, bid=50000.0, ask=50000.0, last=50000.0)
            sim.open_order(OpenOrderRequest(
                symbol='BTCUSD', order_type=OrderType.LIMIT, direction=OrderDirection.LONG,
                lots=0.01, price=_LIMIT))
            for msc, price in ((1001, 49995.0), (1002, 49992.0), (1003, _LIMIT), (1004, 49985.0)):
                _tick(sim, msc=msc, bid=price, ask=price, last=price)
                if sim.get_open_positions():
                    fills[broker_type] = (msc, sim.get_open_positions()[0].entry_price)
                    break

        assert fills[BrokerType.MT5_FOREX] == (1003, _LIMIT), 'the quote rule fills on the touch'
        assert fills[BrokerType.KRAKEN_SPOT] == (1004, _LIMIT), 'Kraken waits for a trade below it'

    def test_on_a_modelled_quote_the_trade_through_fills_before_the_ask_reaches_it(self, spot_mode):
        time_msc = np.array([1000 + MS_PER_DAY * 20000 + i for i in range(5)], dtype=np.int64)
        traded = np.array([50000.0, 49995.0, 49991.0, 49989.5, 49985.0])
        bid, ask, modelled, _ = modelled_quote(
            time_msc, traded, traded.copy(), traded.copy(), np.ones(5, dtype=bool),
            np.array([time_msc[0] // MS_PER_DAY - 1]), np.array([3.0 / 50000.0]), 1.0)
        assert modelled.all()
        assert all(traded[i] < _LIMIT for i in range(5) if ask[i] <= _LIMIT), \
            'on a quote modelled around the trade, the ask at the limit means a trade through it'

        fills = {}
        for broker_type in (BrokerType.KRAKEN_SPOT, BrokerType.MT5_FOREX):
            sim = _simulator(spot_mode, broker_type, first_tick=False,
                             modelled_quote_runs=modelled_runs(time_msc, modelled))
            _tick(sim, msc=int(time_msc[0]), bid=float(bid[0]), ask=float(ask[0]), last=float(traded[0]))
            sim.open_order(OpenOrderRequest(
                symbol='BTCUSD', order_type=OrderType.LIMIT, direction=OrderDirection.LONG,
                lots=0.01, price=_LIMIT))
            for i in range(1, 5):
                _tick(sim, msc=int(time_msc[i]), bid=float(bid[i]), ask=float(ask[i]), last=float(traded[i]))
                if sim.get_open_positions():
                    fills[broker_type] = i
                    break

        assert fills == {BrokerType.KRAKEN_SPOT: 3, BrokerType.MT5_FOREX: 4}, \
            'the trade at 49,989.5 fills it on Kraken while the modelled ask is still 49,992.5'


# One stream of (bid, ask, last) per case; the order is placed on the first tick
_STREAMS: List[Tuple[str, OrderType, float, List[Tuple[float, float, Optional[float]]]]] = [
    ('limit, a trade through on arrival, then again', OrderType.LIMIT, _LIMIT,
     [(_BID, _ASK, 50000.0), (_BID, _ASK, _LIMIT - 1.0), (_BID, _ASK, _LIMIT - 1.0)]),
    ('limit, a trade at its price, then through', OrderType.LIMIT, _LIMIT,
     [(_BID, _ASK, 50000.0), (_BID, _ASK, 50000.0), (_BID, _ASK, _LIMIT), (_BID, _ASK, _LIMIT - 0.5)]),
    ('limit, the quote sweeps through it', OrderType.LIMIT, _LIMIT,
     [(_BID, _ASK, 50000.0), (_BID, _ASK, 50000.0), (_LIMIT - 4.0, _LIMIT - 2.0, _LIMIT - 3.0)]),
    ('limit, a tick without a trade', OrderType.LIMIT, _LIMIT,
     [(_BID, _ASK, 50000.0), (_BID, _ASK, None), (_LIMIT - 2.0, _LIMIT, None)]),
    ('buy stop, the ask through it first, then a trade', OrderType.STOP, 50005.0,
     [(_BID, _ASK, 50000.0), (_BID, _ASK, 50000.0), (50004.0, 50006.0, 50004.0),
      (50004.0, 50006.0, 50005.0)]),
    ('buy stop, a trade through it on arrival', OrderType.STOP, 50005.0,
     [(_BID, _ASK, 50000.0), (50005.0, 50007.0, 50006.0)]),
]


@pytest.mark.parametrize('case, order_type, level, stream', _STREAMS, ids=[s[0] for s in _STREAMS])
@ACCOUNT_MODELS
class TestTheDryRunFillsWhereTheBacktestDoes:
    """
    The simulation's order arrives one millisecond after it is sent; the dry run's arrives on its
    first poll (`polls_until_fill=1`), one tick later. From there both answer the same question.
    """

    def test_same_tick_same_price(self, case, order_type, level, stream, spot_mode):
        assert self._simulation(order_type, level, stream, spot_mode) == self._dry_run(
            order_type, level, stream)

    @staticmethod
    def _simulation(order_type: OrderType, level: float,
                    stream: List[Tuple[float, float, Optional[float]]],
                    spot_mode: bool) -> Optional[Tuple[int, float]]:
        """The tick the simulation fills the buy on, and its price; None if it never does."""
        sim = _simulator(spot_mode, first_tick=False)
        for index, (bid, ask, last) in enumerate(stream):
            _tick(sim, msc=1000 + index, bid=bid, ask=ask, last=last)
            if index == 0:
                sim.open_order(OpenOrderRequest(
                    symbol='BTCUSD', order_type=order_type, direction=OrderDirection.LONG, lots=0.01,
                    price=level if order_type == OrderType.LIMIT else None,
                    stop_price=level if order_type == OrderType.STOP else None))
            elif sim.get_open_positions():
                return index, sim.get_open_positions()[0].entry_price
        return None

    @staticmethod
    def _dry_run(order_type: OrderType, level: float,
                 stream: List[Tuple[float, float, Optional[float]]]) -> Optional[Tuple[int, float]]:
        """The tick the dry run fills the buy on, and its price; None if it never does."""
        simulator = DryRunOrderSimulator(StopTriggerBasis.LAST, LimitFillEvidence.PUBLIC_TRADE,
                                         polls_until_fill=1)
        stamp = datetime(2026, 11, 24, tzinfo=timezone.utc)
        ref = simulator.submit(
            0.01, stamp, OrderDirection.LONG, order_type,
            limit_price=level if order_type == OrderType.LIMIT else None,
            trigger_price=level if order_type == OrderType.STOP else None).broker_ref
        for index, (bid, ask, last) in enumerate(stream[1:], start=1):
            market = TickData(timestamp=stamp, symbol='BTCUSD', bid=bid, ask=ask, last=last,
                              collected_msc=1000 + index, time_msc=1000 + index)
            response = simulator.query(ref, stamp, market)
            if response.status is BrokerOrderStatus.FILLED:
                return index, response.fill_price
        return None
