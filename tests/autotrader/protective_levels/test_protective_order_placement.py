"""
FiniexTestingIDE - Placing the Protective Order (#503, stage D1)

The first stage that leaves the process. A declared `stop_loss` becomes a real STOP order
at the venue, so the level survives this process dying — which a thirty-day unattended run
guarantees will happen.

Three moments are deliberately kept apart, and the whole safety of the feature is in that
separation:

    submit        protective_order_id set · the stamp is NOT · the local check WATCHES
    confirmation  the stamp falls · the local check stands down for this stop
    death         the stamp is cleared · the local check takes it back next tick

Between submitting and hearing back, nobody at the venue holds anything. Standing down on
the declaration rather than on the confirmation would leave the level with neither — the
defect #500 exists to prevent, reinstated at the exact moment it is hardest to notice.
"""

from python.framework.logging.global_logger import GlobalLogger
from python.framework.testing.mock_broker_adapter import MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderStatus,
    OrderType,
    ProtectiveLevelEnforcement,
)
from tests.autotrader.protective_levels.conftest import VenueHoldsProtectionMock

_SYMBOL = 'BTCUSD'


def _executor(profile_default: bool = True) -> LiveTradeExecutor:
    """
    A live executor over a venue that can hold a protective order.

    Args:
        profile_default: The profile's execution.venue_held_protection

    Returns:
        The executor
    """
    return LiveTradeExecutor(
        broker_config=BrokerConfig(
            BrokerType.KRAKEN_SPOT,
            VenueHoldsProtectionMock(mode=MockExecutionMode.INSTANT_FILL)),
        initial_balance=100000.0,
        account_currency='USD',
        logger=GlobalLogger('ProtectivePlacement'),
        venue_held_protection=profile_default,
    )


def _open_protected_long(executor, mock, stop_loss=49000.0, take_profit=None):
    """
    Fill one protected LONG through the executor.

    Args:
        executor: The live executor
        mock: The tick-feeding harness
        stop_loss: Level to declare, or None
        take_profit: Level to declare, or None

    Returns:
        The open position
    """
    mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
    executor.open_order(OpenOrderRequest(
        symbol=_SYMBOL, order_type=OrderType.MARKET, direction=OrderDirection.LONG,
        lots=0.01, stop_loss=stop_loss, take_profit=take_profit))
    mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
    return executor.get_open_positions()[0]


def _protective_orders(executor):
    """
    The protective orders the executor is holding.

    Args:
        executor: The live executor

    Returns:
        Every resting stop that names a position
    """
    return [p for p in executor._active_stop_orders if p.closes_position_id]


class TestItIsPlacedAtAllAndOnlyWhenAsked:
    """The gate. Placing one nobody asked for spends money on an order the algo
    does not know about; not placing one that was asked for is the silent half."""

    def test_a_protected_entry_produces_a_protective_order(self):
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = _executor()
        position = _open_protected_long(executor, mock)

        protective = _protective_orders(executor)
        assert len(protective) == 1
        assert protective[0].closes_position_id == position.position_id

    def test_it_reverses_the_direction_and_carries_the_trigger(self):
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = _executor()
        position = _open_protected_long(executor, mock, stop_loss=49000.0)

        order = _protective_orders(executor)[0]
        assert order.direction == OrderDirection.SHORT, (
            'What protects a LONG is a sell — the same direction would double the position')
        assert order.order_type == OrderType.STOP
        assert order.entry_price == 49000.0, 'For a stop the resting price IS the trigger'
        assert order.close_lots == position.lots

    def test_it_carries_its_own_id_not_the_positions(self):
        """
        The wire key derives from the ORDER id. Overloading the position's id would
        collide with a restart's counter, which is why the position is named separately.
        """
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = _executor()
        position = _open_protected_long(executor, mock)

        order = _protective_orders(executor)[0]
        assert order.pending_order_id != position.position_id
        assert order.closes_position_id == position.position_id
        assert position.protective_order_id == order.pending_order_id

    def test_no_opt_in_places_nothing(self):
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = _executor(profile_default=False)
        _open_protected_long(executor, mock)

        assert not _protective_orders(executor)

    def test_an_entry_without_a_stop_places_nothing(self):
        """Nothing to protect. A profile-wide opt-in must not invent a level."""
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = _executor()
        _open_protected_long(executor, mock, stop_loss=None)

        assert not _protective_orders(executor)


class TestTheStampWaitsForTheVenue:
    """
    The one ordering that decides whether a position is ever unwatched.
    """

    def test_the_local_check_still_watches_before_confirmation(self):
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = _executor()
        position = _open_protected_long(executor, mock)
        position.protective_broker_ref = None  # as it is before the venue answers

        assert position.protective_enforcement(ProtectiveLevelEnforcement.LOCAL) == (
            ProtectiveLevelEnforcement.LOCAL), (
            'Between sending and hearing back nobody at the venue holds anything')

    def test_a_confirmation_hands_the_stop_over(self):
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = _executor()
        position = _open_protected_long(executor, mock)
        order = _protective_orders(executor)[0]

        mock.await_submit_confirmation(executor)

        assert order.broker_ref, 'The venue confirmed the order'
        assert position.protective_broker_ref == order.broker_ref
        assert position.protective_enforcement(ProtectiveLevelEnforcement.LOCAL) == (
            ProtectiveLevelEnforcement.VENUE)

    def test_and_then_the_local_check_leaves_that_stop_alone(self):
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = _executor()
        position = _open_protected_long(executor, mock)
        mock.await_submit_confirmation(executor)
        assert position.protective_broker_ref

        mock.feed_tick(executor, symbol=_SYMBOL, bid=48900.0, ask=48901.0)
        mock.feed_tick(executor, symbol=_SYMBOL, bid=48900.0, ask=48901.0)

        assert executor.get_open_positions(), (
            'The venue holds this stop; acting here too would sell the position twice')
        assert executor.get_execution_stats().sl_tp_triggered == 0


class TestTheWindowBeforeTheVenueAnswers:
    """
    Two defects a real field-study run found on 2026-09-10 that no offline test had.

    Both live in the same few seconds: between submitting the protective order and
    hearing back from the venue. Nothing in the mock world is slow enough for that window
    to exist by accident, so both are driven deliberately.
    """

    def test_a_close_in_that_window_still_waits_for_the_cancel(self):
        """
        Measured: the close went out at 15:37:14, the venue confirmed the stop at
        15:37:18, and the order outlived the position it protected — a naked stop resting
        over a holding that was gone.

        The deferral hung off `protective_broker_ref`, which is only set on confirmation.
        It hangs off the order's EXISTENCE now.
        """
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = _executor()
        position = _open_protected_long(executor, mock)
        # The window, reconstructed: this mock answers inside the same tick, so it has to
        # be put back deliberately. Submitted, filed, and no answer from the venue yet.
        order = _protective_orders(executor)[0]
        order.broker_ref = None
        position.protective_broker_ref = None
        assert position.protective_order_id, 'submitted and known'

        result = executor.close_position(position.position_id)

        assert position.position_id in executor._deferred_closes, (
            'A close inside the window must still go behind the cancel')
        assert result.status != OrderStatus.REJECTED
        assert executor.get_open_positions(), 'nothing closed yet'

    def test_its_wire_key_cannot_collide_with_the_next_entry(self):
        """
        Measured: `protect_pos_btcusd_1_2` and the next entry `pos_btcusd_2` produced the
        same client order id, because the key is the id's TRAILING number — and Kraken
        refused the second with `EGeneral:Invalid arguments:cl_ord_id not unique`.

        A private counter for protective orders was the mistake; they draw from the same
        sequence as everything else.
        """
        mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
        executor = _executor()
        _open_protected_long(executor, mock)
        protective_id = _protective_orders(executor)[0].pending_order_id

        # The id the very next entry would be minted under.
        next_entry_id = executor.portfolio.get_next_position_id(_SYMBOL)

        assert protective_id.rsplit('_', 1)[-1] != next_entry_id.rsplit('_', 1)[-1], (
            f'{protective_id} and {next_entry_id} share a trailing counter, so they '
            f'produce the same wire key and the venue refuses the second')
