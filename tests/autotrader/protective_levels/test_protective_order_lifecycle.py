"""
FiniexTestingIDE - The Protective Order's Life at the Venue (#503, stages D2-D6)

Placing one is the easy half. What decides whether a thirty-day run is actually protected
is what happens to it afterwards — when the strategy moves the level, when a close arrives
by another route, when a partial leaves a smaller position behind, and when the session
ends.

Every case here has the same shape of failure and it is always silent: the console shows a
level, the venue holds a different one or none at all, and nothing says so. That is why
each of these ends in the session channel rather than in a debug line.
"""

from datetime import datetime, timezone

from python.framework.logging.global_logger import GlobalLogger
from python.framework.testing.mock_broker_adapter import MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.live_types.live_execution_types import (
    BrokerOrderStatus,
    BrokerResponse,
)
from python.framework.types.live_types.reconciliation_types import BrokerOrder
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.latency_simulator_types import (
    PendingOperation,
    PendingOrder,
    PendingOrderAction,
)
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderStatus,
    OrderType,
    ProtectiveLevelEnforcement,
)
from tests.autotrader.protective_levels.conftest import VenueHoldsProtectionMock

_SYMBOL = 'BTCUSD'


def _protected_position(lots: float = 0.10, stop_loss: float = 49000.0):
    """
    A live LONG whose stop the venue is already holding.

    Args:
        lots: Position size
        stop_loss: The declared level

    Returns:
        (mock, executor, position, protective order)
    """
    mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
    executor = LiveTradeExecutor(
        broker_config=BrokerConfig(
            BrokerType.KRAKEN_SPOT,
            VenueHoldsProtectionMock(mode=MockExecutionMode.INSTANT_FILL)),
        initial_balance=100000.0,
        account_currency='USD',
        logger=GlobalLogger('ProtectiveLifecycle'),
        venue_held_protection=True,
    )
    mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
    executor.open_order(OpenOrderRequest(
        symbol=_SYMBOL, order_type=OrderType.MARKET, direction=OrderDirection.LONG,
        lots=lots, stop_loss=stop_loss))
    mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
    mock.await_submit_confirmation(executor)
    position = executor.get_open_positions()[0]
    protective = [p for p in executor._active_stop_orders if p.closes_position_id][0]
    assert position.protective_broker_ref, 'the venue confirmed it'
    return mock, executor, position, protective


class TestTheOrderFollowsTheLevel:
    """
    D2. A level that moves locally while the venue keeps enforcing the old one is the
    worst kind of wrong: the console shows the new number and the money obeys the old.
    """

    def test_moving_the_stop_amends_the_order_rather_than_replacing_it(self):
        mock, executor, position, protective = _protected_position()

        executor.modify_position(position.position_id, new_stop_loss=49500.0)

        assert protective in executor._active_stop_orders, (
            'An amend keeps the order — a cancel-replace would open a window with no '
            'protection at the venue at all, on every move of a trailing stop')
        assert protective.execution_state.pending_modification is not None

    def test_withdrawing_the_stop_cancels_the_order(self):
        """
        The other direction, and it cannot be amended into: an order with no level is
        still an order, and the venue would go on enforcing the level the strategy just
        withdrew.
        """
        mock, executor, position, protective = _protected_position()

        executor.modify_position(position.position_id, new_stop_loss=None)

        assert protective.execution_state.in_flight_operation == (
            PendingOperation.PENDING_CANCEL)

    def test_an_unchanged_level_touches_nothing(self):
        mock, executor, position, protective = _protected_position()

        executor.modify_position(position.position_id, new_take_profit=52000.0)

        assert protective.execution_state.pending_modification is None, (
            'Only the stop moved the order; a target is not its business')

    def test_a_refused_amend_says_which_level_the_venue_is_really_holding(self, capsys):
        mock, executor, position, protective = _protected_position()
        # Busy with something else → modify_stop_order refuses locally.
        protective.execution_state.in_flight_operation = PendingOperation.PENDING_CANCEL
        capsys.readouterr()

        executor.modify_position(position.position_id, new_stop_loss=49500.0)

        printed = capsys.readouterr().out
        assert 'could NOT follow' in printed
        assert '49000' in printed, 'the level the venue is still holding has to be named'


class TestACloseWaitsForTheCancel:
    """
    D3. There is no reduce_only at spot, and a market close is measured to go through
    while a stop rests over the same holding. Both can fill; the second sells a holding
    that is gone.
    """

    def test_a_refused_cancel_withholds_the_close_and_says_so(self, capsys):
        mock, executor, position, protective = _protected_position()
        # Busy → the cancel cannot even be scheduled.
        protective.execution_state.in_flight_operation = PendingOperation.PENDING_MODIFY
        capsys.readouterr()

        result = executor.close_position(position.position_id)

        printed = capsys.readouterr().out
        assert result.is_rejected, 'the close must not go out beside a live stop'
        assert executor.get_open_positions(), (
            'the position stays OPEN — and protected, which is the safe end of the two')
        assert 'stays open and protected' in printed


class TestWhatSurvivesAPartialCloseIsProtectedAgain:
    """
    D4. The protective order is cancelled to let the close through; a partial leaves a
    smaller position behind, and without a fresh order that remainder rests at the venue
    with no stop over it — from an entirely routine partial close.
    """

    def test_a_partial_close_re_places_at_the_remaining_size(self):
        mock, executor, position, protective = _protected_position(lots=0.10)

        executor.close_position(position.position_id, lots=0.04)
        mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
        mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)

        remaining = executor.get_open_positions()[0]
        assert remaining.lots == 0.06
        fresh = [p for p in executor._active_stop_orders
                 if p.closes_position_id == remaining.position_id]
        assert len(fresh) == 1, 'the remainder is protected again'
        assert fresh[0].close_lots == 0.06, 'and at its NEW size, not the old one'

    def test_a_full_close_leaves_nothing_to_protect(self):
        mock, executor, position, protective = _protected_position(lots=0.10)

        executor.close_position(position.position_id)
        mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
        mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)

        assert not executor.get_open_positions()
        assert not [p for p in executor._active_stop_orders if p.closes_position_id], (
            'no position, no protective order — inventing one would be worse than none')


class TestSessionEndLeavesItStanding:
    """
    D5. The one order whose entire purpose is to outlive this process. The only pair a
    session can start with today (cancel orders + leave positions) would otherwise cancel
    the protection at exactly the moment the bot stops looking.
    """

    def test_it_is_exempt_from_the_cancel_policy(self, capsys):
        mock, executor, position, protective = _protected_position()
        capsys.readouterr()

        executor.finish_remaining_orders(cancel_orders=True)

        printed = capsys.readouterr().out
        assert 'LEFT STANDING at the venue' in printed
        assert protective in executor._active_stop_orders, (
            'It protects an open position while nothing is running')


class TestARefusedProtectiveAmendDoesNotArmTheCooldown:
    """
    D6. Measured on the trailing stop: 378 amends over 62 positions, worst burst 20 in 39
    ticks. Two rejections arm a 60-second block on every new order in that direction —
    the REPLACEMENT protective order included. A refused amend is not a new-order
    rejection, because no new order was attempted.
    """

    def _refuse_a_modify(self, executor, pending):
        """
        Deliver a broker refusal for one order's amend.

        Args:
            executor: The live executor
            pending: The order whose amend is refused
        """
        executor._handle_modify_response(pending.pending_order_id, BrokerResponse(
            broker_ref=pending.broker_ref,
            status=BrokerOrderStatus.REJECTED,
            rejection_reason='EOrder:Invalid price',
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ))

    def test_a_protective_amend_refusal_notifies_no_outcome(self, capsys):
        mock, executor, position, protective = _protected_position()
        outcomes = []
        executor.add_order_outcome_listener(
            lambda direction, result, pending=None: outcomes.append(result))
        capsys.readouterr()

        self._refuse_a_modify(executor, protective)

        assert not outcomes, (
            'The OrderGuard feeds on this fan-out; a protective amend must not reach it')
        assert 'refused an amend of the protective order' in capsys.readouterr().out, (
            'It stays VISIBLE — it simply does not count as a new-order rejection')

    def test_an_ordinary_order_still_arms_it(self):
        """The other direction, so the exemption cannot be read as "never notify"."""
        mock, executor, position, protective = _protected_position()
        # An ENTRY that rests. Built directly because this test is about the outcome
        # fan-out, not about the submit path — and the mock fills a limit on arrival.
        ordinary = PendingOrder(
            pending_order_id='ord_entry_1',
            order_action=PendingOrderAction.OPEN,
            order_type=OrderType.LIMIT,
            broker_ref='MOCK-ORDINARY',
            symbol=_SYMBOL,
            direction=OrderDirection.LONG,
            lots=0.01,
            entry_price=40000.0,
        )
        executor._active_limit_orders.append(ordinary)
        outcomes = []
        executor.add_order_outcome_listener(
            lambda direction, result, pending=None: outcomes.append(result))

        self._refuse_a_modify(executor, ordinary)

        assert len(outcomes) == 1


class TestAnOrphanIsNotExemptAtSessionEnd:
    """
    D5's exemption has one boundary. It covers an order that still HAS something to
    protect; an ORPHAN — one whose position is gone — is the opposite case.

    A stop resting over a holding that no longer exists sells coins that are not ours to
    sell, and in a shared account (#489) they belong to someone else. Kraken links
    nothing, so this cleanup is ours or nobody's.
    """

    def test_it_is_cancelled_with_the_rest(self, capsys):
        mock, executor, position, protective = _protected_position()
        # The position is gone; the order that protected it is not.
        executor.portfolio.open_positions.pop(position.position_id, None)
        capsys.readouterr()

        executor.finish_remaining_orders(cancel_orders=True)

        printed = capsys.readouterr().out
        assert 'LEFT STANDING' not in printed, 'an orphan is not protecting anything'
        assert 'no longer holds' in printed, (
            'and the operator hears about it — it was resting over a phantom')


class TestASecondCloseJoinsTheFirstInsteadOfOvertakingIt:
    """
    D3's other half, and the sequence that reaches it is the most ordinary one there is.

    A deferred close registers NOTHING with the request processor, so `is_pending_close`
    and `has_pending_orders` both answer False while it waits — and the framework's own
    SL/TP check calls `close_position` again on every tick for as long as the level is
    breached. If that second request went through, the close would be sent beside the
    still-resting stop, which is exactly the double-fill the deferral exists to prevent.
    """

    def test_the_repeat_request_sends_nothing(self):
        mock, executor, position, protective = _protected_position()

        first = executor.close_position(position.position_id)
        second = executor.close_position(position.position_id)

        assert first.status == OrderStatus.PENDING
        assert second.status == OrderStatus.PENDING, (
            'The caller is told the close is coming — it simply is not sent twice')
        assert position.position_id in executor._deferred_closes, (
            'and it is still WAITING, not released by the repeat')
        assert not executor.get_request_processor().get_pending_orders(
            PendingOrderAction.CLOSE), (
            'Not one close may reach the venue while the protective stop still rests')
        assert executor.get_open_positions()

    def test_and_the_waiting_close_still_goes_out_when_the_cancel_confirms(self):
        """The other direction: joining must not mean losing the close."""
        mock, executor, position, protective = _protected_position()
        executor.close_position(position.position_id)
        executor.close_position(position.position_id)

        mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
        mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)

        assert not executor.get_open_positions(), (
            'The cancel confirmed, so the one deferred close was sent and filled')


class TestAReclaimedProtectiveOrderStampsItsPosition:
    """
    The THIRD place a broker_ref goes from None to set: the reconcile pull recognises a
    resting order by the client key we minted and hands the reference back (#355/#473).

    A protective order owes its position the stamp wherever that happens. Without it the
    position still reads LOCAL for a stop the venue is holding, so our own check closes it
    too — and the carry-over then records no reference, which leaves the next boot with an
    order id it can neither ask about nor cancel.
    """

    def test_the_position_hears_about_it(self):
        mock, executor, position, protective = _protected_position()
        # The window put back deliberately: submitted, and the venue has not answered.
        venue_ref = protective.broker_ref
        protective.broker_ref = None
        position.protective_broker_ref = None
        assert position.protective_enforcement(
            ProtectiveLevelEnforcement.LOCAL) == ProtectiveLevelEnforcement.LOCAL

        executor.apply_order_attributions([(protective, BrokerOrder(
            broker_ref=venue_ref,
            symbol=_SYMBOL,
            direction=OrderDirection.SHORT,
            order_type=OrderType.STOP,
            lots=position.lots,
            status=BrokerOrderStatus.PENDING,
            stop_price=49000.0,
            client_order_id='p0001_2',
        ))])

        assert protective.broker_ref == venue_ref
        assert position.protective_broker_ref == venue_ref, (
            'The venue holds this stop; a position that does not know it is watched twice')
        assert position.protective_enforcement(
            ProtectiveLevelEnforcement.LOCAL) == ProtectiveLevelEnforcement.VENUE
