"""
Trade-History Report Builder Tests (#391).

The postprocessor is a pure function (List[TradeRecord] → TradeHistoryReport), so it
is tested in isolation with hand-built fixture trade records — no simulation or live
run required. Covers mapping, the filter path (symbol / close reason / time range),
distinct symbols, and the empty case.
"""

from datetime import datetime, timedelta, timezone

from python.framework.reporting.run_reports.trade_history_report_builder import build_trade_history_report
from python.framework.types.portfolio_types.portfolio_trade_record_types import (
    CloseReason, CloseType, EntryType, TradeRecord)
from python.framework.types.trading_env_types.order_types import OrderDirection


_T0 = datetime(2025, 10, 13, 8, 0, 0, tzinfo=timezone.utc)


def _trade(
    position_id: str = 'p1',
    symbol: str = 'EURUSD',
    direction: OrderDirection = OrderDirection.LONG,
    net_pnl: float = 10.0,
    close_reason: CloseReason = CloseReason.TP_TRIGGERED,
    entry_offset_min: int = 0,
    duration_min: int = 30,
) -> TradeRecord:
    """Build a fixture closed trade with sensible defaults for the audit fields."""
    entry_time = _T0 + timedelta(minutes=entry_offset_min)
    exit_time = entry_time + timedelta(minutes=duration_min)
    return TradeRecord(
        position_id=position_id, symbol=symbol, direction=direction, lots=0.1,
        close_type=CloseType.FULL,
        entry_price=1.1000, entry_time=entry_time,
        entry_tick_value=1.0, entry_bid=1.0999, entry_ask=1.1001,
        exit_price=1.1020, exit_time=exit_time, exit_tick_value=1.0,
        entry_tick_index=0, exit_tick_index=100,
        digits=5, contract_size=100000,
        spread_cost=0.5, commission_cost=0.3, swap_cost=0.0, total_fees=0.8,
        gross_pnl=net_pnl + 0.8, net_pnl=net_pnl,
        close_reason=close_reason, entry_type=EntryType.MARKET,
    )


class TestMapping:
    """TradeRecord → renderable row."""

    def test_builds_rows(self):
        report = build_trade_history_report([_trade(), _trade(position_id='p2')])
        assert report.count == 2
        assert len(report.trades) == 2

    def test_row_fields_mapped(self):
        report = build_trade_history_report([_trade(net_pnl=12.5, duration_min=15)])
        row = report.trades[0]
        assert row.direction == 'long'                 # enum → value
        assert row.close_reason == 'tp_triggered'
        assert row.net_pnl == 12.5
        assert row.duration_s == 15 * 60               # exit - entry
        assert row.entry_time.endswith('+00:00')       # ISO-8601 UTC

    def test_short_direction(self):
        report = build_trade_history_report([_trade(direction=OrderDirection.SHORT)])
        assert report.trades[0].direction == 'short'


class TestFilters:
    """One shared filter path for console / CSV / API."""

    def _mixed(self):
        return [
            _trade(position_id='p1', symbol='EURUSD', close_reason=CloseReason.TP_TRIGGERED, entry_offset_min=0),
            _trade(position_id='p2', symbol='GBPUSD', close_reason=CloseReason.SL_TRIGGERED, entry_offset_min=60),
            _trade(position_id='p3', symbol='EURUSD', close_reason=CloseReason.SL_TRIGGERED, entry_offset_min=120),
        ]

    def test_filter_by_symbol(self):
        report = build_trade_history_report(self._mixed(), symbol='GBPUSD')
        assert report.count == 1
        assert report.trades[0].position_id == 'p2'

    def test_filter_by_close_reason(self):
        report = build_trade_history_report(self._mixed(), close_reason='sl_triggered')
        assert report.count == 2
        assert {r.position_id for r in report.trades} == {'p2', 'p3'}

    def test_filter_by_time_range(self):
        report = build_trade_history_report(
            self._mixed(), start=_T0 + timedelta(minutes=30), end=_T0 + timedelta(minutes=90))
        assert report.count == 1
        assert report.trades[0].position_id == 'p2'


class TestMetadata:
    """Distinct symbols + empty case."""

    def test_distinct_symbols_sorted(self):
        report = build_trade_history_report([
            _trade(symbol='GBPUSD'), _trade(symbol='EURUSD'), _trade(symbol='EURUSD')])
        assert report.symbols == ['EURUSD', 'GBPUSD']

    def test_empty(self):
        report = build_trade_history_report([])
        assert report.count == 0
        assert report.trades == []
        assert report.symbols == []
