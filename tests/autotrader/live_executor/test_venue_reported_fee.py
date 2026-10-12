"""
FiniexTestingIDE - The Venue's Own Fee in the Books (#508)

A live fill is booked once, from the answer that reports it — with the fee that answer carries,
where the venue reports one there. The mock venue here charges rates of its own (0.40 / 0.80 %)
while the broker configuration declares 0.16 / 0.26 %, which is exactly the state the issue was
measured in: an account whose real tier is not the declared one, so the venue's figure and our
estimate differ and the test can tell which one was booked.

Pinned in both account models: what is booked, what the order events say about its source, what
the balance moves by — and that the estimate, where it still has to be used, is never silent
where the venue should have reported its figure.
"""

import copy
from typing import List

import pytest

from python.framework.logging.global_logger import GlobalLogger
from python.framework.persistence.position_book_projection import position_to_carry_over
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.live_types.live_execution_types import TimeoutConfig
from python.framework.types.live_types.live_request_types import TradesQueryResponse
from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason
from python.framework.types.trading_env_types.broker_trade_types import BrokerTrade
from python.framework.types.trading_env_types.broker_types import BrokerType, FeeSource
from python.framework.types.trading_env_types.latency_simulator_types import (
    PendingOrder,
    PendingOrderAction,
)
from python.framework.types.trading_env_types.order_event_types import (
    OrderEvent,
    OrderEventType,
)
from python.framework.types.trading_env_types.order_types import (
    OrderAction,
    OrderDirection,
    OrderSide,
    OrderType,
)
from tests.framework.order_events.conftest import (
    ASK,
    BID,
    limit_order,
    live_session,
    make_simulator,
    market_order,
    record_events,
    sim_tick,
)

ACCOUNT_MODELS = pytest.mark.parametrize('spot_mode', [False, True], ids=['margin', 'spot'])

# The mock venue's own rates, apart from the declared 0.16 / 0.26 %
_VENUE_MAKER_PCT = 0.40
_VENUE_TAKER_PCT = 0.80
_DECLARED_TAKER_PCT = 0.26
# What the session starts with — the spot inventory's quote, and the margin account's balance
_START_USD = 100000.0


def _venue_session(spot_mode: bool = False,
                   mode: MockExecutionMode = MockExecutionMode.DELAYED_FILL):
    """
    A live session against a mock venue charging rates of its own.

    Args:
        spot_mode: The account model
        mode: How the mock venue fills

    Returns:
        (mock, executor, recorded events, the mock venue)
    """
    mock, executor, events = live_session(mode=mode, spot_mode=spot_mode)
    venue = executor.broker.adapter
    venue.set_venue_fee_rates(_VENUE_MAKER_PCT, _VENUE_TAKER_PCT)
    return mock, executor, events, venue


def _fills(events: List[OrderEvent], action: OrderAction) -> List[OrderEvent]:
    """
    The fill events of one leg, in stream order.

    Args:
        events: The recorded stream
        action: Open or close

    Returns:
        Its fill events
    """
    return [e for e in events
            if e.event_type in (OrderEventType.FILLED, OrderEventType.PARTIALLY_FILLED)
            and e.action is action]


def _warnings_of(executor: LiveTradeExecutor, monkeypatch) -> List[str]:
    """
    Record every warning the executor writes.

    Args:
        executor: The executor to listen to
        monkeypatch: pytest's monkeypatch

    Returns:
        The list the warnings are appended to
    """
    seen: List[str] = []
    original = executor.logger.warning

    def recording(message, *args, **kwargs):
        seen.append(message)
        original(message, *args, **kwargs)

    monkeypatch.setattr(executor.logger, 'warning', recording)
    return seen


def _open_and_fill(mock: MockOrderExecution, executor: LiveTradeExecutor) -> None:
    """
    Open one market LONG and let the venue's answer arrive.

    Args:
        mock: The mock harness
        executor: The executor
    """
    executor.open_order(market_order())
    mock.await_submit_confirmation(executor)
    mock.feed_tick(executor, bid=BID, ask=ASK)


class TestAnEntryIsBookedWithTheVenuesFee:
    """The fee in the answer that reports the fill is the fee the books carry."""

    @ACCOUNT_MODELS
    def test_the_fill_its_event_and_its_execution_carry_the_venues_figure(self, spot_mode):
        mock, executor, events, _ = _venue_session(spot_mode)

        _open_and_fill(mock, executor)

        fill = _fills(events, OrderAction.OPEN)[0]
        charged = fill.lots * fill.fill_price * _VENUE_TAKER_PCT / 100
        estimated = fill.lots * fill.fill_price * _DECLARED_TAKER_PCT / 100
        assert fill.fee == pytest.approx(charged)
        assert fill.fee != pytest.approx(estimated), 'the two rates must differ to tell them apart'
        assert fill.fee_source is FeeSource.VENUE
        assert executor.get_order_history()[-1].commission == pytest.approx(charged)
        assert executor.portfolio.get_cost_breakdown().total_fees == pytest.approx(charged)
        position = executor.get_open_positions()[0]
        assert position.entry_trades[0].fee == pytest.approx(charged)

    def test_on_spot_the_balance_pays_the_venues_figure(self):
        mock, executor, events, _ = _venue_session(spot_mode=True)

        _open_and_fill(mock, executor)

        fill = _fills(events, OrderAction.OPEN)[0]
        charged = fill.lots * fill.fill_price * _VENUE_TAKER_PCT / 100
        assert executor.portfolio.get_asset_balance('USD') == pytest.approx(
            _START_USD - fill.lots * fill.fill_price - charged)

    @ACCOUNT_MODELS
    def test_a_fill_in_the_submit_answer_carries_it_too(self, spot_mode):
        mock, executor, events, _ = _venue_session(spot_mode, mode=MockExecutionMode.INSTANT_FILL)

        executor.open_order(market_order())
        mock.await_submit_confirmation(executor)

        fill = _fills(events, OrderAction.OPEN)[0]
        assert fill.fee_source is FeeSource.VENUE
        assert fill.fee == pytest.approx(fill.lots * fill.fill_price * _VENUE_TAKER_PCT / 100)


class TestACloseIsBookedWithTheVenuesFee:
    """The exit leg books its own answer's fee — a round trip costs the venue's two fees."""

    @ACCOUNT_MODELS
    def test_the_round_trip_costs_the_venues_two_fees(self, spot_mode):
        mock, executor, events, _ = _venue_session(spot_mode)
        _open_and_fill(mock, executor)
        position_id = executor.get_open_positions()[0].position_id

        executor.close_position(position_id)
        mock.await_submit_confirmation(executor)
        mock.feed_tick(executor, bid=BID, ask=ASK)

        entry = _fills(events, OrderAction.OPEN)[0]
        exit_ = _fills(events, OrderAction.CLOSE)[0]
        entry_fee = entry.lots * entry.fill_price * _VENUE_TAKER_PCT / 100
        exit_fee = exit_.lots * exit_.fill_price * _VENUE_TAKER_PCT / 100
        assert exit_.fee == pytest.approx(exit_fee)
        assert exit_.fee_source is FeeSource.VENUE
        record = executor.get_trade_history()[-1]
        assert record.total_fees == pytest.approx(entry_fee + exit_fee)
        assert record.net_pnl == pytest.approx(record.gross_pnl - entry_fee - exit_fee)
        if spot_mode:
            assert executor.portfolio.get_asset_balance('USD') == pytest.approx(
                _START_USD - entry.lots * entry.fill_price - entry_fee
                + exit_.lots * exit_.fill_price - exit_fee)
        else:
            assert executor.get_balance() == pytest.approx(_START_USD + record.net_pnl)



class TestTheEstimateIsNeverBookedUnremarked:
    """Where the venue's figure should have been in the answer and is not, the books say so."""

    @ACCOUNT_MODELS
    def test_an_answer_without_the_fee_is_booked_from_the_estimate_and_warned(
            self, spot_mode, monkeypatch):
        mock, executor, events, venue = _venue_session(spot_mode)
        venue.set_fee_in_status_answer(False)
        warnings = _warnings_of(executor, monkeypatch)

        _open_and_fill(mock, executor)

        fill = _fills(events, OrderAction.OPEN)[0]
        assert fill.fee_source is FeeSource.ESTIMATE
        assert fill.fee == pytest.approx(fill.lots * fill.fill_price * _DECLARED_TAKER_PCT / 100)
        assert any("booked from the fee model's estimate" in w for w in warnings), warnings

    def test_a_venue_that_charges_nothing_per_fill_is_not_warned_about(self, monkeypatch):
        executor, mock = _spread_venue_executor(MockBrokerAdapter)
        warnings = _warnings_of(executor, monkeypatch)
        events = record_events(executor)

        _open_and_fill(mock, executor)

        fill = _fills(events, OrderAction.OPEN)[0]
        assert fill.fee_source is FeeSource.ESTIMATE
        assert not fill.fee
        assert not [w for w in warnings if 'fee' in w], warnings

    def test_a_fee_the_model_does_not_charge_is_warned_about_and_not_booked(self, monkeypatch):
        executor, mock = _spread_venue_executor(_SpreadVenueThatReportsAFee)
        warnings = _warnings_of(executor, monkeypatch)
        events = record_events(executor)

        _open_and_fill(mock, executor)

        fill = _fills(events, OrderAction.OPEN)[0]
        assert not fill.fee
        assert fill.fee_source is FeeSource.ESTIMATE
        assert any('NOT booked' in w for w in warnings), warnings


class _SpreadVenueThatReportsAFee(MockBrokerAdapter):
    """A venue the configured spread model does not describe: it reports a fee per fill."""

    def _filled_answer(self, broker_ref, fill_price, filled_lots, fill_fee):
        """
        The FILLED answer with a fee the spread model knows nothing of.

        Args:
            broker_ref: The order's reference
            fill_price: The price it filled at
            filled_lots: The volume it filled
            fill_fee: The fee this mock computed — zero for a spread model

        Returns:
            The raw answer, with a fee
        """
        answer = super()._filled_answer(broker_ref, fill_price, filled_lots, fill_fee)
        answer['fill_fee'] = 1.25
        return answer


def _spread_venue_executor(adapter_class):
    """
    A live executor against a mock venue whose broker charges a spread, not a fee per fill.

    Args:
        adapter_class: The mock adapter class to build

    Returns:
        (executor, a harness for feeding ticks) — the first tick already fed
    """
    config = copy.deepcopy(MockBrokerAdapter().broker_config)
    config['fee_structure'] = {'model': 'spread'}
    return _executor_against(adapter_class(mode=MockExecutionMode.DELAYED_FILL,
                                           broker_config=config))


def _executor_against(adapter: MockBrokerAdapter):
    """
    A live executor against a given mock venue.

    Args:
        adapter: The mock venue

    Returns:
        (executor, a harness for feeding ticks) — the first tick already fed
    """
    executor = LiveTradeExecutor(
        broker_config=BrokerConfig(BrokerType.KRAKEN_SPOT, adapter),
        initial_balance=_START_USD,
        account_currency='USD',
        logger=GlobalLogger(name='VenueFeeTest'),
        timeout_config=TimeoutConfig(order_timeout_seconds=30.0),
        session_key='mock',
    )
    mock = MockOrderExecution(mode=MockExecutionMode.DELAYED_FILL)
    mock.feed_tick(executor, bid=BID, ask=ASK)
    return executor, mock


class TestAProtectiveOrderBooksInSteps:
    """
    The venue reports one total for everything a protective order executed — its volume, its
    average price and its fee — and the resolver books it in steps: each step books what a
    total grew by since the last one, and a step whose fee is not that growth says why on its
    event. Live only at spot today, where Kraken holds the order; pinned in both account models
    because the arithmetic is shared.
    """

    def _held_long(self, spot_mode: bool = False):
        """
        A filled LONG of 0.10 and the protective order the venue holds over it.

        Args:
            spot_mode: The account model

        Returns:
            (executor, the protective order, recorded events)
        """
        mock = MockOrderExecution(
            mode=MockExecutionMode.INSTANT_FILL, spot_mode=spot_mode,
            initial_balance=_START_USD,
            initial_balances={'USD': _START_USD, 'BTC': 0.0} if spot_mode else None)
        executor = mock.create_executor()
        events = record_events(executor)
        mock.feed_tick(executor, bid=50000.0, ask=50001.0)
        executor.open_order(market_order(lots=0.10))
        mock.feed_tick(executor, bid=50000.0, ask=50001.0)
        position = executor.get_open_positions()[0]
        protective = PendingOrder(
            pending_order_id='ord_protect_1',
            order_action=PendingOrderAction.CLOSE,
            order_type=OrderType.STOP,
            broker_ref='OABCDE-1234-XYZ',
            symbol='BTCUSD',
            direction=OrderDirection.SHORT,
            close_reason=CloseReason.SL_TRIGGERED,
            closes_position_id=position.position_id,
        )
        return executor, protective, events

    @ACCOUNT_MODELS
    def test_two_steps_book_the_growth_of_each_total(self, spot_mode):
        executor, protective, events = self._held_long(spot_mode)

        executor._apply_venue_close(protective, filled_lots=0.04, avg_price=49000.0,
                                    reported_fee=15.68)
        executor._apply_venue_close(protective, filled_lots=0.10, avg_price=48950.0,
                                    reported_fee=39.18)

        closes = _fills(events, OrderAction.CLOSE)
        assert [(e.fee, e.fee_source) for e in closes] == [
            (pytest.approx(15.68), FeeSource.VENUE), (pytest.approx(23.50), FeeSource.VENUE)]
        # The second step at its OWN price, not at the average over both:
        # (0.10 x 48950 - 0.04 x 49000) / 0.06 = 2935 / 0.06
        step_price = 2935.0 / 0.06
        first, second = executor.get_trade_history()[-2:]
        assert (first.exit_price, second.exit_price) == (
            pytest.approx(49000.0), pytest.approx(step_price))
        entry = _fills(events, OrderAction.OPEN)[0]
        assert first.total_fees + second.total_fees == pytest.approx(entry.fee + 39.18)
        for record in (first, second):
            assert record.net_pnl == pytest.approx(record.gross_pnl - record.total_fees)
        if spot_mode:
            assert executor.portfolio.get_asset_balance('USD') == pytest.approx(
                _START_USD - entry.lots * entry.fill_price - entry.fee
                + 0.04 * 49000.0 - 15.68 + 0.06 * step_price - 23.50)
        else:
            assert executor.get_balance() == pytest.approx(
                _START_USD + first.net_pnl + second.net_pnl)
        assert not executor.get_open_positions()

    @ACCOUNT_MODELS
    def test_a_step_booked_from_the_estimate_is_settled_by_the_next_total(self, spot_mode):
        """
        The first answer carried no fee, so its step was booked from the estimate. The venue's
        next total covers that step too — it books what the total exceeds the books by, so the
        order costs what the venue charged, once.
        """
        executor, protective, events = self._held_long(spot_mode)

        executor._apply_venue_close(protective, filled_lots=0.04, avg_price=49000.0)
        executor._apply_venue_close(protective, filled_lots=0.10, avg_price=48950.0,
                                    reported_fee=39.18)

        first, second = _fills(events, OrderAction.CLOSE)
        assert (first.fee_source, second.fee_source) == (FeeSource.ESTIMATE, FeeSource.VENUE)
        assert first.fee > 0
        assert first.fee + second.fee == pytest.approx(39.18)

    @ACCOUNT_MODELS
    def test_the_step_that_settles_an_estimate_says_so(self, spot_mode):
        """
        The settling step books the venue's total less what is already booked, which is not
        what the total grew by — its event says why. Once settled, nothing of the books is an
        estimate any more: the next step books what the total grew by and says nothing.
        """
        executor, protective, events = self._held_long(spot_mode)

        executor._apply_venue_close(protective, filled_lots=0.04, avg_price=49000.0)
        executor._apply_venue_close(protective, filled_lots=0.07, avg_price=48980.0,
                                    reported_fee=27.43)
        executor._apply_venue_close(protective, filled_lots=0.10, avg_price=48950.0,
                                    reported_fee=39.18)

        estimated, settling, last = _fills(events, OrderAction.CLOSE)
        assert (estimated.fee_source, estimated.message) == (FeeSource.ESTIMATE, None)
        assert settling.message == (
            f"the venue's total {27.43:.8f} less {estimated.fee:.8f} already booked, "
            f'{estimated.fee:.8f} of it from the estimate')
        assert settling.fee == pytest.approx(27.43 - estimated.fee)
        assert (last.fee, last.message) == (pytest.approx(39.18 - 27.43), None)
        assert protective.execution_state.venue_close_estimated_fee == 0.0

    @ACCOUNT_MODELS
    def test_an_estimate_above_the_venues_later_total_stays_booked_at_that_step(
            self, spot_mode, monkeypatch):
        """
        The first step was booked from the declared rate; the venue's total for the whole order
        comes in below it — an account on a cheaper tier than declared. The second step books no
        fee, what the estimate booked beyond the venue's figure stays booked, and the session log
        warns in the words the step's event carries.
        """
        executor, protective, events = self._held_long(spot_mode)
        warnings = _warnings_of(executor, monkeypatch)
        venue_total = 3.92

        executor._apply_venue_close(protective, filled_lots=0.04, avg_price=49000.0)
        estimated = _fills(events, OrderAction.CLOSE)[0].fee
        executor._apply_venue_close(protective, filled_lots=0.10, avg_price=48950.0,
                                    reported_fee=venue_total)

        first, second = _fills(events, OrderAction.CLOSE)
        assert first.fee_source is FeeSource.ESTIMATE
        assert estimated > venue_total, 'the estimate must exceed the total to test this'
        assert (second.fee, second.fee_source) == (0.0, FeeSource.VENUE)
        assert second.message == (
            f"the venue's total {venue_total:.8f} is below the {estimated:.8f} already booked, "
            f'{estimated:.8f} of it from the estimate — the excess '
            f'{estimated - venue_total:.8f} stays booked until a later total above the books '
            f'absorbs it')
        assert f'⚠️ Protective order OABCDE-1234-XYZ: {second.message}' in warnings
        assert protective.execution_state.venue_close_applied_fee == pytest.approx(estimated)
        entry = _fills(events, OrderAction.OPEN)[0]
        records = executor.get_trade_history()[-2:]
        assert sum(r.total_fees for r in records) == pytest.approx(entry.fee + estimated)
        step_price = 2935.0 / 0.06
        if spot_mode:
            assert executor.portfolio.get_asset_balance('USD') == pytest.approx(
                _START_USD - entry.lots * entry.fill_price - entry.fee
                + 0.04 * 49000.0 - estimated + 0.06 * step_price)
        else:
            assert executor.get_balance() == pytest.approx(
                _START_USD + sum(r.net_pnl for r in records))
        assert not executor.get_open_positions()

    @ACCOUNT_MODELS
    def test_a_later_total_above_the_books_absorbs_the_excess(self, spot_mode):
        """
        The excess an over-estimate left is not reversed at its step, yet nothing is lost: the
        next total that exceeds what is booked books only its growth beyond the books, so the
        order ends at the venue's figure after all.
        """
        executor, protective, events = self._held_long(spot_mode)
        executor._apply_venue_close(protective, filled_lots=0.04, avg_price=49000.0)
        estimated = _fills(events, OrderAction.CLOSE)[0].fee
        below, final_total = estimated * 0.5, estimated * 1.5

        executor._apply_venue_close(protective, filled_lots=0.07, avg_price=48980.0,
                                    reported_fee=below)
        executor._apply_venue_close(protective, filled_lots=0.10, avg_price=48960.0,
                                    reported_fee=final_total)

        fees = [e.fee for e in _fills(events, OrderAction.CLOSE)]
        assert fees[1] == 0.0, 'the overshooting step books nothing'
        assert sum(fees) == pytest.approx(final_total), 'the order ends at the venue total'
        assert not executor.get_open_positions()

    @ACCOUNT_MODELS
    def test_steps_that_all_carry_the_venues_fee_say_nothing_more(self, spot_mode, monkeypatch):
        executor, protective, events = self._held_long(spot_mode)
        warnings = _warnings_of(executor, monkeypatch)

        executor._apply_venue_close(protective, filled_lots=0.04, avg_price=49000.0,
                                    reported_fee=15.68)
        executor._apply_venue_close(protective, filled_lots=0.10, avg_price=48950.0,
                                    reported_fee=39.18)

        assert [e.message for e in _fills(events, OrderAction.CLOSE)] == [None, None]
        assert not [w for w in warnings if 'already booked' in w], warnings

    @ACCOUNT_MODELS
    def test_an_excess_books_only_the_positions_share_of_the_fee(self, spot_mode):
        """
        The venue reports 0.25 lots on a position of 0.10: the excess is not booked, and
        neither is its share of the fee — 50.00 x 0.10 / 0.25 = 20.00.
        """
        executor, protective, events = self._held_long(spot_mode)

        executor._apply_venue_close(protective, filled_lots=0.25, avg_price=49000.0,
                                    reported_fee=50.00)

        assert _fills(events, OrderAction.CLOSE)[0].fee == pytest.approx(20.00)
        assert protective.execution_state.venue_close_applied_fee == pytest.approx(20.00)
        assert not executor.get_open_positions()

    def test_the_same_total_arriving_again_books_nothing(self):
        executor, protective, events = self._held_long()

        executor._apply_venue_close(protective, filled_lots=0.10, avg_price=48950.0,
                                    reported_fee=39.18)
        executor._apply_venue_close(protective, filled_lots=0.10, avg_price=48950.0,
                                    reported_fee=39.18)

        assert [e.fee for e in _fills(events, OrderAction.CLOSE)] == [pytest.approx(39.18)]

    @ACCOUNT_MODELS
    def test_a_filled_answer_without_volume_after_a_step_closes_the_rest(self, spot_mode):
        """
        FILLED with no volume means the order executed all it was for: what it booked plus
        what the position still holds. Read as the order's last step size, it booked nothing.
        """
        executor, protective, events = self._held_long(spot_mode)
        executor._apply_venue_close(protective, filled_lots=0.04, avg_price=49000.0,
                                    reported_fee=15.68)

        executor._route_resting_fill(protective, fill_price=48950.0)

        assert [e.lots for e in _fills(events, OrderAction.CLOSE)] == [
            pytest.approx(0.04), pytest.approx(0.06)]
        assert not executor.get_open_positions()

    def test_a_fill_for_a_position_already_gone_still_carries_the_venues_fee(self):
        executor, protective, events = self._held_long()
        executor._apply_venue_close(protective, filled_lots=0.10, avg_price=48950.0,
                                    reported_fee=39.18)

        executor._apply_venue_close(protective, filled_lots=0.12, avg_price=48940.0,
                                    reported_fee=47.00)

        orphan = _fills(events, OrderAction.CLOSE)[-1]
        assert (orphan.fee, orphan.fee_source) == (pytest.approx(7.82), FeeSource.VENUE)

    def test_what_was_booked_travels_with_the_position_into_the_carry_over(self):
        executor, protective, _ = self._held_long()

        executor._apply_venue_close(protective, filled_lots=0.04, avg_price=49000.0,
                                    reported_fee=15.68)

        note = position_to_carry_over(executor.get_open_positions()[0])
        assert (note.protective_applied_lots, note.protective_applied_fee,
                note.protective_applied_value) == (
            pytest.approx(0.04), pytest.approx(15.68), pytest.approx(1960.0))


class _DryRunVenue(MockBrokerAdapter):
    """A venue in dry run: it declares what the venue does, its answers come from our simulator."""

    def reads_venue_account(self) -> bool:
        """
        Answered by our own simulator.

        Returns:
            False
        """
        return False


class TestADryRunIsNotWarnedAbout:
    """The capability describes the venue; a dry run's answers carry no fee and say nothing."""

    def test_a_fill_without_a_fee_is_booked_from_the_estimate_silently(self, monkeypatch):
        executor, mock = _executor_against(_DryRunVenue(mode=MockExecutionMode.DELAYED_FILL))
        executor.broker.adapter.set_fee_in_status_answer(False)
        warnings = _warnings_of(executor, monkeypatch)
        events = record_events(executor)

        _open_and_fill(mock, executor)

        fill = _fills(events, OrderAction.OPEN)[0]
        assert fill.fee_source is FeeSource.ESTIMATE
        assert fill.fee > 0
        assert not [w for w in warnings if 'fee' in w], warnings


class TestWhoExpectsTheVenuesFee:
    """
    One answer to "should this fill carry the venue's fee", asked of the executor — read by the
    session's warning and stamped into the field study's capture for its certificate.
    """

    def test_a_venue_that_reports_it_in_the_answer(self):
        _, executor, _, _ = _venue_session()

        assert executor.expects_venue_fee_in_fill_answer() is True

    def test_not_in_a_dry_run(self):
        executor, _ = _executor_against(_DryRunVenue(mode=MockExecutionMode.DELAYED_FILL))

        assert executor.expects_venue_fee_in_fill_answer() is False

    def test_not_on_a_venue_that_charges_nothing_per_fill(self):
        executor, _ = _spread_venue_executor(MockBrokerAdapter)

        assert executor.expects_venue_fee_in_fill_answer() is False

    @ACCOUNT_MODELS
    def test_not_in_a_simulation(self, spot_mode):
        """Whatever the configured venue declares — a backtest has no venue to ask."""
        assert make_simulator(spot_mode).expects_venue_fee_in_fill_answer() is False


class TestTheMockVenueChargesARestingLimitAsAMaker:
    """A limit that rested and filled later provided liquidity, which is how the executor files it."""

    @pytest.mark.parametrize('order_type, rate_pct', [
        (OrderType.LIMIT, _VENUE_MAKER_PCT), (OrderType.MARKET, _VENUE_TAKER_PCT)],
        ids=['limit', 'market'])
    def test_the_fee_in_its_fill_answer(self, order_type, rate_pct):
        venue = MockBrokerAdapter(mode=MockExecutionMode.DELAYED_FILL)
        venue.set_venue_fee_rates(_VENUE_MAKER_PCT, _VENUE_TAKER_PCT)
        ref = venue.do_request_submit({
            'symbol': 'BTCUSD', 'direction': OrderDirection.LONG, 'lots': 0.01,
            'expected_price': 50000.0, 'order_type': order_type})['broker_ref']

        answer = venue.do_request_query({'broker_ref': ref})

        assert answer['fill_fee'] == pytest.approx(0.01 * answer['fill_price'] * rate_pct / 100)


class TestTheTradesDrainBooksNothing:
    """
    The venue's executions are evidence about a booking, never a second booking route. A
    response for an order that is still working used to book it from the executions' total.
    """

    def test_a_response_for_a_working_order_leaves_it_working_and_still_reaches_consumers(self):
        mock, executor, events = live_session()
        order_id = executor.open_order(limit_order()).order_id
        mock.await_submit_confirmation(executor)
        working = [o for o in executor.get_active_orders() if o.pending_order_id == order_id][0]
        heard: List[TradesQueryResponse] = []
        executor.add_trades_response_consumer(heard.append)

        executor._handle_trades_response(TradesQueryResponse(
            order_id=order_id,
            broker_ref=working.broker_ref,
            trades=[BrokerTrade(
                trade_id='T-1', parent_broker_ref=working.broker_ref, order_id=order_id,
                volume=working.lots, price=40000.0, fee=1.0, fee_currency='USD',
                timestamp=executor.get_current_time(), side=OrderSide.BUY, is_maker=True)],
            success=True,
        ))

        assert [o.pending_order_id for o in executor.get_active_orders()] == [order_id]
        assert not executor.get_open_positions()
        assert not working.fills.trades, 'nothing is written onto the order'
        assert [r.order_id for r in heard] == [order_id]


class TestTheSimulationEstimates:
    """A backtest has no venue to ask: its fee is the model's, and its events say so."""

    @ACCOUNT_MODELS
    def test_a_simulated_fill_says_estimate(self, spot_mode):
        sim = make_simulator(spot_mode)
        events = record_events(sim)
        sim_tick(sim, msc=1_000)
        sim.open_order(market_order())
        sim_tick(sim, msc=2_000)

        fill = _fills(events, OrderAction.OPEN)[0]
        assert fill.fee_source is FeeSource.ESTIMATE
        assert fill.fee > 0
