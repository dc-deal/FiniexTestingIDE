"""
FiniexTestingIDE - When a Reconcile Cycle Writes Broker Truth (#362)

The order-event stream gets a broker-truth record when the reconciliation picture CHANGES — a
crossing between clean and divergent, or a divergence with other members — and never more often
than the configured minimum distance. A change inside that distance is deferred, not dropped, so
the stream ends with the latest picture. A clean stretch forgets the last divergence: one that
returns is reported and recorded again.
"""

from types import SimpleNamespace

import pytest

from python.framework.testing.mock_broker_adapter import MockBrokerAdapter
from python.framework.trading_env.live import reconciler as reconciler_module
from python.framework.trading_env.live.reconciler import Reconciler
from python.framework.types.config_types.autotrader_defaults_config_types import (
    ReconciliationDefaults,
)
from python.framework.types.config_types.market_config_types import TradingModel
from tests.autotrader.cold_start.conftest import RecordingLogger
from tests.autotrader.reconciliation.conftest import (
    FakeExecutor,
    make_broker_order,
    make_broker_position,
    make_pending,
)


class _Clock:
    """The reconciler's monotonic clock, moved by hand."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    """Replace the reconciler module's clock — and only that module's."""
    fake = _Clock()
    monkeypatch.setattr(reconciler_module, 'time', SimpleNamespace(monotonic=fake.monotonic))
    return fake


def _reconciler(adapter: MockBrokerAdapter, active_orders=(), interval: float = 300.0,
                trading_model: TradingModel = TradingModel.SPOT, positions=(),
                logger=None) -> Reconciler:
    """A Reconciler over seeded local state, with the record distance under test."""
    return Reconciler(
        executor=FakeExecutor(adapter, list(active_orders), list(positions)),
        config=ReconciliationDefaults(enabled=True, broker_truth_min_interval_seconds=interval),
        logger=logger or RecordingLogger(),
        trading_model=trading_model,
        symbol='ETHUSD',
    )


class TestWhatIsAChange:
    def test_a_clean_session_writes_no_record(self, clock):
        adapter = MockBrokerAdapter()
        adapter.set_broker_orders([make_broker_order('O1')])
        reconciler = _reconciler(adapter, [make_pending('o1', 'O1')])

        assert not reconciler.reconcile().broker_truth_due, 'the session started clean'

    def test_a_crossing_to_divergent_is_due_and_reads_balances(self, clock):
        adapter = MockBrokerAdapter()
        adapter.set_broker_orders([])
        reconciler = _reconciler(adapter, [make_pending('o1', 'O1')])

        result = reconciler.reconcile()

        assert result.broker_truth_due and result.broker_truth_state_changed
        assert result.divergence.orphan_orders == ['o1']

    def test_the_same_picture_again_is_not_due(self, clock):
        adapter = MockBrokerAdapter()
        adapter.set_broker_orders([])
        reconciler = _reconciler(adapter, [make_pending('o1', 'O1')], interval=0.0)
        reconciler.reconcile()

        assert not reconciler.reconcile().broker_truth_due

    def test_other_members_are_due_without_a_balance_read(self, clock):
        adapter = MockBrokerAdapter()
        adapter.set_broker_orders([make_broker_order('G1')])
        reconciler = _reconciler(adapter, interval=0.0)
        reconciler.reconcile()

        adapter.set_broker_orders([make_broker_order('G1'), make_broker_order('G2')])
        result = reconciler.reconcile()

        assert result.broker_truth_due and not result.broker_truth_state_changed
        assert result.divergence.ghost_orders == ['G1', 'G2']


class TestTheMinimumDistance:
    def test_a_change_inside_it_is_deferred_not_dropped(self, clock):
        adapter = MockBrokerAdapter()
        adapter.set_broker_orders([])
        reconciler = _reconciler(adapter, [make_pending('o1', 'O1')])
        assert reconciler.reconcile().broker_truth_due              # 0 s: divergent, written

        adapter.set_broker_orders([make_broker_order('O1')])
        clock.now += 60
        assert not reconciler.reconcile().broker_truth_due          # 60 s: clean, too soon

        clock.now += 241
        result = reconciler.reconcile()                             # 301 s: still clean
        assert result.broker_truth_due and result.broker_truth_state_changed
        assert result.divergence is None

    def test_a_picture_that_flips_back_inside_it_writes_nothing(self, clock):
        """An order at the edge of a tolerance: the stream keeps the picture it last wrote."""
        adapter = MockBrokerAdapter()
        adapter.set_broker_orders([])
        reconciler = _reconciler(adapter, [make_pending('o1', 'O1')])
        reconciler.reconcile()                                      # divergent, written

        adapter.set_broker_orders([make_broker_order('O1')])
        clock.now += 60
        reconciler.reconcile()                                      # clean, deferred
        adapter.set_broker_orders([])
        clock.now += 60
        assert not reconciler.reconcile().broker_truth_due          # divergent again: as written
        clock.now += 600
        assert not reconciler.reconcile().broker_truth_due, 'nothing differs from the record'


class TestADivergenceThatReturns:
    def test_it_is_reported_and_recorded_again(self, clock):
        logger = RecordingLogger()
        adapter = MockBrokerAdapter()
        adapter.set_broker_orders([])
        reconciler = _reconciler(adapter, [make_pending('o1', 'O1')], interval=0.0,
                                 logger=logger)
        first = reconciler.reconcile()                              # o1 missing at the venue
        adapter.set_broker_orders([make_broker_order('O1')])
        clean = reconciler.reconcile()                              # back
        adapter.set_broker_orders([])
        again = reconciler.reconcile()                              # missing once more

        assert [first.broker_truth_due, clean.broker_truth_due, again.broker_truth_due] == [
            True, True, True]
        warnings = [w for w in logger.warnings if '[RECONCILE]' in w]
        assert len(warnings) == 2, 'a clean stretch in between is news, not "unchanged"'


class TestWhatTheCycleCarries:
    def test_a_skipped_cycle_marks_nothing(self, clock):
        adapter = MockBrokerAdapter()
        adapter.set_transport_fault('openorders', 'venue busy')    # transient
        reconciler = _reconciler(adapter, [make_pending('o1', 'O1')])

        result = reconciler.reconcile()

        assert result.skipped_reason is not None and not result.broker_truth_due

    def test_positions_travel_on_a_margin_account_only(self, clock):
        adapter = MockBrokerAdapter()
        adapter.set_broker_orders([])
        adapter.set_broker_positions([make_broker_position('P1')])

        spot = _reconciler(adapter).reconcile()
        margin = _reconciler(adapter, trading_model=TradingModel.MARGIN).reconcile()

        assert spot.broker_positions is None, 'spot: not read'
        assert [p.broker_ref for p in margin.broker_positions] == ['P1']
        assert spot.broker_orders == [] and margin.broker_orders == []
