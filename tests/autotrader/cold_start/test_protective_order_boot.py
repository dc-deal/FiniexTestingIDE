"""
FiniexTestingIDE - What Became of the Protective Order While Nothing Ran (#503, stage E)

The whole point of putting a stop at the venue is that it works when we do not. So the
next boot has to ask what happened to it — and the three answers are not interchangeable.

    still resting   → adopt it back, so this session can amend and cancel it
    it FIRED        → the money already moved; the books catch up or every later number
                      is wrong, starting with the balance cross-check
    unknown         → an ABSENCE, never "still working". The position is unprotected and
                      the local check takes the level back from the first tick

Without the FIRED branch the feature's most ordinary success — a stop that triggers
overnight — makes the next boot report that somebody sold outside this bot. The note still
claims the coin, the account no longer holds it, and the cross-check calls that a shortfall.
"""

from python.framework.autotrader.cold_start_adopter import ColdStartAdopter
from python.framework.types.config_types.autotrader_defaults_config_types import (
    ColdStartDefaults,
)
from python.framework.types.live_types.live_execution_types import (
    BrokerOrderStatus,
    BrokerResponse,
)
from python.framework.types.live_types.reconciliation_types import BrokerOrder
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.persistence_types import PositionCarryOver
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderType
from python.framework.utils.time_utils import parse_datetime

_PROTECTIVE_REF = 'OABCDE-1234-XYZ'


def _carried_long(protective_ref=_PROTECTIVE_REF) -> PositionCarryOver:
    """
    The note an earlier session left about one protected LONG.

    Args:
        protective_ref: The venue's reference for its protective order, or None

    Returns:
        The carry-over record
    """
    return PositionCarryOver(
        position_id='pos_btcusd_1',
        symbol='BTCUSD',
        direction='long',
        lots=0.01,
        original_lots=0.01,
        entry_price=50000.0,
        entry_time='2026-09-09T12:00:00+00:00',
        entry_type='market',
        stop_loss=49000.0,
        contract_size=1,
        digits=1,
        status='open',
        protective_order_id='protect_pos_btcusd_1_2',
        protective_broker_ref=protective_ref,
    )


def _answer(status, filled_lots=None, fill_price=None) -> BrokerResponse:
    """
    One venue answer about a protective order.

    Args:
        status: What the venue says its state is
        filled_lots: Executed volume, when there is any
        fill_price: Price it executed at

    Returns:
        The BrokerResponse the query layer would produce
    """
    return BrokerResponse(
        broker_ref=_PROTECTIVE_REF,
        status=status,
        filled_lots=filled_lots,
        fill_price=fill_price,
        timestamp=parse_datetime('2026-09-10T06:00:00+00:00'),
    )


def _boot(spot_executor, store, logger, answer: BrokerResponse,
          adoption_mode: str = 'auto'):
    """
    Run the cold start with the venue giving one fixed answer about the protective order.

    The answer is injected at the processor's query seam rather than driven through the
    mock adapter: what is under test is the BOOT's reading of it, and the three readings
    differ by exactly this value.

    Args:
        spot_executor: A spot-mode live executor
        store: The carry-over store
        logger: The recording logger
        answer: What the venue says about the protective order
        adoption_mode: The cold-start adoption policy to boot under

    Returns:
        Whether the boot allowed the session to start
    """
    processor = spot_executor.get_request_processor()
    processor.query_order_sync = lambda **kwargs: answer
    return ColdStartAdopter(
        executor=spot_executor,
        store=store,
        config=ColdStartDefaults(adoption_mode=adoption_mode),
        symbol='BTCUSD',
        logger=logger,
    ).run()


class TestAStopThatFiredOvernight:
    """
    The feature working exactly as intended, and the boot has to recognise it as such.
    """

    def test_the_position_is_closed_rather_than_restored_as_open(
        self, spot_executor, store, logger
    ):
        store.save(session_key='paa53', highest_position_counter=1,
                   open_positions=[_carried_long()])

        assert _boot(spot_executor, store, logger,
                     _answer(BrokerOrderStatus.FILLED, 0.01, 49000.0)) is True
        # The boot RECORDS it; the first tick books it. A close cannot be written before
        # a price exists — the trade record needs a bid and an ask.
        assert spot_executor.get_open_positions(), 'not yet — there is no tick'

        spot_executor.on_tick(TickData(
            timestamp=parse_datetime('2026-09-10T06:00:01+00:00'),
            symbol='BTCUSD', bid=49000.0, ask=49001.0, volume=1.0))

        assert not spot_executor.get_open_positions(), (
            'The venue closed it while nothing was running — the books have to catch up')

    def test_and_the_balance_check_does_not_call_it_a_shortfall(
        self, spot_executor, store, logger
    ):
        """
        The silent half. The note claims the coin because it was written before the stop
        fired; the account is right and the note is merely out of date. Reporting that as
        "somebody sold outside this bot" turns the feature's success into an alarm on
        every restart.
        """
        store.save(session_key='paa53', highest_position_counter=1,
                   open_positions=[_carried_long()])

        _boot(spot_executor, store, logger,
              _answer(BrokerOrderStatus.FILLED, 0.01, 49000.0))

        assert not any('short' in message.lower() for message in logger.errors), (
            f'A closed position cannot be short of anything: {logger.errors}')


class TestAStopStillResting:
    """Adopted back, or it rests at the venue with no session aware of it."""

    def test_it_returns_to_this_sessions_stop_world(self, spot_executor, store, logger):
        store.save(session_key='paa53', highest_position_counter=1,
                   open_positions=[_carried_long()])

        assert _boot(spot_executor, store, logger,
                     _answer(BrokerOrderStatus.PENDING)) is True

        position = spot_executor.get_open_positions()[0]
        assert position.protective_broker_ref == _PROTECTIVE_REF
        adopted = [p for p in spot_executor._active_stop_orders
                   if p.closes_position_id == position.position_id]
        assert len(adopted) == 1, (
            'Unadopted it cannot be amended when the level moves, cannot be cancelled '
            'before a close, and comes back at the next boot as a stranger')
        assert adopted[0].broker_ref == _PROTECTIVE_REF


class TestAReferenceTheVenueDoesNotRecognise:
    """
    The reading that must never be "still working". Kraken answers an empty record for a
    txid it never minted, and a bot that read that as PENDING would run for a month
    believing in a protection that is not there.
    """

    def test_the_position_comes_back_unprotected_and_it_is_said_out_loud(
        self, spot_executor, store, logger
    ):
        store.save(session_key='paa53', highest_position_counter=1,
                   open_positions=[_carried_long()])

        assert _boot(spot_executor, store, logger,
                     _answer(BrokerOrderStatus.UNKNOWN)) is True

        position = spot_executor.get_open_positions()[0]
        assert position.protective_broker_ref is None, (
            'No protection at the venue means the local check takes the level back')
        assert not [p for p in spot_executor._active_stop_orders
                    if p.closes_position_id]
        assert any('does not recognise' in message for message in logger.errors), (
            f'An absence has to reach the operator, not a debug line: {logger.errors}')


class TestABookWithoutProtection:
    """The unchanged case — nothing carried a reference, nothing is asked."""

    def test_nothing_is_queried_and_the_position_restores_as_before(
        self, spot_executor, store, logger
    ):
        store.save(session_key='paa53', highest_position_counter=1,
                   open_positions=[_carried_long(protective_ref=None)])
        asked = []
        spot_executor.get_request_processor().query_order_sync = (
            lambda **kwargs: asked.append(kwargs))

        ColdStartAdopter(
            executor=spot_executor, store=store,
            config=ColdStartDefaults(adoption_mode='auto'),
            symbol='BTCUSD', logger=logger,
        ).run()

        assert not asked, 'A book with no protective reference asks the venue nothing'
        assert len(spot_executor.get_open_positions()) == 1


class TestAnOrderIdThatCarriesNoReference:
    """
    The window the carry-over can catch: the protective order was SUBMITTED and the
    session ended before the venue answered. The note then names an order id whose order
    object died with that session, and no reference to ask about it with.

    Left standing, that id is a trap rather than a record. Every close is withheld behind
    a cancel of an order this session cannot find, so the position becomes permanently
    unclosable — and the state is written again on every save, so it survives each further
    restart.
    """

    def test_the_dangling_id_is_cleared_and_said_out_loud(
        self, spot_executor, store, logger
    ):
        store.save(session_key='paa53', highest_position_counter=1,
                   open_positions=[_carried_long(protective_ref=None)])

        assert _boot(spot_executor, store, logger,
                     _answer(BrokerOrderStatus.PENDING)) is True

        position = spot_executor.get_open_positions()[0]
        assert position.protective_order_id is None, (
            'An id with no reference names nothing this session can act on')
        assert position.protective_broker_ref is None
        assert any('no venue reference' in message for message in logger.errors), (
            f'It was submitted with real money behind it: {logger.errors}')

    def test_and_the_position_can_still_be_closed(self, spot_executor, store, logger):
        """The consequence that makes it worth a boot branch of its own."""
        store.save(session_key='paa53', highest_position_counter=1,
                   open_positions=[_carried_long(protective_ref=None)])
        _boot(spot_executor, store, logger, _answer(BrokerOrderStatus.PENDING))
        spot_executor.on_tick(TickData(
            timestamp=parse_datetime('2026-09-10T06:00:01+00:00'),
            symbol='BTCUSD', bid=49000.0, ask=49001.0, volume=1.0))

        result = spot_executor.close_position('pos_btcusd_1')

        assert not result.is_rejected, (
            f'Withheld behind a cancel that can never be scheduled: {result.message}')

class TestTheVenueAlsoLISTSTheProtectiveOrder:
    """
    The seam the carry-over branch alone cannot cover: at boot the venue's open-order list
    contains the protective order too, and there it looks like any other resting order of
    ours — it carries a key this bot minted, and it is a STOP, which the adopter has
    admitted as a resting type since #500.

    Adopted a second time it becomes an ENTRY beside the CLOSE the carry-over files, both
    copies poll the same broker reference, and when the stop fires the entry copy mints a
    phantom position with an entry fee. Under the shipped `operator_confirm` default the
    milder outcome comes first and is still wrong: the bot reports its own stop as an
    unaccounted resting order and an unattended restart refuses to start.
    """

    def _resting_protective_order(self):
        """
        The protective order as the venue's open-order list reports it.

        Returns:
            The BrokerOrder
        """
        return BrokerOrder(
            broker_ref=_PROTECTIVE_REF,
            symbol='BTCUSD',
            direction=OrderDirection.SHORT,
            order_type=OrderType.STOP,
            lots=0.01,
            status=BrokerOrderStatus.PENDING,
            stop_price=49000.0,
            client_order_id='ppaa5_2',
        )

    def test_it_is_adopted_once_as_protection_and_not_again_as_an_entry(
        self, spot_executor, store, logger
    ):
        # A session key of the MINTED width (4 chars), so the venue's echoed wire key
        # parses back into a session this bot has actually sent under — which is the whole
        # precondition for the order becoming an adoption candidate at all.
        store.save(session_key='paa5', highest_position_counter=1,
                   open_positions=[_carried_long()])
        spot_executor.broker.adapter.set_broker_orders([self._resting_protective_order()])

        assert _boot(spot_executor, store, logger,
                     _answer(BrokerOrderStatus.PENDING)) is True

        for_this_ref = [p for p in spot_executor._active_stop_orders
                        if p.broker_ref == _PROTECTIVE_REF]
        assert len(for_this_ref) == 1, (
            f'One venue order, one shadow order. Two would poll the same reference and the '
            f'entry copy would open a phantom position when the stop fires: {for_this_ref}')
        assert for_this_ref[0].closes_position_id == 'pos_btcusd_1', (
            'and the one that survives is the CLOSE, which is what it actually is')

    def test_and_an_unattended_restart_still_starts(self, spot_executor, store, logger):
        """
        The milder outcome, and the likelier one: `operator_confirm` is the SHIPPED default
        and refuses to start while orders of ours rest at the venue that nothing accounts
        for. Counting the bot's own protective order among them means a supervised restart
        at 03:00 comes back, finds its own stop, and stays flat until somebody looks.
        """
        store.save(session_key='paa5', highest_position_counter=1,
                   open_positions=[_carried_long()])
        spot_executor.broker.adapter.set_broker_orders([self._resting_protective_order()])

        started = _boot(spot_executor, store, logger, _answer(BrokerOrderStatus.PENDING),
                        adoption_mode='operator_confirm')

        assert started is True, (
            'Its own protective order is accounted for — it is not a reason to stay flat')
