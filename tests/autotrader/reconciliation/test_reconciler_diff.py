"""
Reconciler order diff on SPOT — ghost / orphan / stale + in-flight grace (#151).

Orders are world-agnostic (resting LIMITs vs broker OpenOrders). Local orders
without a settled broker_ref (None / DRYRUN-*) are excluded from the diff so the
async submit roundtrip and dry-run never read as false orphans.
"""

from python.framework.testing.mock_broker_adapter import MockDivergenceMode
from tests.autotrader.reconciliation.conftest import make_pending, make_broker_order


def test_clean_when_orders_match(mock_adapter, make_reconciler):
    mock_adapter.set_broker_orders([make_broker_order('O1')])
    rec = make_reconciler(mock_adapter, active_orders=[make_pending('o1', 'O1')])
    result = rec.reconcile()
    assert result.is_clean
    assert result.ghost_orders == []
    assert result.orphan_orders == []


def test_ghost_order_broker_has_extra(mock_adapter, make_reconciler):
    mock_adapter.set_broker_orders([make_broker_order('O1'), make_broker_order('O2')])
    rec = make_reconciler(mock_adapter, active_orders=[make_pending('o1', 'O1')])
    result = rec.reconcile()
    assert not result.is_clean
    assert [o.broker_ref for o in result.ghost_orders] == ['O2']
    assert result.orphan_orders == []


def test_orphan_order_local_has_extra(mock_adapter, make_reconciler):
    mock_adapter.set_broker_orders([make_broker_order('O1')])
    rec = make_reconciler(
        mock_adapter,
        active_orders=[make_pending('o1', 'O1'), make_pending('o2', 'O2')],
    )
    result = rec.reconcile()
    assert [p.broker_ref for p in result.orphan_orders] == ['O2']
    assert result.ghost_orders == []


def test_drop_orders_makes_all_orphans(mock_adapter, make_reconciler):
    mock_adapter.set_broker_orders([make_broker_order('O1')])
    mock_adapter.set_divergence_mode(MockDivergenceMode.DROP_ORDERS)
    rec = make_reconciler(mock_adapter, active_orders=[make_pending('o1', 'O1')])
    result = rec.reconcile()
    assert len(result.orphan_orders) == 1


def test_stale_order_price_mismatch(mock_adapter, make_reconciler):
    mock_adapter.set_broker_orders([make_broker_order('O1', price=2100.0)])
    rec = make_reconciler(mock_adapter, active_orders=[make_pending('o1', 'O1', limit_price=2000.0)])
    result = rec.reconcile()
    assert len(result.stale_orders) == 1
    local, broker = result.stale_orders[0]
    assert broker.price == 2100.0


def test_inflight_order_without_broker_ref_skipped(mock_adapter, make_reconciler):
    # Local order mid-roundtrip (broker_ref=None) must NOT read as an orphan.
    mock_adapter.set_broker_orders([])
    rec = make_reconciler(mock_adapter, active_orders=[make_pending('o1', None)])
    result = rec.reconcile()
    assert result.is_clean
    assert result.orphan_orders == []


def test_dryrun_order_skipped(mock_adapter, make_reconciler):
    mock_adapter.set_broker_orders([])
    rec = make_reconciler(mock_adapter, active_orders=[make_pending('o1', 'DRYRUN-000001')])
    result = rec.reconcile()
    assert result.is_clean


def test_partial_fill_bucket_populated(mock_adapter, make_reconciler):
    mock_adapter.set_broker_orders([make_broker_order('O1')])
    rec = make_reconciler(
        mock_adapter,
        active_orders=[make_pending('o1', 'O1', lots=0.01, cumulative_filled_lots=0.004)],
    )
    result = rec.reconcile()
    assert [p.pending_order_id for p in result.partial_fills] == ['o1']
    # a partial fill is observed but is NOT a divergence — is_clean stays True
    assert result.is_clean
