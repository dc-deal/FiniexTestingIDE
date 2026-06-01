"""
Reconciler cadence (is_due hybrid) + ALERT_ONLY non-mutation + counters (#151).
"""

import pytest

from python.framework.types.config_types.autotrader_defaults_config_types import ReconciliationDefaults
from tests.autotrader.reconciliation.conftest import make_pending, make_broker_order


def test_is_due_by_ticks(mock_adapter, make_reconciler):
    cfg = ReconciliationDefaults(enabled=True, interval_ticks=10, min_interval_seconds=9999.0)
    rec = make_reconciler(mock_adapter, config=cfg)
    assert not rec.is_due(5)
    assert rec.is_due(10)


def test_is_due_by_seconds(mock_adapter, make_reconciler):
    # min_interval_seconds=0 → always due by wall-clock, regardless of ticks
    cfg = ReconciliationDefaults(enabled=True, interval_ticks=99999, min_interval_seconds=0.0)
    rec = make_reconciler(mock_adapter, config=cfg)
    assert rec.is_due(1)


def test_reconcile_resets_tick_window(mock_adapter, make_reconciler):
    cfg = ReconciliationDefaults(enabled=True, interval_ticks=10, min_interval_seconds=9999.0)
    rec = make_reconciler(mock_adapter, config=cfg)
    rec.reconcile(current_tick=10)
    assert not rec.is_due(15)
    assert rec.is_due(20)


def test_alert_only_does_not_mutate_local(mock_adapter, make_reconciler):
    mock_adapter.set_broker_orders([])
    pendings = [make_pending('o1', 'O1')]
    rec = make_reconciler(mock_adapter, active_orders=pendings)
    result = rec.reconcile()
    assert len(result.orphan_orders) == 1
    # local state untouched — ALERT_ONLY
    assert len(pendings) == 1
    assert pendings[0].broker_ref == 'O1'


def test_divergence_counters_current_and_cumulative(mock_adapter, make_reconciler):
    mock_adapter.set_broker_orders([])
    rec = make_reconciler(mock_adapter, active_orders=[make_pending('o1', 'O1')])
    rec.reconcile()
    rec.reconcile()
    counters = rec.get_display_counters()
    assert counters['reconcile_enabled'] is True
    assert counters['reconcile_divergences'] == 1        # CURRENT cycle (one persistent orphan)
    assert counters['reconcile_total_divergences'] == 2  # cumulative across both cycles
    assert counters['reconcile_clean'] is False


def test_recovery_resets_current_count_and_clean_flag(mock_adapter, make_reconciler):
    # Regression (found in live test): a resolved divergence must return the
    # panel to clean — the current count resets, only the session total persists.
    mock_adapter.set_broker_orders([])
    rec = make_reconciler(mock_adapter, active_orders=[make_pending('o1', 'O1')])
    rec.reconcile()  # orphan → current=1, not clean
    assert rec.get_display_counters()['reconcile_divergences'] == 1
    assert rec.get_display_counters()['reconcile_clean'] is False

    # resolve: broker now reports the order → matched → clean
    mock_adapter.set_broker_orders([make_broker_order('O1')])
    rec.reconcile()
    counters = rec.get_display_counters()
    assert counters['reconcile_divergences'] == 0          # current resets → panel ● ok
    assert counters['reconcile_clean'] is True
    assert counters['reconcile_total_divergences'] == 1    # cumulative stays


def test_clean_cycle_sets_clean_flag(mock_adapter, make_reconciler):
    mock_adapter.set_broker_orders([make_broker_order('O1')])
    rec = make_reconciler(mock_adapter, active_orders=[make_pending('o1', 'O1')])
    rec.reconcile()
    counters = rec.get_display_counters()
    assert counters['reconcile_divergences'] == 0
    assert counters['reconcile_clean'] is True


def test_non_alert_only_mode_rejected(mock_adapter, make_reconciler):
    cfg = ReconciliationDefaults(enabled=True, mode='auto_correct')
    with pytest.raises(NotImplementedError):
        make_reconciler(mock_adapter, config=cfg)
