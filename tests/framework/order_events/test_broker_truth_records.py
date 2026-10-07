"""
FiniexTestingIDE - Broker Truth in the Order-Event Stream (#362)

A live session asks the venue what it holds — at its start, at its end, and when the
reconciliation picture changed — and writes the answer into the order-event stream beside its own
steps, on the same counter. A part the venue does not give is recorded as unread, never as an
empty answer, which would say the venue holds nothing. Only an executor with a venue asks one: a
backtest's venue is its own book.
"""

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import pytest

from python.framework.exceptions.connection_errors import ConnectionGaveUpError
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.live import live_trade_executor as live_module
from python.framework.types.api.report_types import BrokerTruthRow
from python.framework.types.live_types.broker_truth_types import (
    BrokerTruthPart,
    BrokerTruthReadReason,
    BrokerTruthRecord,
)
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus
from python.framework.types.live_types.reconciliation_types import (
    BrokerOrder,
    ReconcileDivergence,
    ReconcileState,
    ReconciliationResult,
)
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderType
from tests.framework.order_events.conftest import live_session, make_simulator, market_order

_START = BrokerTruthReadReason.SESSION_START
_END = BrokerTruthReadReason.SESSION_END
_REF = 'OQ3V2K-ABCDE-FGHIJK'


def _venue_order() -> BrokerOrder:
    """A resting limit the venue reports."""
    return BrokerOrder(
        broker_ref=_REF, symbol='BTCUSD', direction=OrderDirection.LONG,
        order_type=OrderType.LIMIT, lots=0.01, status=BrokerOrderStatus.PENDING, price=40000.0)


def _truths(executor) -> List[BrokerTruthRecord]:
    """Every broker-truth record the executor hands out from here on."""
    records: List[BrokerTruthRecord] = []
    executor.add_broker_truth_listener(records.append)
    return records


class TestTheDeclaration:
    """The names a reader meets, held to what serves and writes them — both directions."""

    def test_every_part_is_a_field_of_the_served_line_and_back(self):
        venue_fields = {name for name in BrokerTruthRow.model_fields if name.startswith('venue_')}
        assert {part.value for part in BrokerTruthPart} == venue_fields

    def test_every_read_reason_has_a_place_that_writes_it(self):
        """
        A member nothing writes would satisfy every other test without meaning anything. Read
        once, and only where a writer can sit — the session and the execution layer.
        """
        sources = [path.read_text(encoding='utf-8')
                   for layer in ('python/framework/autotrader', 'python/framework/trading_env')
                   for path in Path(layer).rglob('*.py')]
        unwritten = [reason.value for reason in BrokerTruthReadReason
                     if not any(f'BrokerTruthReadReason.{reason.name}' in source
                                for source in sources)]
        assert unwritten == [], f'nothing in the session or the executors writes {unwritten}'


class TestTheSessionRead:
    """At start and end the venue is asked for all of it, under the ladder."""

    def test_it_writes_what_the_venue_holds(self):
        _, executor, _ = live_session()
        truths = _truths(executor)
        executor.broker.adapter.set_broker_orders([_venue_order()])
        executor.broker.adapter.set_broker_balances({'USD': 812.4, 'BTC': 0.0031})

        record = executor.record_session_truth(_START)

        assert truths == [record]
        assert record.read_reason is _START
        assert [order.broker_ref for order in record.snapshot.venue_orders] == [_REF]
        assert record.snapshot.venue_balances == {'USD': 812.4, 'BTC': 0.0031}
        assert record.snapshot.unread_parts == []

    @pytest.mark.parametrize('spot_mode,positions', [(False, []), (True, None)],
                             ids=['margin', 'spot'])
    def test_positions_are_read_on_a_margin_account_only(self, spot_mode, positions):
        _, executor, _ = live_session(spot_mode=spot_mode)

        record = executor.record_session_truth(_END)

        assert record.snapshot.venue_positions == positions
        assert BrokerTruthPart.VENUE_POSITIONS not in record.snapshot.unread_parts, (
            'not read is not unread')

    def test_a_part_the_venue_does_not_give_is_unread_not_empty(self):
        _, executor, _ = live_session()
        executor.broker.adapter.set_transport_fault('balances', 'venue unreachable', terminal=True)

        record = executor.record_session_truth(_END)

        assert record.snapshot.venue_balances is None
        assert record.snapshot.unread_parts == [BrokerTruthPart.VENUE_BALANCES]
        assert record.snapshot.venue_orders == [], 'the other parts are read all the same'

    def test_a_ladder_that_aborts_ends_neither_the_read_nor_the_session(self, monkeypatch):
        """The configured rule at the venue is to abort; a record is an observation only."""
        _, executor, _ = live_session()
        balances = executor.broker.adapter.get_broker_balances
        real = live_module.run_with_ladder

        def gives_up_on_balances(operation, ladder, wait=None):
            if operation == balances:
                raise ConnectionGaveUpError('broker_rest gave up after 3 attempts')
            return real(operation, ladder, wait)
        monkeypatch.setattr(live_module, 'run_with_ladder', gives_up_on_balances)

        record = executor.record_session_truth(_END)

        assert record.snapshot.unread_parts == [BrokerTruthPart.VENUE_BALANCES]


class TestItsPlaceInTheStream:
    def test_it_shares_the_counter_with_the_order_events(self):
        mock, executor, events = live_session()
        truths = _truths(executor)

        executor.record_session_truth(_START)
        executor.open_order(market_order())
        mock.await_submit_confirmation(executor)
        executor.record_session_truth(_END)

        seqs = sorted([truth.seq for truth in truths] + [event.seq for event in events])
        assert seqs == list(range(1, len(seqs) + 1)), 'one counter, no gap, no repeat'
        assert truths[0].seq < events[0].seq and events[-1].seq < truths[-1].seq

    def test_before_the_first_tick_it_has_only_its_receipt_time(self):
        executor = MockOrderExecution().create_executor()   # no tick fed: no clock yet

        record = executor.record_session_truth(_START)

        assert record.event_time is None and record.ts_init is not None

    def test_a_backtest_asks_no_venue(self):
        simulator = make_simulator(spot_mode=False)

        assert simulator.record_session_truth(_START) is None


class TestAReconcileRecord:
    """Inside the tick loop: what the cycle read, plus the balances on a crossing only."""

    @staticmethod
    def _cycle(state_changed: bool) -> ReconciliationResult:
        """A divergent cycle the Reconciler marked due."""
        return ReconciliationResult(
            timestamp=datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc),
            is_clean=False,
            broker_orders=[_venue_order()],
            divergence=ReconcileDivergence(ghost_orders=[_REF]),
            broker_truth_due=True,
            broker_truth_state_changed=state_changed)

    def test_it_takes_the_cycles_orders_and_asks_no_second_time(self):
        _, executor, _ = live_session()
        executor.broker.adapter.set_transport_fault('openorders', 'asked twice', terminal=True)

        record = executor.record_reconcile_truth(self._cycle(state_changed=False))

        assert [order.broker_ref for order in record.snapshot.venue_orders] == [_REF]
        assert record.read_reason is BrokerTruthReadReason.RECONCILE
        assert record.reconcile_state is ReconcileState.DIVERGENT
        assert record.divergence.ghost_orders == [_REF]

    def test_balances_are_read_on_a_crossing_only(self):
        _, executor, _ = live_session()
        executor.broker.adapter.set_broker_balances({'USD': 812.4})

        same_side = executor.record_reconcile_truth(self._cycle(state_changed=False))
        crossing = executor.record_reconcile_truth(self._cycle(state_changed=True))

        assert same_side.snapshot.venue_balances is None
        assert same_side.snapshot.unread_parts == [], 'not read on this occasion — not lost'
        assert crossing.snapshot.venue_balances == {'USD': 812.4}

    def test_a_failed_balance_read_does_not_wait(self, monkeypatch):
        """The tick loop's cadence is its ladder: one attempt, then the record goes out."""
        _, executor, _ = live_session()
        executor.broker.adapter.set_transport_fault('balances', 'venue busy')   # transient
        monkeypatch.setattr(time, 'sleep', lambda seconds: pytest.fail('the tick loop waited'))

        record = executor.record_reconcile_truth(self._cycle(state_changed=True))

        assert record.snapshot.venue_balances is None
        assert record.snapshot.unread_parts == [BrokerTruthPart.VENUE_BALANCES]
