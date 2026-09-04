"""
FiniexTestingIDE - Session-End Cleanup Tests (#492)

What the cleanup actually does to the two things a session can still hold. Drives the live
executor through `MockOrderExecution`, so a venue action is observable: the mock records
what was cancelled, and a position that is not closed simply stays.

The regression that matters most is the one that used to be the CONTRACT: a position open at
the end must NOT produce a trade record. That record was a realised exit filled from the last
tick and booked locally — nothing reached the venue, and at spot it also moved base → quote
in the balance ledger, so the summary reported a coin the account still held.
"""

import inspect

import pytest

from python.framework.testing.mock_broker_adapter import MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderType,
)


def _executor_with_open_position(spot: bool = False):
    """
    A live executor holding exactly one open LONG position.

    Args:
        spot: Whether to run the portfolio in spot mode (balances instead of margin)

    Returns:
        (mock, executor) — the mock is needed to feed further ticks
    """
    kwargs = (
        {'spot_mode': True, 'initial_balances': {'USD': 100000.0, 'BTC': 0.0}}
        if spot else {})
    mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL, **kwargs)
    executor = mock.create_executor()

    mock.feed_tick(executor, bid=59999.0, ask=60001.0)
    executor.open_order(OpenOrderRequest(
        symbol='BTCUSD', order_type=OrderType.MARKET,
        direction=OrderDirection.LONG, lots=0.01))
    mock.feed_tick(executor, bid=59999.0, ask=60001.0)   # the fill drains

    assert len(executor.get_open_positions()) == 1, 'fixture failed to open a position'
    return mock, executor


class TestPositionsSurvive:
    """The cleanup finishes ORDERS. A position is not its business any more."""

    def test_the_position_is_still_open_afterwards(self):
        mock, executor = _executor_with_open_position()
        mock.feed_tick(executor, bid=60500.0, ask=60502.0)

        executor.finish_remaining_orders()

        assert len(executor.get_open_positions()) == 1

    def test_no_trade_record_is_produced(self):
        """The old contract asserted the opposite — that record was a fabricated exit."""
        mock, executor = _executor_with_open_position()
        mock.feed_tick(executor, bid=60500.0, ask=60502.0)

        executor.finish_remaining_orders()

        assert executor.get_trade_history() == []

    def test_no_close_carries_the_reserved_reason(self):
        mock, executor = _executor_with_open_position()
        mock.feed_tick(executor, bid=60500.0, ask=60502.0)

        executor.finish_remaining_orders()

        assert not [t for t in executor.get_trade_history()
                    if t.close_reason == CloseReason.SCENARIO_END]

    def test_the_spot_balance_still_shows_the_coin(self):
        """
        The sharper half of the old defect: the close moved base → quote in the ledger.

        So the summary printed `BTC 0.0` and a raised USD balance while the coin was at the
        broker — the balance line contradicted the account, and the reconciler would flag it
        on the next boot.
        """
        mock, executor = _executor_with_open_position(spot=True)
        mock.feed_tick(executor, bid=60500.0, ask=60502.0)

        executor.finish_remaining_orders()

        balances = executor.portfolio.get_balances()
        assert balances.get('BTC', 0.0) == pytest.approx(0.01), (
            f'the coin left the balance sheet without leaving the account: {balances}')

    def test_it_survives_a_session_that_never_saw_a_tick(self):
        """
        The #355 abort case: a restored position and no price to close it at.

        Fabricating a price is not an option and dereferencing the tick that is not there
        used to take the rest of the cleanup down with it.
        """
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = mock.create_executor()

        executor.finish_remaining_orders()   # must not raise

        assert executor.get_open_positions() == []


class TestCheckCleanShutdownKnowsThePolicy:
    """A position left by DECISION is a note; one that survives unexpectedly is an error."""

    def test_a_policy_left_position_is_not_an_error(self):
        mock, executor = _executor_with_open_position()
        mock.feed_tick(executor, bid=60500.0, ask=60502.0)
        executor.finish_remaining_orders()

        clean = executor.check_clean_shutdown(expect_flat=False)

        assert clean is True, (
            'a position the policy allows to stay must not grade the session as unclean — '
            'it would end every session as FINISHED_WITH_ERRORS and exit 3')

    def test_an_unexpected_survivor_is_still_an_error(self):
        mock, executor = _executor_with_open_position()
        mock.feed_tick(executor, bid=60500.0, ask=60502.0)
        executor.finish_remaining_orders()

        clean = executor.check_clean_shutdown(expect_flat=True)

        assert clean is False

    def test_a_flat_session_is_clean_either_way(self):
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = mock.create_executor()
        mock.feed_tick(executor, bid=59999.0, ask=60001.0)

        assert executor.check_clean_shutdown(expect_flat=True) is True
        assert executor.check_clean_shutdown(expect_flat=False) is True


class TestOrdersAxis:
    """Resting orders are cancelled, or left where they are."""

    def _executor_with_resting_order(self):
        """
        A live executor holding one resting LIMIT order.

        DELAYED_FILL, not INSTANT_FILL: the instant mode fills a LIMIT on submission, so the
        order would never rest and both tests below would pass without an order to act on.

        Returns:
            (mock, executor)
        """
        mock = MockOrderExecution(mode=MockExecutionMode.DELAYED_FILL)
        executor = mock.create_executor()
        mock.feed_tick(executor, bid=59999.0, ask=60001.0)
        executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.LIMIT,
            direction=OrderDirection.LONG, lots=0.01, price=50000.0))
        mock.await_submit_confirmation(executor)

        assert executor.get_pending_stats().active_limit_orders, (
            'fixture failed to place a resting order')
        return mock, executor

    def test_cancel_expires_the_order_locally(self):
        mock, executor = self._executor_with_resting_order()

        executor.finish_remaining_orders(cancel_orders=True)

        expired = [o for o in executor.get_order_history()
                   if getattr(o.status, 'value', o.status) == 'expired']
        assert expired, 'a cancelled resting order must leave an EXPIRED record'

    def test_leave_does_not_expire_it(self):
        """
        Left standing means left in BOTH places.

        An order that can still fill must not be recorded as expired — the record would say
        the order is finished while the venue still has it working.
        """
        mock, executor = self._executor_with_resting_order()

        executor.finish_remaining_orders(cancel_orders=False)

        expired = [o for o in executor.get_order_history()
                   if getattr(o.status, 'value', o.status) == 'expired']
        assert not expired, f'an order left at the venue was recorded as expired: {expired}'


class TestTheEmergencyIsNotFoldedIn:
    """
    #492 gives the emergency mode no behaviour, and that boundary is worth pinning.

    Emergency flattening — the case where liquidating IS right — belongs to the safety
    baseline (#356). Folding it in here would put one code path in charge of both "the
    session is over" and "something went wrong", which is the confusion this policy exists
    to end. And it would misfire: an operator Ctrl+C arrives as `shutdown_mode='emergency'`
    (the grade disambiguates it via `operator_interrupted`, the BEHAVIOUR has no such
    discriminator), so a rule keyed on the mode would liquidate on every manual stop.

    The guard is structural on purpose: there is no emergency session to run here, and a
    test that asserts "nothing happens" against a mode nothing reads would be vacuous. A
    signature that grows a mode parameter is the thing to notice.
    """

    def test_the_cleanup_does_not_take_a_shutdown_mode(self):
        parameters = set(inspect.signature(
            LiveTradeExecutor.finish_remaining_orders).parameters)

        assert parameters == {'self', 'cancel_orders', 'current_msc'}, (
            f'finish_remaining_orders grew a parameter: {sorted(parameters)}. If it is a '
            f'shutdown mode, emergency flattening has arrived here instead of in #356')

    def test_the_shutdown_check_only_asks_about_flatness(self):
        parameters = set(inspect.signature(
            LiveTradeExecutor.check_clean_shutdown).parameters)

        assert parameters == {'self', 'expect_flat'}, (
            f'check_clean_shutdown grew a parameter: {sorted(parameters)}')
