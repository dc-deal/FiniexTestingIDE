"""
Reconciler — the Client Order ID as a Join Key (#355 Phase 1)

#473 sends our own key on every live order and parses it back out of the venue's open
orders. This suite covers the half that reads it: a resting broker order carrying OUR key
is no longer an anonymous ghost, and what it IS depends on which session minted the key.

    our key + local pending without broker_ref  → attributed  (the lost submit answer)
    our key, this session, no local pending     → abandoned   (placed and forgotten)
    our key shape, another session              → foreign_session (adoption is Phase 2)
    no key at all                               → ghost       (not ours, as far as we know)

The fourth bucket is local rather than broker-side: a pending whose submit was never
answered and which the venue does not show either. Nothing times it out (the resting-order
list has no timeout), so it keeps has_pending_orders() true — which is why it is reported
into the session error pot exactly once.

Offline: broker truth is a seeded MockBrokerAdapter, local state is built by hand.
"""

from typing import List

from python.framework.types.trading_env_types.latency_simulator_types import PendingOperation
from python.framework.utils.run_id_utils import build_client_order_id

from tests.autotrader.reconciliation.conftest import make_broker_order, make_pending

_OURS = '1641'          # the FakeExecutor's session (conftest default)
_EARLIER = '8b3f'       # a previous session of the same bot


def _ckey(order_id: str, session_key: str = _OURS) -> str:
    """The wire key a session would have sent for this internal order id."""
    return build_client_order_id(session_key, order_id)


class TestAttribution:
    """Our key + a pending still waiting for its reference = a repair, not a divergence."""

    def test_lost_answer_is_attributed(self, mock_adapter, make_reconciler):
        pending = make_pending(
            'pos_ethusd_47',
            broker_ref=None,
            in_flight_operation=PendingOperation.PENDING_SUBMIT,
        )
        broker_order = make_broker_order(
            'OQ7X2A-RESTING', client_order_id=_ckey('pos_ethusd_47'))
        mock_adapter.set_broker_orders([broker_order])
        reconciler = make_reconciler(mock_adapter, active_orders=[pending])

        result = reconciler.reconcile(current_tick=1)

        assert len(result.attributed_orders) == 1
        local, broker = result.attributed_orders[0]
        assert local.pending_order_id == 'pos_ethusd_47'
        assert broker.broker_ref == 'OQ7X2A-RESTING'
        assert result.ghost_orders == []
        assert result.abandoned_orders == []
        assert result.unconfirmed_orders == []

    def test_attribution_alone_keeps_the_cycle_clean(self, mock_adapter, make_reconciler):
        pending = make_pending(
            'pos_ethusd_47',
            broker_ref=None,
            in_flight_operation=PendingOperation.PENDING_SUBMIT,
        )
        mock_adapter.set_broker_orders([
            make_broker_order('OQ7X2A-RESTING', client_order_id=_ckey('pos_ethusd_47')),
        ])
        reconciler = make_reconciler(mock_adapter, active_orders=[pending])

        result = reconciler.reconcile(current_tick=1)

        # A repair is not damage: the panel must not read "divergent" because a
        # reference was reclaimed.
        assert result.is_clean is True
        assert reconciler.get_display_counters()['reconcile_attributed'] == 1

    def test_reconciler_itself_writes_nothing(self, mock_adapter, make_reconciler):
        # ALERT_ONLY is the Reconciler's contract; the executor performs the write.
        pending = make_pending(
            'pos_ethusd_47',
            broker_ref=None,
            in_flight_operation=PendingOperation.PENDING_SUBMIT,
        )
        mock_adapter.set_broker_orders([
            make_broker_order('OQ7X2A-RESTING', client_order_id=_ckey('pos_ethusd_47')),
        ])
        reconciler = make_reconciler(mock_adapter, active_orders=[pending])

        reconciler.reconcile(current_tick=1)

        assert pending.broker_ref is None
        assert pending.execution_state.in_flight_operation is PendingOperation.PENDING_SUBMIT

    def test_a_confirmed_pending_is_matched_by_ref_only_once(self, mock_adapter, make_reconciler):
        # A pending WITH a reference must not also be indexed by client key — otherwise
        # one order could fill two buckets in the same cycle.
        pending = make_pending('pos_ethusd_47', broker_ref='OQ7X2A-RESTING')
        mock_adapter.set_broker_orders([
            make_broker_order('OQ7X2A-RESTING', client_order_id=_ckey('pos_ethusd_47')),
        ])
        reconciler = make_reconciler(mock_adapter, active_orders=[pending])

        result = reconciler.reconcile(current_tick=1)

        assert result.attributed_orders == []
        assert result.ghost_orders == []
        assert result.orphan_orders == []
        assert result.is_clean is True


class TestUnclaimedClassification:
    """An order neither join found is sorted by the key it carries, not by its absence."""

    def test_our_key_without_a_local_pending_is_abandoned(self, mock_adapter, make_reconciler):
        mock_adapter.set_broker_orders([
            make_broker_order('OQ7X2A-RESTING', client_order_id=_ckey('pos_ethusd_51')),
        ])
        reconciler = make_reconciler(mock_adapter, active_orders=[])

        result = reconciler.reconcile(current_tick=1)

        assert len(result.abandoned_orders) == 1
        assert result.abandoned_orders[0].broker_ref == 'OQ7X2A-RESTING'
        assert result.ghost_orders == []
        assert result.foreign_session_orders == []
        assert result.is_clean is False
        counters = reconciler.get_display_counters()
        assert counters['reconcile_divergences'] == 1
        assert counters['reconcile_unaccounted'] == 1

    def test_another_sessions_key_is_foreign_not_ghost(self, mock_adapter, make_reconciler):
        mock_adapter.set_broker_orders([
            make_broker_order(
                'OQ7X2A-OLD', client_order_id=_ckey('pos_ethusd_12', _EARLIER)),
        ])
        reconciler = make_reconciler(mock_adapter, active_orders=[])

        result = reconciler.reconcile(current_tick=1)

        assert len(result.foreign_session_orders) == 1
        assert result.foreign_session_orders[0].broker_ref == 'OQ7X2A-OLD'
        assert result.abandoned_orders == []
        assert result.ghost_orders == []
        assert result.is_clean is False
        assert reconciler.get_display_counters()['reconcile_unaccounted'] == 1

    def test_no_key_at_all_stays_a_ghost(self, mock_adapter, make_reconciler):
        # Backwards behaviour: an order placed by hand or by another client is exactly
        # what ghost_orders meant before this change, and still does.
        mock_adapter.set_broker_orders([make_broker_order('OQ7X2A-MANUAL')])
        reconciler = make_reconciler(mock_adapter, active_orders=[])

        result = reconciler.reconcile(current_tick=1)

        assert len(result.ghost_orders) == 1
        assert result.abandoned_orders == []
        assert result.foreign_session_orders == []

    def test_a_foreign_clients_key_is_not_read_as_ours(self, mock_adapter, make_reconciler):
        # Client order ids are free-format at the venue. Anything that is not our exact
        # shape must fall through to ghost rather than be claimed.
        for foreign in ('bot-7-entry', 'p164_47', 'p1641_x', 'p16411_47'):
            mock_adapter.set_broker_orders([
                make_broker_order('OQ7X2A-OTHER', client_order_id=foreign),
            ])
            reconciler = make_reconciler(mock_adapter, active_orders=[])

            result = reconciler.reconcile(current_tick=1)

            assert len(result.ghost_orders) == 1, f'{foreign} was claimed as a key of ours'
            assert result.abandoned_orders == []
            assert result.foreign_session_orders == []


class TestTrackedElsewhere:
    """An order the latency queue is still waiting on is ours AND accounted for."""

    def test_an_in_flight_market_order_is_not_abandoned(self, mock_adapter, make_reconciler):
        # MARKET and CLOSE orders live in the request processor, not in the resting-order
        # list the diff compares against. Catch one in the window where the venue already
        # lists it and the naive reading is "we placed it and stopped tracking it" — a red
        # panel marker for an order that fills a second later.
        mock_adapter.set_broker_orders([
            make_broker_order('OQ7X2A-INFLIGHT', client_order_id=_ckey('pos_ethusd_47')),
        ])
        reconciler = make_reconciler(
            mock_adapter, active_orders=[], in_flight_order_ids=['pos_ethusd_47'])

        result = reconciler.reconcile(current_tick=1)

        assert result.abandoned_orders == []
        assert result.ghost_orders == []
        assert result.foreign_session_orders == []
        assert result.is_clean is True

    def test_an_order_nobody_tracks_is_still_abandoned(self, mock_adapter, make_reconciler):
        # The guard must not swallow the real case it sits next to.
        mock_adapter.set_broker_orders([
            make_broker_order('OQ7X2A-RESTING', client_order_id=_ckey('pos_ethusd_51')),
        ])
        reconciler = make_reconciler(
            mock_adapter, active_orders=[], in_flight_order_ids=['pos_ethusd_47'])

        result = reconciler.reconcile(current_tick=1)

        assert len(result.abandoned_orders) == 1
        assert result.is_clean is False


class TestBrokerOrderWithoutAReference:
    """A keyed order with no venue reference must never be attributed."""

    def test_it_is_not_paired(self, mock_adapter, make_reconciler):
        # Attributing it would set broker_ref='' — the pending is then skipped by the poller
        # (no ref) AND no longer PENDING_SUBMIT, so the error-pot report that grades the
        # session never fires and the run ends green with a blocked algo. Kraken cannot
        # produce this shape today; the second adapter (#209) is why it is a guard.
        pending = make_pending(
            'pos_ethusd_47',
            broker_ref=None,
            in_flight_operation=PendingOperation.PENDING_SUBMIT,
        )
        mock_adapter.set_broker_orders([
            make_broker_order('', client_order_id=_ckey('pos_ethusd_47')),
        ])
        reconciler = make_reconciler(mock_adapter, active_orders=[pending])

        result = reconciler.reconcile(current_tick=1)

        assert result.attributed_orders == []
        # It stays unconfirmed — which is the honest answer: we still do not know.
        assert len(result.unconfirmed_orders) == 1


class TestUnconfirmedPendings:
    """The local half: submitted, never answered, and not at the broker either."""

    def test_unanswered_submit_absent_at_broker_is_reported_once(
        self, mock_adapter, make_reconciler, logger
    ):
        pending = make_pending(
            'pos_ethusd_51',
            broker_ref=None,
            in_flight_operation=PendingOperation.PENDING_SUBMIT,
        )
        mock_adapter.set_broker_orders([])
        reconciler = make_reconciler(mock_adapter, active_orders=[pending])

        # Spy on the error channel: what reaches the pot is what the operator sees, and
        # the property under test is the COUNT — once per order, not once per cycle.
        logged: List[str] = []
        logger.error = logged.append

        first = reconciler.reconcile(current_tick=1)
        second = reconciler.reconcile(current_tick=2)

        # The bucket persists — the order really is unaccounted for on both cycles ...
        assert len(first.unconfirmed_orders) == 1
        assert len(second.unconfirmed_orders) == 1
        assert first.is_clean is False
        # ... but the pot hears about it exactly once.
        errors = [line for line in logged if 'pos_ethusd_51' in line]
        assert len(errors) == 1, f'expected one pot error, got {len(errors)}'
        assert '#487' in errors[0]

    def test_each_order_is_reported_on_its_own(self, mock_adapter, make_reconciler, logger):
        # The edge trigger is per ORDER, not per session: with only one order in play a
        # single global flag would pass the test above while swallowing every later order.
        first = make_pending(
            'pos_ethusd_51',
            broker_ref=None,
            in_flight_operation=PendingOperation.PENDING_SUBMIT,
        )
        second = make_pending(
            'pos_ethusd_52',
            broker_ref=None,
            in_flight_operation=PendingOperation.PENDING_SUBMIT,
        )
        mock_adapter.set_broker_orders([])
        reconciler = make_reconciler(mock_adapter, active_orders=[first, second])

        logged: List[str] = []
        logger.error = logged.append

        reconciler.reconcile(current_tick=1)
        reconciler.reconcile(current_tick=2)

        # One error EACH across the two cycles. A single global "already reported" flag
        # would report the first order and swallow the second — the assertion that
        # separates the two implementations.
        assert len([line for line in logged if 'pos_ethusd_51' in line]) == 1
        assert len([line for line in logged if 'pos_ethusd_52' in line]) == 1

    def test_a_repaired_order_stops_counting_as_unaccounted(
        self, mock_adapter, make_reconciler
    ):
        # Reported unconfirmed on one cycle, attributed on the next: the final summary must
        # not keep claiming it, or a session that healed itself reads as one that did not.
        pending = make_pending(
            'pos_ethusd_47',
            broker_ref=None,
            in_flight_operation=PendingOperation.PENDING_SUBMIT,
        )
        mock_adapter.set_broker_orders([])
        reconciler = make_reconciler(mock_adapter, active_orders=[pending])
        reconciler.reconcile(current_tick=1)

        mock_adapter.set_broker_orders([
            make_broker_order('OQ7X2A-RESTING', client_order_id=_ckey('pos_ethusd_47')),
        ])
        result = reconciler.reconcile(current_tick=2)

        assert len(result.attributed_orders) == 1
        assert reconciler.get_display_counters()['reconcile_attributed'] == 1
        assert reconciler.get_display_counters()['reconcile_unaccounted'] == 0

    def test_a_normal_submit_roundtrip_is_not_reported(self, mock_adapter, make_reconciler):
        # broker_ref=None with NO in-flight submit marker is the ordinary window between
        # sending an order and hearing back. It was never a divergence and must not become
        # one — that grace is what _is_reconcilable_ref exists for.
        pending = make_pending('pos_ethusd_52', broker_ref=None)
        mock_adapter.set_broker_orders([])
        reconciler = make_reconciler(mock_adapter, active_orders=[pending])

        result = reconciler.reconcile(current_tick=1)

        assert result.unconfirmed_orders == []
        assert result.orphan_orders == []
        assert result.is_clean is True

    def test_dry_run_orders_stay_excluded(self, mock_adapter, make_reconciler):
        pending = make_pending(
            'pos_ethusd_53',
            broker_ref='DRYRUN-abc',
            in_flight_operation=PendingOperation.PENDING_SUBMIT,
        )
        mock_adapter.set_broker_orders([])
        reconciler = make_reconciler(mock_adapter, active_orders=[pending])

        result = reconciler.reconcile(current_tick=1)

        assert result.unconfirmed_orders == []
        assert result.orphan_orders == []
        assert result.is_clean is True


class TestDurableStatesDoNotFlood:
    """
    A divergence that has not CHANGED says nothing new.

    Most of these states are durable — a resting order stays resting until somebody cancels
    it — so a per-cycle warning would put tens of thousands of identical lines into the
    session pot over a thirty-day run. The warning fires on change; an unchanged cycle says
    so once per cycle at INFO, which keeps the poll visibly alive without burying it.
    """

    def test_the_warning_fires_once_and_the_repeat_is_info(
        self, mock_adapter, make_reconciler, logger
    ):
        mock_adapter.set_broker_orders([
            make_broker_order('OQ7X2A-RESTING', client_order_id=_ckey('pos_ethusd_51')),
        ])
        reconciler = make_reconciler(mock_adapter, active_orders=[])

        warnings: List[str] = []
        infos: List[str] = []
        logger.warning = warnings.append
        logger.info = infos.append

        for tick in range(1, 6):
            reconciler.reconcile(current_tick=tick)

        assert len([w for w in warnings if '[RECONCILE]' in w]) == 1
        assert len([i for i in infos if 'UNCHANGED' in i]) == 4
        # And the order is NAMED once, so the operator can act on it.
        assert len([w for w in warnings if 'placed and forgotten' in w]) == 1

    def test_a_changed_picture_reports_again(self, mock_adapter, make_reconciler, logger):
        # One abandoned order replaced by a DIFFERENT one is a change, even at the same count.
        mock_adapter.set_broker_orders([
            make_broker_order('OQ7X2A-FIRST', client_order_id=_ckey('pos_ethusd_51')),
        ])
        reconciler = make_reconciler(mock_adapter, active_orders=[])

        warnings: List[str] = []
        logger.warning = warnings.append

        reconciler.reconcile(current_tick=1)
        mock_adapter.set_broker_orders([
            make_broker_order('OQ7X2A-SECOND', client_order_id=_ckey('pos_ethusd_52')),
        ])
        reconciler.reconcile(current_tick=2)

        assert len([w for w in warnings if '[RECONCILE]' in w]) == 2


class TestNoSessionKey:
    """A session that stamps no key can claim nothing — and must not pretend otherwise."""

    def test_keyed_broker_order_is_never_claimed_without_a_session_key(
        self, mock_adapter, make_reconciler
    ):
        mock_adapter.set_broker_orders([
            make_broker_order('OQ7X2A-RESTING', client_order_id=_ckey('pos_ethusd_47')),
        ])
        reconciler = make_reconciler(mock_adapter, active_orders=[], session_key='')

        result = reconciler.reconcile(current_tick=1)

        assert result.attributed_orders == []
        assert result.abandoned_orders == []
        assert len(result.foreign_session_orders) == 1
