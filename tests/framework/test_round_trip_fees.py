"""
Round-trip fee accounting (#506) — two fees on a maker/taker venue, one on a spread broker.

The defect: both close paths passed `exit_fee=None` with the comment "V1: No exit commission".
On a maker/taker venue EVERY fill is charged, so every completed round trip was under-booked by
exactly one fee — half the trading cost of a finished trade, in both pipelines. Measured against
Kraken on 2026-09-08: one real Field Study run booked $0.08412231 while the venue charged
$0.37607000, and six of six exit legs booked exactly zero.

`exit_fee=None` was NOT simply wrong. It is correct for a SPREAD broker: MT5 charges no
per-side commission, the spread IS the round-trip price, and it is booked once at entry.
Charging again at exit would double-count it. So the switch is the FEE MODEL, and that
asymmetry is what these tests pin — the parity case is the point of the issue.

The second half is the money. `close_position_portfolio` runs its pre-close refresh BEFORE the
exit fee is attached, so in MARGIN mode `unrealized_pnl` was still gross of it while
`_create_trade_record` reads `total_fees` (which has it) beside `net_pnl` (which did not) — a
record that contradicted itself, and a balance that moved by the figure without the fee.

Driven directly against the PortfolioManager and the fee factories: no ticks, no latency, no
scenario — the arithmetic is the subject.
"""

import copy
from datetime import datetime, timezone

import pytest

from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.logging.global_logger import GlobalLogger
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.decision_trading_api import DecisionTradingApi
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.trading_env.portfolio_manager import PortfolioManager
from python.framework.trading_env.simulation.trade_simulator import TradeSimulator
from python.framework.trading_env.trading_fees import MakerTakerFee
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason
from python.framework.types.portfolio_types.portfolio_types import Position
from python.framework.types.trading_env_types.broker_types import BrokerType, FeeType
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderType

_MT5_CONFIG = 'configs/brokers/mt5/mt5_broker_config.json'
_KRAKEN_SEED = 'configs/brokers/kraken/kraken_spot_broker_config.json'

# The fee-factory tests run over the MOCK adapter, whose config carries BTCUSD.
_SYMBOL = 'BTCUSD'
_EXIT = 51_000.0
_LOTS = 0.01

# The portfolio tests run over the real MT5 config, which carries EURUSD.
_PF_SYMBOL = 'EURUSD'
_PF_ENTRY = 1.10
_PF_EXIT = 1.11
_PF_LOTS = 1.0


class _NullLogger:
    """Minimal duck-typed logger — the arithmetic under test does not log."""

    def verbose(self, *args, **kwargs): pass
    def debug(self, *args, **kwargs): pass
    def info(self, *args, **kwargs): pass
    def warning(self, *args, **kwargs): pass
    def error(self, *args, **kwargs): pass


def _executor(model: str) -> LiveTradeExecutor:
    """
    Build an executor whose adapter declares one fee model.

    The broker TYPE is deliberately held constant: `_fee_model()` reads
    `adapter.broker_config['fee_structure']['model']` and nothing else, so varying the model
    alone is what isolates the branch under test.

    Args:
        model: 'maker_taker', 'spread', or 'absent' to drop the block entirely

    Returns:
        A LiveTradeExecutor over a mock adapter carrying that fee structure
    """
    config = copy.deepcopy(MockBrokerAdapter().broker_config)
    if model == 'absent':
        config.pop('fee_structure', None)
    else:
        config['fee_structure'] = {
            'model': model, 'maker_fee': 0.25, 'taker_fee': 0.40, 'fee_currency': 'quote',
        }
    adapter = MockBrokerAdapter(broker_config=config)
    return LiveTradeExecutor(
        broker_config=BrokerConfig(BrokerType.KRAKEN_SPOT, adapter),
        initial_balance=10_000.0,
        account_currency='USD',
        logger=GlobalLogger(name='RoundTripFeeTest'),
    )


def _portfolio(spot_mode: bool = False) -> PortfolioManager:
    """
    Build a portfolio over the real MT5 broker config.

    Args:
        spot_mode: Build the asset-inventory portfolio instead of the margin one

    Returns:
        A PortfolioManager with a fixed clock
    """
    return PortfolioManager(
        logger=_NullLogger(), initial_balance=10_000.0, account_currency='USD',
        broker_config=BrokerConfigFactory.build_broker_config(_MT5_CONFIG),
        leverage=500, margin_call_level=50.0, stop_out_level=20.0,
        spot_mode=spot_mode,
        clock_fn=lambda: datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc))


def _fee(cost: float) -> MakerTakerFee:
    """
    A maker/taker fee of an exact cost, so the assertions are arithmetic rather than fixtures.

    Args:
        cost: The fee cost in account currency

    Returns:
        A MakerTakerFee whose cost is exactly `cost`
    """
    return MakerTakerFee(
        is_maker=False, maker_rate=0.0, taker_rate=100.0, order_value=cost,
        timestamp=datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc))


def _open(portfolio: PortfolioManager, entry_fee_cost: float,
          lots: float = _PF_LOTS) -> Position:
    """
    Put one marked LONG position into the portfolio with an entry fee already attached.

    Args:
        portfolio: The portfolio to open in
        entry_fee_cost: Cost of the entry fee
        lots: Position size

    Returns:
        The open position, marked at the exit price
    """
    position = Position(
        position_id='p1', symbol=_PF_SYMBOL, direction=OrderDirection.LONG, lots=lots,
        original_lots=lots, entry_price=_PF_ENTRY,
        entry_time=datetime(2026, 9, 8, 11, 0, tzinfo=timezone.utc),
        entry_tick_value=1.0, digits=5, contract_size=100000)
    position.add_fee(_fee(entry_fee_cost))
    portfolio.open_positions['p1'] = position
    portfolio.mark_dirty(TickData(
        timestamp=datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc),
        symbol=_PF_SYMBOL, bid=_PF_EXIT, ask=_PF_EXIT))
    portfolio._ensure_positions_updated()
    return position


class TestTheFeeModelDecidesWhetherThereIsAnExitFee:
    """
    The asymmetry that makes `exit_fee=None` right for one broker and wrong for the other.
    """

    def test_a_maker_taker_venue_charges_the_exit(self):
        executor = _executor('maker_taker')
        spec = executor.broker.adapter.get_symbol_specification(_SYMBOL)

        fee = executor._create_exit_fee(
            symbol_spec=spec, lots=_LOTS, exit_price=_EXIT, is_maker=False)

        assert fee is not None
        assert fee.fee_type is FeeType.MAKER_TAKER
        # taker 0.40 % of 0.01 x 51000 x contract_size 1
        assert fee.cost == pytest.approx(_LOTS * _EXIT * spec.contract_size * 0.004)

    def test_a_spread_broker_charges_nothing_per_side(self):
        """
        The one `return None` that leaves MT5 untouched — its round-trip price is the spread
        and it was already booked at entry, so a second charge would double-count it.
        """
        executor = _executor('spread')
        spec = executor.broker.adapter.get_symbol_specification(_SYMBOL)

        fee = executor._create_exit_fee(
            symbol_spec=spec, lots=_LOTS, exit_price=_EXIT, is_maker=False)

        assert fee is None

    def test_an_absent_fee_structure_falls_back_to_spread(self):
        """The default is the conservative side: no per-side charge invented from nothing."""
        executor = _executor('absent')
        spec = executor.broker.adapter.get_symbol_specification(_SYMBOL)

        assert executor._fee_model() is FeeType.SPREAD
        assert executor._create_exit_fee(
            symbol_spec=spec, lots=_LOTS, exit_price=_EXIT) is None

    def test_the_entry_and_the_exit_read_ONE_model_lookup(self):
        """
        `_fee_model()` exists so the two legs cannot disagree about which world they are in.
        """
        assert _executor('maker_taker')._fee_model() is FeeType.MAKER_TAKER
        assert _executor('spread')._fee_model() is FeeType.SPREAD


class TestARoundTripBooksBothLegs:
    """The count, at the level where it becomes money."""

    def test_a_maker_taker_round_trip_carries_two_fees(self):
        portfolio = _portfolio()
        position = _open(portfolio, entry_fee_cost=2.0)

        portfolio.close_position_portfolio(
            position_id='p1', exit_price=_PF_EXIT, exit_tick_value=1.0, exit_tick_index=10,
            exit_fee=_fee(3.0), close_reason=CloseReason.MANUAL)

        assert len(position.fees) == 2, 'entry and exit, not entry alone'
        assert position.get_total_fees() == pytest.approx(5.0)

    def test_a_spread_round_trip_carries_one(self):
        """`exit_fee=None` is what a spread broker passes, and it must stay a single fee."""
        portfolio = _portfolio()
        position = _open(portfolio, entry_fee_cost=2.0)

        portfolio.close_position_portfolio(
            position_id='p1', exit_price=_PF_EXIT, exit_tick_value=1.0, exit_tick_index=10,
            exit_fee=None, close_reason=CloseReason.MANUAL)

        assert len(position.fees) == 1
        assert position.get_total_fees() == pytest.approx(2.0)


class TestTheExitFeeLeavesTheMoney:
    """
    `net_pnl == gross_pnl - total_fees` has to hold in BOTH account models.

    In margin mode it did not: the pre-close refresh ran before the fee was attached, so the
    record's `total_fees` carried the exit fee while its `net_pnl` and the balance did not.
    """

    @pytest.mark.parametrize('spot_mode', [False, True], ids=['margin', 'spot'])
    def test_the_trade_record_does_not_contradict_itself(self, spot_mode):
        portfolio = _portfolio(spot_mode=spot_mode)
        if spot_mode:
            portfolio._balances['EUR'] = _PF_LOTS
        _open(portfolio, entry_fee_cost=2.0)

        portfolio.close_position_portfolio(
            position_id='p1', exit_price=_PF_EXIT, exit_tick_value=1.0, exit_tick_index=10,
            exit_fee=_fee(3.0), close_reason=CloseReason.MANUAL)

        record = portfolio.get_trade_history()[-1]
        assert record.total_fees == pytest.approx(5.0)
        assert record.net_pnl == pytest.approx(record.gross_pnl - record.total_fees)

    def test_margin_balance_moves_by_the_net_figure(self):
        portfolio = _portfolio()
        before = portfolio.balance
        _open(portfolio, entry_fee_cost=2.0)

        realized = portfolio.close_position_portfolio(
            position_id='p1', exit_price=_PF_EXIT, exit_tick_value=1.0, exit_tick_index=10,
            exit_fee=_fee(3.0), close_reason=CloseReason.MANUAL)

        record = portfolio.get_trade_history()[-1]
        assert realized == pytest.approx(record.net_pnl)
        assert portfolio.balance - before == pytest.approx(record.net_pnl)

    def test_a_spread_close_is_bit_identical_to_before(self):
        """
        The regression guard for MT5: with no exit fee the margin branch must not re-mark, so
        the realised figure is exactly the position's marked P&L.
        """
        portfolio = _portfolio()
        position = _open(portfolio, entry_fee_cost=2.0)
        marked = position.unrealized_pnl

        realized = portfolio.close_position_portfolio(
            position_id='p1', exit_price=_PF_EXIT, exit_tick_value=1.0, exit_tick_index=10,
            exit_fee=None, close_reason=CloseReason.MANUAL)

        assert realized == pytest.approx(marked)


class TestAPartialCloseChargesOnlyTheClosedLots:
    """The fee follows `close_lots`, never the position size."""

    def test_the_closed_portion_carries_the_whole_exit_fee_and_its_share_of_the_entry(self):
        portfolio = _portfolio()
        _open(portfolio, entry_fee_cost=2.0, lots=3.0)

        portfolio.partial_close_position(
            position_id='p1', close_lots=1.0, exit_price=_PF_EXIT, exit_tick_value=1.0,
            exit_tick_index=10, exit_fee=_fee(3.0), close_reason=CloseReason.MANUAL)

        record = portfolio.get_trade_history()[-1]
        # a third of the 2.0 entry fee, plus the exit fee charged on the closed third
        assert record.total_fees == pytest.approx(2.0 / 3.0 + 3.0)
        assert record.net_pnl == pytest.approx(record.gross_pnl - record.total_fees)


class TestTheSessionCostIsReadableThroughTheApi:
    """
    A decision logic has to be able to ASK what the session has spent (#506).

    Why this is pinned here rather than assumed: the Field Study's cost ceiling is the
    self-abort of a real-money release gate, and it first reached for
    `DecisionTradingApi.get_order_history()` — which is declared but raises in V1. The whole
    offline suite stayed green and the live run died seven seconds in, because nothing
    exercised the path. This is that test.

    `get_cost_breakdown()` is the right source for a second reason, not only because it works:
    the portfolio books every fee through ONE categorising site, so it carries both legs of a
    round trip whether or not a decision event was emitted for them — and a full close emits
    none.
    """

    def _api(self, executor) -> DecisionTradingApi:
        """
        Build the algo-facing API over an executor.

        Args:
            executor: The executor to wrap

        Returns:
            A DecisionTradingApi requiring only MARKET orders
        """
        return DecisionTradingApi(executor, required_order_types=[OrderType.MARKET])

    def test_the_api_reports_both_legs_of_a_round_trip(self):
        executor = _executor('maker_taker')
        api = self._api(executor)
        portfolio = executor.portfolio
        portfolio.open_positions['p1'] = Position(
            position_id='p1', symbol=_SYMBOL, direction=OrderDirection.LONG, lots=_LOTS,
            original_lots=_LOTS, entry_price=_EXIT,
            entry_time=datetime(2026, 9, 8, 11, 0, tzinfo=timezone.utc),
            entry_tick_value=1.0, digits=2, contract_size=1)

        portfolio._record_fee_cost(_fee(2.0))     # the entry leg
        portfolio._record_fee_cost(_fee(3.0))     # the exit leg — emits no decision event

        assert api.get_cost_breakdown().total_fees == pytest.approx(5.0)

    def test_the_order_history_is_not_the_source_it_looks_like(self):
        """
        Declared, documented, and it raises. Pinned so the next reader does not spend a live
        run finding out — the error message now names the accessor to use instead.
        """
        api = self._api(_executor('maker_taker'))

        with pytest.raises(NotImplementedError) as refused:
            api.get_order_history()

        assert 'get_cost_breakdown()' in str(refused.value)

    def test_the_breakdown_is_a_copy_a_logic_cannot_disturb(self):
        """A decision logic must not be able to write into the portfolio's own tracking."""
        executor = _executor('maker_taker')
        api = self._api(executor)
        executor.portfolio._record_fee_cost(_fee(2.0))

        breakdown = api.get_cost_breakdown()
        breakdown.total_fees = 999.0

        assert api.get_cost_breakdown().total_fees == pytest.approx(2.0)


class TestTheDeclaredRateIsTheRateCharged:
    """
    The link between the file and the money (#337).

    Everything else about the fee rate was pinned except the part that matters: that the
    number a broker's seed DECLARES is the number a fill is actually charged. Measured
    2026-09-09 — re-freezing the Kraken seed from 0.25/0.40 to 0.40/0.80 doubled a real
    backtest's cost (fees $0.16 -> $0.32, P&L -$0.17 -> -$0.33) and the whole suite stayed
    green, because no test connected the two ends.

    The expectation is READ from the config, never repeated here. So a deliberate re-freeze
    does not break this test — the tier moves and the assertion moves with it — while a break
    in the path from the file to the charge does.
    """

    def _executor_on_the_seed(self) -> TradeSimulator:
        """
        An executor over the real git-tracked Kraken seed — the file a backtest reads.

        Returns:
            A TradeSimulator whose adapter carries the seed's declared fee structure — the
            BACKTEST side, which is the reader this seam was built for
        """
        return TradeSimulator(
            broker_config=BrokerConfigFactory.build_broker_config(_KRAKEN_SEED),
            initial_balance=10_000.0,
            account_currency='USD',
            logger=GlobalLogger(name='DeclaredRateTest'),
            spot_mode=True)

    def test_a_taker_exit_is_charged_the_rate_the_seed_declares(self):
        executor = self._executor_on_the_seed()
        spec = executor.broker.get_symbol_specification('ETHUSD')
        declared = executor.broker.adapter.get_taker_fee()

        fee = executor._create_exit_fee(
            symbol_spec=spec, lots=1.0, exit_price=4000.0, is_maker=False)

        notional = 1.0 * spec.contract_size * 4000.0
        assert fee is not None
        assert fee.cost == pytest.approx(notional * declared / 100.0)

    def test_a_maker_exit_is_charged_the_other_declared_rate(self):
        """The two rates are distinct in the seed, so a swap between them has to show."""
        executor = self._executor_on_the_seed()
        spec = executor.broker.get_symbol_specification('ETHUSD')
        maker, taker = (executor.broker.adapter.get_maker_fee(),
                        executor.broker.adapter.get_taker_fee())
        assert maker != taker, 'the seed must declare two different rates for this to prove anything'

        fee = executor._create_exit_fee(
            symbol_spec=spec, lots=1.0, exit_price=4000.0, is_maker=True)

        notional = 1.0 * spec.contract_size * 4000.0
        assert fee.cost == pytest.approx(notional * maker / 100.0)

    def test_the_seed_declares_a_maker_taker_venue_at_all(self):
        """
        A seed that quietly became `spread` would make every assertion above vacuous — the
        exit fee would be None and a Kraken round trip would silently cost one leg again.
        """
        executor = self._executor_on_the_seed()

        assert executor._fee_model() == FeeType.MAKER_TAKER

