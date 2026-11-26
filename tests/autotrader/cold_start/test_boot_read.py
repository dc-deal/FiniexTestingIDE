"""
The Boot Read — a Fill the Book Does Not Hold (#652)

A session killed between a market submit and its fill leaves the coins at the venue and nothing in its
book. The next start used to read only the venue's open orders, restore a flat book and buy again. Now a
start reads the venue's closed orders since its carry-over was written, files every fill of its own
against the book the carry-over holds, and holds its entries over one the book does not hold.

The venue here is the mock adapter, which answers the closed-orders read from what a test seeds. The
carry-over is written moments before the start, so each test dates its write back first: the venue's
close instants then lie between the write and the start, as they would after a kill.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

import pytest

from python.framework.autotrader.cold_start.cold_start_adopter import ColdStartAdopter
from python.framework.autotrader.safety.unbooked_fill_hold import hold_entries_over_unbooked_fills
from python.framework.testing.mock_broker_adapter import MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.types.autotrader_types.cold_start_types import BootFillClass, BootRead
from python.framework.types.config_types.autotrader_defaults_config_types import ColdStartDefaults
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus
from python.framework.types.live_types.reconciliation_types import BrokerOrder
from python.framework.types.persistence_types import (
    BootReadHoldCarryOver,
    OpenOrderCarryOver,
    PositionCarryOver,
)
from python.framework.types.trading_env_types.entry_hold_types import EntryHoldReason
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderType
from python.framework.utils.run_id_utils import build_client_order_id
from tests.autotrader.cold_start.conftest import PREVIOUS_SESSION_KEY

# How long before the start the carry-over was written, in each test
_WRITTEN_AGO = timedelta(minutes=10)

# A finding a held session carries
_HOLD = BootReadHoldCarryOver(written_at_utc='2026-11-26T09:00:05+00:00',
                              read_from_utc='2026-11-26T08:55:00+00:00', client_order_ids=['p8b3f_8'])


class _Alerts:
    """Records what would have gone out of band."""

    def __init__(self):
        self.sent: List[tuple] = []

    def send(self, kind, level, title, body) -> None:
        self.sent.append((kind, level, title))


def _key(counter: int, session_key: str = PREVIOUS_SESSION_KEY) -> str:
    """The wire key an earlier session sent an order under."""
    return build_client_order_id(session_key, f'pos_btcusd_{counter}')


def _closed(ref: str, counter: int, closed_at: Optional[datetime], direction=OrderDirection.LONG,
            lots: float = 0.001, filled: Optional[float] = None,
            session_key: str = PREVIOUS_SESSION_KEY) -> BrokerOrder:
    """An order the venue reports closed, as the boot read receives it."""
    return BrokerOrder(
        broker_ref=ref, symbol='BTCUSD', direction=direction, order_type=OrderType.MARKET,
        lots=lots, status=BrokerOrderStatus.FILLED,
        filled_lots=lots if filled is None else filled,
        client_order_id=_key(counter, session_key), ts_event=closed_at)


def _note(lots: float = 0.004, broker_ref: Optional[str] = None) -> PositionCarryOver:
    """A remembered spot position, as an earlier session wrote it down."""
    return PositionCarryOver(
        position_id='pos_btcusd_7', symbol='BTCUSD', direction='long', lots=lots, original_lots=lots,
        entry_price=61200.0, entry_time='2026-11-24T02:00:00+00:00', entry_type='market',
        contract_size=1, broker_ref=broker_ref)


def _remember(store, tmp_path: Path, notes: Optional[List[PositionCarryOver]] = None,
              open_orders: Optional[List[OpenOrderCarryOver]] = None) -> datetime:
    """
    Write the carry-over as the killed session left it, dated back, and return its write instant.
    """
    store.save(session_key=PREVIOUS_SESSION_KEY, highest_position_counter=7,
               open_positions=list(notes or []), open_orders_at_write=open_orders)
    written_at = datetime.now(timezone.utc) - _WRITTEN_AGO
    for document in (tmp_path / 'cold_start_state').glob('*.json'):
        envelope = json.loads(document.read_text())
        envelope['saved_at_utc'] = written_at.isoformat()
        document.write_text(json.dumps(envelope))
    return written_at


def _adopter(executor, store, logger, dry_run: bool = False) -> ColdStartAdopter:
    """The boot step under test, adopting without asking."""
    return ColdStartAdopter(
        executor=executor, store=store, config=ColdStartDefaults(adoption_mode='auto'),
        symbol='BTCUSD', logger=logger, dry_run=dry_run, interactive=False)


def _read(executor, store, logger) -> BootRead:
    """Run the boot step and hand back its boot read."""
    adopter = _adopter(executor, store, logger)
    assert adopter.run() is True
    boot_read = adopter.get_boot_read()
    assert boot_read is not None
    return boot_read


class TestAFillAfterTheKill:

    def test_an_entry_the_book_does_not_hold_holds_the_entries(self, spot_executor, store, logger, tmp_path):
        written_at = _remember(store, tmp_path)
        spot_executor.broker.adapter.set_closed_broker_orders(
            [_closed('OQX3', 8, written_at + timedelta(seconds=30))])

        boot_read = _read(spot_executor, store, logger)

        (fill,) = boot_read.fills
        assert fill.fill_class == BootFillClass.UNBOOKED_ENTRY
        assert boot_read.holds_entries()
        held = hold_entries_over_unbooked_fills(boot_read, spot_executor, attended=False,
                                                operator_alerts=_Alerts(), logger=logger)
        assert held is True
        assert spot_executor.get_entry_holds().is_held(EntryHoldReason.UNBOOKED_FILL), (
            'the strategy must not buy the same position a second time')
        assert any('OQX3' in e and 'does not hold' in e for e in logger.errors)

    def test_the_balance_is_read_with_an_empty_book_once_a_fill_is_found(
            self, spot_executor, store, logger, tmp_path):
        written_at = _remember(store, tmp_path)
        adapter = spot_executor.broker.adapter
        adapter.set_closed_broker_orders([_closed('OQX3', 8, written_at + timedelta(seconds=30))])
        adapter.set_broker_balances({'XXBT': 0.001, 'ZUSD': 938.0})

        _read(spot_executor, store, logger)

        line = next(i for i in logger.infos if 'beyond the book' in i)
        assert 'found 1 such fill' in line and "not this bot's to use" not in line

    def test_a_clean_start_reads_and_holds_nothing(self, spot_executor, store, logger, tmp_path):
        _remember(store, tmp_path)

        boot_read = _read(spot_executor, store, logger)

        assert (boot_read.fills, boot_read.complete, boot_read.holds_entries()) == ([], True, False)

    def test_another_sessions_key_is_not_ours(self, spot_executor, store, logger, tmp_path):
        written_at = _remember(store, tmp_path)
        spot_executor.broker.adapter.set_closed_broker_orders(
            [_closed('OF1', 8, written_at + timedelta(seconds=30), session_key='zz99')])

        assert _read(spot_executor, store, logger).fills == []


class TestWhatTheBookAlreadyHolds:

    def test_the_entry_of_a_restored_position_is_accounted_for(self, spot_executor, store, logger, tmp_path):
        written_at = _remember(store, tmp_path, notes=[_note(broker_ref='OENTRY')])
        spot_executor.broker.adapter.set_broker_balances({'XXBT': 0.004, 'ZUSD': 750.0})
        spot_executor.broker.adapter.set_closed_broker_orders(
            [_closed('OENTRY', 7, written_at + timedelta(seconds=2), lots=0.004)])

        boot_read = _read(spot_executor, store, logger)

        assert boot_read.fills[0].fill_class == BootFillClass.ACCOUNTED_FOR
        assert not boot_read.holds_entries()

    def test_an_order_answered_before_the_write_is_accounted_for(self, spot_executor, store, logger, tmp_path):
        written_at = _remember(store, tmp_path, open_orders=[
            OpenOrderCarryOver(client_order_id=_key(9),
                               submitted_at_utc=(datetime.now(timezone.utc) - timedelta(hours=1))
                               .isoformat())])
        # The read reaches back an hour for the open order; this one closed in that stretch and
        # was never open at the write, so the book written then holds it
        spot_executor.broker.adapter.set_closed_broker_orders(
            [_closed('OOLD', 8, written_at - timedelta(minutes=20))])

        assert _read(spot_executor, store, logger).fills[0].fill_class == BootFillClass.ACCOUNTED_FOR


class TestOrdersOpenAtTheWrite:

    def test_one_that_filled_just_before_the_write_unseen_holds(self, spot_executor, store, logger, tmp_path):
        sent = datetime.now(timezone.utc) - _WRITTEN_AGO - timedelta(minutes=5)
        written_at = _remember(store, tmp_path, open_orders=[
            OpenOrderCarryOver(client_order_id=_key(8), submitted_at_utc=sent.isoformat())])
        spot_executor.broker.adapter.set_closed_broker_orders(
            [_closed('OQX3', 8, written_at - timedelta(minutes=2))])

        boot_read = _read(spot_executor, store, logger)

        assert boot_read.read_from <= sent, 'the read reaches back to the oldest order open at the write'
        assert boot_read.fills[0].fill_class == BootFillClass.UNBOOKED_ENTRY


class TestUnclearEvidence:

    def test_a_close_the_book_does_not_know_holds(self, spot_executor, store, logger, tmp_path):
        written_at = _remember(store, tmp_path, notes=[_note()])
        spot_executor.broker.adapter.set_broker_balances({'XXBT': 0.0, 'ZUSD': 1245.0})
        spot_executor.broker.adapter.set_closed_broker_orders(
            [_closed('OSELL', 8, written_at + timedelta(seconds=30), direction=OrderDirection.SHORT,
                     lots=0.004)])

        assert _read(spot_executor, store, logger).fills[0].fill_class == BootFillClass.UNBOOKED_CLOSE

    def test_a_partial_fill_is_unclear(self, spot_executor, store, logger, tmp_path):
        written_at = _remember(store, tmp_path)
        spot_executor.broker.adapter.set_closed_broker_orders(
            [_closed('OPART', 8, written_at + timedelta(seconds=30), lots=0.002, filled=0.001)])

        assert _read(spot_executor, store, logger).fills[0].fill_class == BootFillClass.UNCLEAR

    def test_a_fill_inside_the_clock_skew_margin_is_unclear(self, spot_executor, store, logger, tmp_path):
        written_at = _remember(store, tmp_path)
        spot_executor.broker.adapter.set_closed_broker_orders(
            [_closed('OEDGE', 8, written_at + timedelta(seconds=1))])

        boot_read = _read(spot_executor, store, logger)

        assert boot_read.fills[0].fill_class == BootFillClass.UNCLEAR
        assert boot_read.holds_entries()

    def test_a_read_that_gave_up_holds(self, spot_executor, store, logger, tmp_path):
        _remember(store, tmp_path)
        spot_executor.broker.adapter.set_transport_fault('closedorders', 'venue unreachable',
                                                         terminal=True)

        boot_read = _read(spot_executor, store, logger)

        assert (boot_read.complete, boot_read.holds_entries()) == (False, True)
        assert 'gave up' in boot_read.incomplete_reason


class TestAnOrderStillInFlightAtTheStart:

    def test_a_market_order_of_ours_still_working_holds_the_entries(
            self, spot_executor, store, logger, tmp_path):
        # Killed after the submit, before the fill: the venue still works the order at the start
        _remember(store, tmp_path)
        spot_executor.broker.adapter.set_broker_orders([BrokerOrder(
            broker_ref='OQX3', symbol='BTCUSD', direction=OrderDirection.LONG,
            order_type=OrderType.MARKET, lots=0.001, status=BrokerOrderStatus.PENDING,
            client_order_id=_key(8))])

        boot_read = _read(spot_executor, store, logger)

        (in_flight,) = boot_read.get_holding_fills()
        assert (in_flight.fill_class, in_flight.broker_ref) == (BootFillClass.IN_FLIGHT, 'OQX3')
        assert boot_read.holds_entries(), 'its fill may come yet — opening now would buy it twice'
        assert 'still in flight' in boot_read.describe()

    def test_another_sessions_market_order_holds_nothing(self, spot_executor, store, logger, tmp_path):
        _remember(store, tmp_path)
        spot_executor.broker.adapter.set_broker_orders([BrokerOrder(
            broker_ref='OF1', symbol='BTCUSD', direction=OrderDirection.LONG,
            order_type=OrderType.MARKET, lots=0.001, status=BrokerOrderStatus.PENDING,
            client_order_id=_key(8, session_key='zz99'))])

        assert not _read(spot_executor, store, logger).holds_entries()


class TestWhereNothingIsRead:

    def test_a_first_start_reads_nothing(self, spot_executor, store, logger):
        adopter = _adopter(spot_executor, store, logger)
        assert adopter.run() is True
        assert adopter.get_boot_read() is None

    def test_a_dry_run_reads_nothing(self, spot_executor, store, logger, tmp_path):
        _remember(store, tmp_path)
        adopter = _adopter(spot_executor, store, logger, dry_run=True)
        assert adopter.run() is True
        assert adopter.get_boot_read() is None

    def test_a_margin_session_reads_nothing(self, executor, store, logger, tmp_path):
        _remember(store, tmp_path)
        adopter = _adopter(executor, store, logger)
        assert adopter.run() is True
        assert adopter.get_boot_read() is None
        assert any('not read at margin' in i for i in logger.infos)


class TestTheHold:

    def test_an_attended_yes_lifts_it(self, spot_executor, store, logger, tmp_path):
        written_at = _remember(store, tmp_path)
        spot_executor.broker.adapter.set_closed_broker_orders(
            [_closed('OQX3', 8, written_at + timedelta(seconds=30))])
        boot_read = _read(spot_executor, store, logger)

        held = hold_entries_over_unbooked_fills(boot_read, spot_executor, attended=True,
                                                operator_alerts=_Alerts(), logger=logger,
                                                ask=lambda question: 'y')

        assert held is False
        assert not spot_executor.get_entry_holds().is_held(EntryHoldReason.UNBOOKED_FILL)

    def test_an_attended_no_keeps_it(self, spot_executor, store, logger, tmp_path):
        written_at = _remember(store, tmp_path)
        spot_executor.broker.adapter.set_closed_broker_orders(
            [_closed('OQX3', 8, written_at + timedelta(seconds=30))])
        boot_read = _read(spot_executor, store, logger)

        assert hold_entries_over_unbooked_fills(boot_read, spot_executor, attended=True,
                                                operator_alerts=_Alerts(), logger=logger,
                                                ask=lambda question: 'n') is True

    def test_a_restart_finds_the_same_fill_after_the_held_session_wrote_again(
            self, store, logger, tmp_path):
        # The fill was open at the first write and closed just before it, unseen
        sent = datetime.now(timezone.utc) - _WRITTEN_AGO - timedelta(minutes=5)
        written_at = _remember(store, tmp_path, open_orders=[
            OpenOrderCarryOver(client_order_id=_key(8), submitted_at_utc=sent.isoformat())])
        fill = _closed('OQX3', 8, written_at - timedelta(minutes=2))
        first = MockOrderExecution(mode=MockExecutionMode.TIMEOUT, spot_mode=True,
                                   initial_balances={'USD': 1000.0, 'BTC': 0.0}).create_executor()
        first.broker.adapter.set_closed_broker_orders([fill])
        found = _read(first, store, logger)
        held = BootReadHoldCarryOver(
            written_at_utc=found.written_at.isoformat(), read_from_utc=found.read_from.isoformat(),
            client_order_ids=[f.client_order_id for f in found.get_holding_fills()])
        # The held session wrote its carry-over again: its own open orders — none — replace the list
        store.save(session_key='', highest_position_counter=0, open_positions=[],
                   open_orders_at_write=[], write_boot_read_hold=True, boot_read_hold=held)

        restarted = MockOrderExecution(mode=MockExecutionMode.TIMEOUT, spot_mode=True,
                                       initial_balances={'USD': 1000.0, 'BTC': 0.0}).create_executor()
        restarted.broker.adapter.set_closed_broker_orders([fill])
        boot_read = _read(restarted, store, logger)

        assert boot_read.written_at == written_at
        assert boot_read.fills[0].fill_class == BootFillClass.UNBOOKED_ENTRY, (
            'a restart must not step past the fill that holds the entries')


class TestTheCarryOver:

    def test_open_orders_and_the_held_start_are_read_back(self, store):
        sent = '2026-11-26T09:00:00+00:00'
        store.save(session_key=PREVIOUS_SESSION_KEY, highest_position_counter=8,
                   open_orders_at_write=[OpenOrderCarryOver(client_order_id='p8b3f_8',
                                                            submitted_at_utc=sent)],
                   write_boot_read_hold=True, boot_read_hold=_HOLD)

        payload = store.load()

        assert [(o.client_order_id, o.submitted_at_utc) for o in payload.open_orders_at_write] == [
            ('p8b3f_8', sent)]
        assert payload.boot_read_hold == _HOLD

    def test_a_write_that_does_not_state_the_hold_leaves_it(self, store):
        store.save(session_key=PREVIOUS_SESSION_KEY, highest_position_counter=8,
                   write_boot_read_hold=True, boot_read_hold=_HOLD)
        store.save(session_key='', highest_position_counter=0)

        assert store.load().boot_read_hold == _HOLD

    def test_a_lift_clears_it(self, store):
        store.save(session_key=PREVIOUS_SESSION_KEY, highest_position_counter=8,
                   write_boot_read_hold=True, boot_read_hold=_HOLD)
        store.save(session_key='', highest_position_counter=0, write_boot_read_hold=True,
                   boot_read_hold=None)

        assert store.load().boot_read_hold is None

    def test_a_document_from_before_the_boot_read_holds_neither(self, store, tmp_path):
        store.save(session_key=PREVIOUS_SESSION_KEY, highest_position_counter=8)
        for document in (tmp_path / 'cold_start_state').glob('*.json'):
            envelope = json.loads(document.read_text())
            del envelope['snapshot']['open_orders_at_write'], envelope['snapshot']['boot_read_hold']
            document.write_text(json.dumps(envelope))

        payload = store.load()

        assert (payload.open_orders_at_write, payload.boot_read_hold) == ([], None)

    def test_an_adopted_order_is_open_at_the_write_from_its_adoption(self, spot_executor):
        resting = BrokerOrder(broker_ref='ORST', symbol='BTCUSD', direction=OrderDirection.LONG,
                              order_type=OrderType.LIMIT, lots=0.001,
                              status=BrokerOrderStatus.PENDING, price=60000.0,
                              client_order_id=_key(5))
        before = datetime.now(timezone.utc)
        spot_executor.adopt_resting_orders([('pos_btcusd_5', resting)])

        (entry,) = spot_executor.get_open_orders_at_write()

        assert entry.client_order_id == _key(5)
        assert datetime.fromisoformat(entry.submitted_at_utc) >= before, (
            'its submit is unknown here; it was still open when this start adopted it')
