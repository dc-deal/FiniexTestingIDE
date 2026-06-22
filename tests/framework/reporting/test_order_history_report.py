"""
Order-History Report Builder Tests (#391).

The postprocessor is a pure function (RunUnits → OrderHistoryReport), tested in
isolation with hand-built fixture orders wrapped in a single RunUnit — no simulation
or live run required. Covers mapping (incl. None-safe optional fields + rejected
orders), the filter path (symbol / status), distinct symbols, and the empty case.
"""

from datetime import datetime, timezone

from python.framework.reporting.builders.order_history_report_builder import build_order_history_report
from python.framework.reporting.builders.run_unit import RunUnit
from python.framework.types.trading_env_types.order_types import (
    OrderAction, OrderDirection, OrderResult, OrderStatus, RejectionReason)


_T0 = datetime(2025, 10, 13, 8, 0, 0, tzinfo=timezone.utc)


def _units(orders):
    """Wrap a flat order list in a single RunUnit (the builder consumes units)."""
    return [RunUnit(name='', symbol='', order_history=orders)]


def _order(
    order_id: str = 'o1',
    symbol: str = 'EURUSD',
    status: OrderStatus = OrderStatus.EXECUTED,
    direction: OrderDirection = OrderDirection.LONG,
) -> OrderResult:
    """A filled order fixture with sensible defaults."""
    return OrderResult(
        order_id=order_id, status=status,
        executed_price=1.1000, executed_lots=0.1, execution_time=_T0,
        commission=0.2, swap=0.0, slippage_points=1.0,
        position_id=f'pos_{order_id}', action=OrderAction.OPEN,
        symbol=symbol, direction=direction, requested_lots=0.1,
    )


def _rejected(order_id: str = 'o9', symbol: str = 'EURUSD') -> OrderResult:
    """A rejected order fixture — optional execution fields stay None."""
    return OrderResult(
        order_id=order_id, status=OrderStatus.REJECTED,
        symbol=symbol, direction=OrderDirection.SHORT, requested_lots=0.5,
        rejection_reason=RejectionReason.INSUFFICIENT_MARGIN,
        rejection_message='not enough margin',
    )


class TestMapping:
    """OrderResult → renderable row."""

    def test_builds_rows(self):
        report = build_order_history_report(_units([_order(), _order(order_id='o2')]))
        assert report.count == 2
        assert len(report.orders) == 2

    def test_filled_row_fields_mapped(self):
        report = build_order_history_report(_units([_order()]))
        row = report.orders[0]
        assert row.direction == 'long'                 # enum → value
        assert row.action == 'open'
        assert row.status == 'executed'
        assert row.executed_price == 1.1000
        assert row.execution_time.endswith('+00:00')   # ISO-8601 UTC
        assert row.rejection_reason == ''

    def test_rejected_row_is_none_safe(self):
        row = build_order_history_report(_units([_rejected()])).orders[0]
        assert row.status == 'rejected'
        assert row.rejection_reason == 'insufficient_margin'
        assert row.rejection_message == 'not enough margin'
        assert row.executed_price == 0.0               # None → 0.0
        assert row.execution_time == ''                # None → ''


class TestFilters:
    """One shared filter path for console / CSV / API."""

    def _mixed(self):
        return [
            _order(order_id='o1', symbol='EURUSD', status=OrderStatus.EXECUTED),
            _order(order_id='o2', symbol='GBPUSD', status=OrderStatus.EXECUTED),
            _rejected(order_id='o3', symbol='EURUSD'),
        ]

    def test_filter_by_symbol(self):
        report = build_order_history_report(_units(self._mixed()), symbol='GBPUSD')
        assert report.count == 1
        assert report.orders[0].order_id == 'o2'

    def test_filter_by_status(self):
        report = build_order_history_report(_units(self._mixed()), status='rejected')
        assert report.count == 1
        assert report.orders[0].order_id == 'o3'


class TestMetadata:
    """Distinct symbols + empty case."""

    def test_distinct_symbols_sorted(self):
        report = build_order_history_report(_units([
            _order(symbol='GBPUSD'), _order(symbol='EURUSD'), _order(symbol='EURUSD')]))
        assert report.symbols == ['EURUSD', 'GBPUSD']

    def test_empty(self):
        report = build_order_history_report(_units([]))
        assert report.count == 0
        assert report.orders == []
        assert report.symbols == []
