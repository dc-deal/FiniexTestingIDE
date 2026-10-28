"""
FiniexTestingIDE - Venue-Account Report (#362)

The venue's account per live session, derived from the broker-truth lines of its order-event
stream: what the venue held at the start and at the end — counted, with its balance sheet as it
came — and what the reconciliation recorded in between. Built over hand-made lines, because every
case here is a sequence of lines, and a session would have to be steered into each one.
"""

from typing import List, Optional

from python.framework.reporting.builders.run_unit import RunUnit
from python.framework.reporting.builders.venue_account_report_builder import (
    asked_its_venue,
    build_venue_account_report,
)
from python.framework.types.live_types.broker_truth_types import (
    BrokerTruthPart,
    BrokerTruthReadReason,
    BrokerTruthRecord,
    BrokerTruthSnapshot,
)
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus
from python.framework.types.live_types.reconciliation_types import (
    BrokerOrder,
    BrokerPosition,
    ReconcileDivergence,
    ReconcileState,
    ReconcileTotals,
)
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderType

_RUN_ID = '20261007_120000_ab12cd34'
_REF = 'OQ3V2K-ABCDE-FGHIJK'


def _order() -> BrokerOrder:
    """
    A resting limit the venue reports.

    Returns:
        The order
    """
    return BrokerOrder(
        broker_ref=_REF, symbol='BTCUSD', direction=OrderDirection.LONG,
        order_type=OrderType.LIMIT, lots=0.01, status=BrokerOrderStatus.PENDING, price=40000.0)


def _read(seq: int, reason: BrokerTruthReadReason,
          snapshot: Optional[BrokerTruthSnapshot] = None) -> BrokerTruthRecord:
    """
    A start or end line.

    Args:
        seq: Its place on the stream
        reason: SESSION_START or SESSION_END
        snapshot: What the venue answered; an empty account when omitted

    Returns:
        The line
    """
    return BrokerTruthRecord(
        seq=seq, read_reason=reason,
        snapshot=snapshot or BrokerTruthSnapshot(venue_orders=[], venue_balances={}))


def _reconcile(seq: int, divergence: Optional[ReconcileDivergence]) -> BrokerTruthRecord:
    """
    A reconcile line — divergent when it names a divergence, clean when it does not.

    Args:
        seq: Its place on the stream
        divergence: The picture by identity, None for a clean one

    Returns:
        The line
    """
    return BrokerTruthRecord(
        seq=seq, read_reason=BrokerTruthReadReason.RECONCILE,
        snapshot=BrokerTruthSnapshot(venue_orders=[]),
        reconcile_state=(ReconcileState.CLEAN if divergence is None
                         else ReconcileState.DIVERGENT),
        divergence=divergence)


def _session(lines: List[BrokerTruthRecord]) -> List[RunUnit]:
    """
    One live session's unit.

    Args:
        lines: Its broker-truth lines, in stream order

    Returns:
        The run's units
    """
    return [RunUnit(name='btc_session', symbol='BTCUSD', broker_truth=lines)]


class TestTheStartAndTheEnd:
    """Each read counted, its balance sheet as it came, a part it gave up on named."""

    def test_the_reads_are_counted_with_their_balances_and_their_lines(self):
        report = build_venue_account_report(_RUN_ID, _session([
            _read(1, BrokerTruthReadReason.SESSION_START, BrokerTruthSnapshot(
                venue_orders=[_order()], venue_balances={'ZUSD': 812.4, 'XETH': 0.0031})),
            _read(9, BrokerTruthReadReason.SESSION_END),
        ]))

        row = report.units[0]
        assert (row.name, report.run_id) == ('btc_session', _RUN_ID)
        assert (row.at_start.seq, row.at_start.venue_order_count) == (1, 1)
        assert row.at_start.venue_balances == {'ZUSD': 812.4, 'XETH': 0.0031}
        assert (row.at_end.seq, row.at_end.venue_order_count, row.at_end.venue_balances) == (
            9, 0, {}), 'an empty account is a count of nothing, not an absence'

    def test_a_part_the_read_gave_up_on_stays_null_and_named(self):
        report = build_venue_account_report(_RUN_ID, _session([
            _read(1, BrokerTruthReadReason.SESSION_END, BrokerTruthSnapshot(
                venue_orders=[], unread_parts=[BrokerTruthPart.VENUE_BALANCES])),
        ]))

        at_end = report.units[0].at_end
        assert at_end.venue_balances is None
        assert at_end.unread_parts == [BrokerTruthPart.VENUE_BALANCES]

    def test_positions_are_counted_on_a_margin_account_and_null_on_spot(self):
        margin = build_venue_account_report(_RUN_ID, _session([
            _read(1, BrokerTruthReadReason.SESSION_START, BrokerTruthSnapshot(
                venue_orders=[], venue_balances={'USD': 10000.0}, venue_positions=[
                    BrokerPosition(symbol='EURUSD', direction=OrderDirection.LONG, lots=0.1,
                                   entry_price=1.1)])),
        ]))
        spot = build_venue_account_report(_RUN_ID, _session([
            _read(1, BrokerTruthReadReason.SESSION_START)]))

        assert margin.units[0].at_start.venue_position_count == 1
        assert spot.units[0].at_start.venue_position_count is None

    def test_a_read_the_session_never_took_is_null(self):
        """Stopped before its start read, its end read still taken."""
        report = build_venue_account_report(_RUN_ID, _session([
            _read(1, BrokerTruthReadReason.SESSION_END)]))

        assert report.units[0].at_start is None and report.units[0].at_end.seq == 1


class TestTheReconciliationBetween:
    """How many lines a changed picture wrote, how many found the books apart, and the last."""

    def test_a_divergence_that_ended_says_so(self):
        report = build_venue_account_report(_RUN_ID, _session([
            _read(1, BrokerTruthReadReason.SESSION_START),
            _reconcile(5, ReconcileDivergence(ghost_orders=[_REF])),
            _reconcile(8, None),
            _read(12, BrokerTruthReadReason.SESSION_END),
        ]))

        row = report.units[0]
        assert (row.reconcile_lines, row.divergent_lines) == (2, 1)
        assert row.last_reconcile_state is ReconcileState.CLEAN
        assert row.last_divergence.ghost_orders == [_REF], 'the episode is named though it ended'

    def test_the_latest_divergent_picture_is_the_one_named(self):
        report = build_venue_account_report(_RUN_ID, _session([
            _reconcile(3, ReconcileDivergence(ghost_orders=[_REF])),
            _reconcile(7, ReconcileDivergence(orphan_orders=['pos_btcusd_2'])),
        ]))

        row = report.units[0]
        assert row.last_reconcile_state is ReconcileState.DIVERGENT
        assert (row.last_divergence.ghost_orders, row.last_divergence.orphan_orders) == (
            [], ['pos_btcusd_2'])

    def test_no_reconcile_line_leaves_the_last_state_null(self):
        report = build_venue_account_report(_RUN_ID, _session([
            _read(1, BrokerTruthReadReason.SESSION_START)]))

        row = report.units[0]
        assert (row.reconcile_lines, row.divergent_lines) == (0, 0)
        assert row.last_reconcile_state is None and row.last_divergence is None


class TestWhoGetsARow:
    def test_a_unit_that_asked_no_venue_has_none(self):
        """A backtest's scenario, a dry run against a real venue — nothing was asked."""
        report = build_venue_account_report(_RUN_ID, [
            RunUnit(name='scenario_1', symbol='BTCUSD'),
            RunUnit(name='btc_session', symbol='BTCUSD',
                    broker_truth=[_read(1, BrokerTruthReadReason.SESSION_START)]),
        ])

        assert [row.name for row in report.units] == ['btc_session']
        assert report.key == ['name']

    def test_a_session_that_reconciled_has_one_without_a_line(self):
        """#631: the totals never vanish with an empty list of lines — a session whose every cycle
        was skipped is exactly the one whose record has to say so."""
        skipped_only = RunUnit(
            name='btc_session', symbol='BTCUSD', reconcile_enabled=True,
            reconcile_totals=ReconcileTotals(cycles_compared=0, cycles_skipped=4,
                                             divergences_seen=0))
        never_due = RunUnit(
            name='btc_session', symbol='BTCUSD', reconcile_enabled=True,
            reconcile_totals=ReconcileTotals(cycles_compared=0, cycles_skipped=0,
                                             divergences_seen=0))

        assert asked_its_venue(skipped_only) is True
        assert asked_its_venue(never_due) is False
        row = build_venue_account_report(_RUN_ID, [skipped_only]).units[0]
        assert row.at_start is None and row.reconcile_lines == 0
        assert row.reconciliation.cycles_skipped == 4


class TestOverTheWholeSession:
    """#631: how often the comparison ran, which its lines cannot say."""

    def _row(self, enabled, totals):
        unit = RunUnit(name='btc_session', symbol='BTCUSD',
                       broker_truth=[_read(1, BrokerTruthReadReason.SESSION_START)],
                       reconcile_enabled=enabled, reconcile_totals=totals)
        return build_venue_account_report(_RUN_ID, [unit]).units[0]

    def test_the_totals_are_served_as_the_reconciler_kept_them(self):
        row = self._row(True, ReconcileTotals(cycles_compared=2877, cycles_skipped=3,
                                              divergences_seen=4))

        served = row.model_dump(mode='json')['reconciliation']
        assert served == {'enabled': True, 'cycles_compared': 2877, 'cycles_skipped': 3,
                          'divergences_seen': 4}

    def test_switched_off_the_counts_are_null(self):
        reconciliation = self._row(False, None).reconciliation

        assert reconciliation.enabled is False
        assert (reconciliation.cycles_compared, reconciliation.cycles_skipped,
                reconciliation.divergences_seen) == (None, None, None)

    def test_on_and_never_due_counts_zero(self):
        reconciliation = self._row(True, ReconcileTotals(
            cycles_compared=0, cycles_skipped=0, divergences_seen=0)).reconciliation

        assert reconciliation.enabled is True
        assert (reconciliation.cycles_compared, reconciliation.cycles_skipped) == (0, 0)

    def test_a_unit_that_does_not_say_whether_it_reconciled_has_none(self):
        """The builder does not guess: no answer is served as no answer."""
        assert self._row(None, None).reconciliation is None
