"""
Pending-Orders Report Builder Tests (#391).

Maps each run unit's PendingOrderStats (lifecycle + latency + active orders) to the
PendingOrdersReport. Tested with real PendingOrderStats / ActiveOrderSnapshot fixtures
wrapped in RunUnits — units with no pending activity are skipped (mirrors the console).
"""

from python.framework.reporting.run_reports.pending_orders_report_builder import build_pending_orders_report
from python.framework.reporting.run_reports.run_unit import RunUnit
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderType
from python.framework.types.trading_env_types.pending_order_stats_types import (
    ActiveOrderSnapshot, PendingOrderStats)


def _active(order_id: str = 'L1', order_type: OrderType = OrderType.LIMIT) -> ActiveOrderSnapshot:
    return ActiveOrderSnapshot(
        order_id=order_id, order_type=order_type, symbol='EURUSD',
        direction=OrderDirection.LONG, lots=0.1, entry_price=1.1000,
        stop_loss=1.0980, take_profit=1.1040)


def _pending(resolved=3, filled=2, rejected=0, forced=1, timed_out=0,
             avg=42.0, mn=21.0, mx=60.0, actives=None) -> PendingOrderStats:
    ps = PendingOrderStats(
        total_resolved=resolved, total_filled=filled, total_rejected=rejected,
        total_force_closed=forced, total_timed_out=timed_out,
        avg_latency_ms=avg, min_latency_ms=mn, max_latency_ms=mx)
    if actives:
        ps.active_limit_orders = actives
    return ps


def _unit(name: str = 's1', pending: PendingOrderStats = None) -> RunUnit:
    return RunUnit(name=name, symbol='EURUSD', pending_stats=pending)


class TestBuild:
    def test_maps_unit(self):
        report = build_pending_orders_report([_unit(pending=_pending())])
        assert len(report.units) == 1
        u = report.units[0]
        assert (u.total_resolved, u.total_filled, u.total_force_closed) == (3, 2, 1)
        assert (u.avg_latency_ms, u.min_latency_ms, u.max_latency_ms) == (42.0, 21.0, 60.0)

    def test_active_orders_mapped(self):
        report = build_pending_orders_report([_unit(pending=_pending(actives=[_active()]))])
        rows = report.units[0].active_limit_orders
        assert len(rows) == 1
        a = rows[0]
        assert a.order_id == 'L1' and a.order_type == 'limit' and a.direction == 'long'
        assert a.entry_price == 1.1000 and a.stop_loss == 1.0980 and a.take_profit == 1.1040

    def test_skips_units_without_activity(self):
        report = build_pending_orders_report([
            _unit(name='none', pending=None),
            _unit(name='empty', pending=PendingOrderStats()),   # no resolved, no active
            _unit(name='real', pending=_pending()),
        ])
        assert [u.name for u in report.units] == ['real']

    def test_no_latency_is_none(self):
        # min_latency_ms None → avg reported as None (no latency data)
        ps = PendingOrderStats(total_resolved=1, total_filled=1)
        report = build_pending_orders_report([_unit(pending=ps)])
        assert report.units[0].avg_latency_ms is None
