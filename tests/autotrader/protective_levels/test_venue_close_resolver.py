"""
FiniexTestingIDE - The Venue-Close Resolver (#503, stage C)

The venue reports one fill through several routes: a FILLED query, a terminal answer that
still carries volume, a partial one that does not, and after a restart the boot resolver.
Each is a legitimate way to learn the same fact, so the booking has to be idempotent by
construction rather than by nobody asking twice.

`_apply_venue_close` applies the DELTA against its own counter,
`execution_state.venue_close_applied_lots` (decision 13.a, 2026-09-10). The fills
aggregate cannot serve: it answers how much the VENUE filled, and on a venue with
trade-level reporting the trades drain sets it to the full amount BEFORE anything is
booked — so a delta taken against it would be zero exactly in the case this exists for.

Fed directly here, deliberately. The mock cannot price-trigger a resting stop order, so
driving one through the poll loop would prove the mock rather than the resolver.
"""

from datetime import datetime, timezone

from python.framework.testing.mock_broker_adapter import MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason
from python.framework.types.trading_env_types.broker_trade_types import BrokerTrade
from python.framework.types.trading_env_types.latency_simulator_types import (
    PendingOrder,
    PendingOrderAction,
)
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderSide,
    OrderType,
)

_SYMBOL = 'BTCUSD'


def _live_with_one_long(lots: float = 0.10):
    """
    A live executor holding one filled LONG.

    Args:
        lots: Size of the position

    Returns:
        (mock, executor, position)
    """
    mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL)
    executor = mock.create_executor()
    mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
    executor.open_order(OpenOrderRequest(
        symbol=_SYMBOL, order_type=OrderType.MARKET, direction=OrderDirection.LONG,
        lots=lots, stop_loss=49000.0))
    mock.feed_tick(executor, symbol=_SYMBOL, bid=50000.0, ask=50001.0)
    return mock, executor, executor.get_open_positions()[0]


def _protective_order(position_id: str) -> PendingOrder:
    """
    A protective order the venue holds for the given position.

    Args:
        position_id: The position it protects

    Returns:
        The CLOSE PendingOrder
    """
    return PendingOrder(
        pending_order_id='ord_protect_1',
        order_action=PendingOrderAction.CLOSE,
        order_type=OrderType.STOP,
        broker_ref='OABCDE-1234-XYZ',
        symbol=_SYMBOL,
        direction=OrderDirection.SHORT,
        close_reason=CloseReason.SL_TRIGGERED,
        closes_position_id=position_id,
    )


def _trade(volume: float, price: float, trade_id: str) -> BrokerTrade:
    """
    One execution as the venue reports it.

    Args:
        volume: Lots in this execution
        price: Its price
        trade_id: The venue's trade reference

    Returns:
        The BrokerTrade
    """
    return BrokerTrade(
        trade_id=trade_id,
        parent_broker_ref='OABCDE-1234-XYZ',
        order_id='ord_protect_1',
        volume=volume,
        price=price,
        fee=volume * price * 0.008,
        fee_currency='USD',
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        side=OrderSide.SELL,
        is_maker=False,
    )


class TestTheSameFillArrivesTwice:
    """
    The property the resolver exists for. Booking it twice would close a position that is
    already closed, or book the same lots against the balance a second time.
    """

    def test_a_repeated_report_books_nothing_further(self):
        mock, executor, position = _live_with_one_long()
        protective = _protective_order(position.position_id)

        executor._apply_venue_close(protective, filled_lots=0.10, avg_price=49000.0)
        after_first = len(executor.get_trade_history())
        executor._apply_venue_close(protective, filled_lots=0.10, avg_price=49000.0)

        assert after_first == 1
        assert len(executor.get_trade_history()) == 1, (
            'The same volume arrived again — a second trade record is a second close')

    def test_the_counter_records_what_was_booked(self):
        mock, executor, position = _live_with_one_long()
        protective = _protective_order(position.position_id)

        executor._apply_venue_close(protective, filled_lots=0.10, avg_price=49000.0)

        assert protective.execution_state.venue_close_applied_lots == 0.10

    def test_the_counter_is_not_the_fills_aggregate(self):
        """
        Decision 13.a, asserted rather than assumed.

        On a venue with trade-level reporting the trades drain fills the aggregate BEFORE
        anything is booked. Were the delta taken against it, this close would never
        happen — and the failure would be silent: no error, just a position that stays
        open while the venue has already sold it.
        """
        mock, executor, position = _live_with_one_long()
        protective = _protective_order(position.position_id)
        protective.fills.append_trade(_trade(0.10, 49000.0, 'T-1'))
        assert protective.fills.cumulative_filled_lots == 0.10
        assert protective.execution_state.venue_close_applied_lots == 0.0

        executor._apply_venue_close(protective, filled_lots=0.10, avg_price=49000.0)

        assert not executor.get_open_positions(), (
            'The trades drain had already set the fills aggregate; a delta taken against '
            'it would have been zero and this position would still be open')


class TestAPartialThenTheRest:
    """
    Kraken has no PARTIALLY_FILLED: a half-filled order stays `open` and reports what
    executed beside it. Both halves are real lots leaving the position.
    """

    def test_it_books_a_partial_close_then_the_close(self):
        mock, executor, position = _live_with_one_long(lots=0.10)
        protective = _protective_order(position.position_id)

        executor._apply_venue_close(protective, filled_lots=0.04, avg_price=49000.0)
        assert len(executor.get_open_positions()) == 1, 'Lots remain — still open'
        assert executor.get_open_positions()[0].lots == 0.06

        executor._apply_venue_close(protective, filled_lots=0.10, avg_price=48950.0)
        assert not executor.get_open_positions()
        assert len(executor.get_trade_history()) == 2, (
            'One partial-close record and one close record')

    def test_the_second_booking_does_not_reuse_the_first_executions(self):
        """
        `_fill_close_order` hands its trade list to the portfolio as the closing record's
        executions. Handed the SAME list twice, the second close would report the first
        partial's executions again — and the fee with them.
        """
        mock, executor, position = _live_with_one_long(lots=0.10)
        protective = _protective_order(position.position_id)
        protective.fills.append_trade(_trade(0.04, 49000.0, 'T-1'))

        executor._apply_venue_close(protective, filled_lots=0.04, avg_price=49000.0)
        protective.fills.append_trade(_trade(0.06, 48950.0, 'T-2'))
        executor._apply_venue_close(protective, filled_lots=0.10, avg_price=48970.0)

        first, second = executor.get_trade_history()[-2:]
        assert [t.trade_id for t in first.exit_trades] == ['T-1']
        assert [t.trade_id for t in second.exit_trades] == ['T-2'], (
            'The second close must carry only the executions it actually booked')


class TestWhatItCannotAttributeIsNeverSilent:
    """
    §35: an error the operator must see reaches the session channel. The venue's own
    reference is the only handle left for a manual check, so it is always named.
    """

    def test_a_close_for_a_position_we_no_longer_hold_is_reported(self, capsys):
        mock, executor, position = _live_with_one_long()
        protective = _protective_order('pos_btcusd_does_not_exist')
        capsys.readouterr()

        executor._apply_venue_close(protective, filled_lots=0.10, avg_price=49000.0)

        printed = capsys.readouterr().out
        assert len(executor.get_open_positions()) == 1, 'Nothing was booked'
        assert 'OABCDE-1234-XYZ' in printed
        assert 'no longer holds' in printed

    def test_more_lots_than_the_position_holds_is_reported(self, capsys):
        mock, executor, position = _live_with_one_long(lots=0.10)
        protective = _protective_order(position.position_id)
        capsys.readouterr()

        executor._apply_venue_close(protective, filled_lots=0.25, avg_price=49000.0)

        printed = capsys.readouterr().out
        assert not executor.get_open_positions(), 'What COULD be attributed was booked'
        assert 'OABCDE-1234-XYZ' in printed
        assert 'unattributable' in printed

    def test_an_unattributable_report_is_not_replayed_forever(self):
        """
        The counter advances even where nothing could be booked. Otherwise every poll
        would rediscover the same orphan volume and write the same error again, burying
        the session channel the message has to reach.
        """
        mock, executor, position = _live_with_one_long()
        protective = _protective_order('pos_btcusd_does_not_exist')

        executor._apply_venue_close(protective, filled_lots=0.10, avg_price=49000.0)

        assert protective.execution_state.venue_close_applied_lots == 0.10


class TestTheCounterRecordsWhatWasBookedNotWhatWasAsked:
    """
    `_fill_close_order` does not always book what it was handed: it converts a partial into
    a FULL close when the remainder would fall under the symbol's volume_min. A counter
    that recorded the REQUEST would then under-record, and the venue's next report of the
    same volume would look like an unattributable excess — a false alarm about a healthy
    close, written into the session channel where the real ones live.
    """

    def test_a_remainder_below_volume_min_advances_the_counter_to_the_whole_position(self):
        mock, executor, position = _live_with_one_long(lots=0.10)
        protective = _protective_order(position.position_id)
        # Leaves 0.00002 lots, below the mock's volume_min of 5e-05 → full close.
        executor._apply_venue_close(protective, filled_lots=0.09998, avg_price=49000.0)

        assert not executor.get_open_positions(), 'It was converted to a full close'
        assert protective.execution_state.venue_close_applied_lots == 0.10, (
            'The counter has to record the 0.10 that was booked, not the 0.09998 asked for')

    def test_and_the_venues_next_report_is_then_a_clean_no_op(self, capsys):
        mock, executor, position = _live_with_one_long(lots=0.10)
        protective = _protective_order(position.position_id)
        executor._apply_venue_close(protective, filled_lots=0.09998, avg_price=49000.0)
        capsys.readouterr()

        executor._apply_venue_close(protective, filled_lots=0.10, avg_price=49000.0)

        printed = capsys.readouterr().out
        assert '❌' not in printed, (
            f'A healthy close must not raise an operator error on the next poll: {printed}')


class TestANoOpIsSilent:
    """
    The idempotency path must be distinguishable from the error path. A no-op that shouted
    "check the account by hand" every poll would bury the messages that mean it.
    """

    def test_the_repeated_report_writes_no_error(self, capsys):
        mock, executor, position = _live_with_one_long()
        protective = _protective_order(position.position_id)
        executor._apply_venue_close(protective, filled_lots=0.10, avg_price=49000.0)
        capsys.readouterr()

        executor._apply_venue_close(protective, filled_lots=0.10, avg_price=49000.0)

        assert '❌' not in capsys.readouterr().out


class TestAFilledAnswerWithoutVolumeStillBooks:
    """
    Kraken reports `vol_exec` on every status, but a FILLED answer that carries none must
    not be read as "nothing was closed". A stop-out that leaves no trace is
    indistinguishable from a stop that never fired.
    """

    def test_it_falls_back_to_the_positions_own_size(self):
        mock, executor, position = _live_with_one_long(lots=0.10)
        protective = _protective_order(position.position_id)

        executor._route_resting_fill(protective, fill_price=49000.0, filled_lots=None)

        assert not executor.get_open_positions(), (
            'A FILLED protective order with no volume closes the position it was for')

    def test_an_unsizeable_answer_is_reported_rather_than_swallowed(self, capsys):
        mock, executor, position = _live_with_one_long()
        protective = _protective_order('pos_btcusd_does_not_exist')
        capsys.readouterr()

        executor._route_resting_fill(protective, fill_price=49000.0, filled_lots=None)

        printed = capsys.readouterr().out
        assert 'OABCDE-1234-XYZ' in printed
        assert len(executor.get_open_positions()) == 1


class TestTheSynthesisIsNotSuppressedOnASecondBooking:
    """
    `_unbooked_close_trades` slices the executions not yet booked. Once the known ones are
    all consumed the slice is EMPTY — and an empty list is not the same instruction as
    "there are none": handing `[]` down would skip the synthesis and write a close with no
    execution behind it at all.
    """

    def test_the_second_close_still_carries_an_execution(self):
        mock, executor, position = _live_with_one_long(lots=0.10)
        protective = _protective_order(position.position_id)
        protective.fills.append_trade(_trade(0.04, 49000.0, 'T-1'))

        executor._apply_venue_close(protective, filled_lots=0.04, avg_price=49000.0)
        # The venue reports the rest without adding an execution for it.
        executor._apply_venue_close(protective, filled_lots=0.10, avg_price=48950.0)

        second = executor.get_trade_history()[-1]
        assert second.exit_trades, 'A close with no execution behind it is not a record'
        assert second.exit_trades[0].trade_id.startswith('SYNTH-')
