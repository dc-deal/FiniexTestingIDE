"""
Trade-History Report Builder Tests (#391).

The postprocessor is a pure function (RunUnits → TradeHistoryReport), so it is tested
in isolation with hand-built fixture trade records wrapped in a single RunUnit — no
simulation or live run required. Covers mapping, the filter path (symbol / close
reason / time range), distinct symbols, and the empty case.
"""

from datetime import datetime, timedelta, timezone

from python.framework.reporting.builders.run_unit import RunUnit
from python.framework.reporting.builders.trade_history_report_builder import build_trade_history_report
from python.framework.types.portfolio_types.portfolio_trade_record_types import (
    CloseReason, CloseType, EntryType, TradeRecord)
from python.framework.types.trading_env_types.order_types import OrderDirection


_T0 = datetime(2025, 10, 13, 8, 0, 0, tzinfo=timezone.utc)


def _units(trades):
    """Wrap a flat trade list in a single RunUnit (the builder consumes units)."""
    return [RunUnit(name='', symbol='', trade_history=trades)]


def _trade(
    position_id: str = 'p1',
    symbol: str = 'EURUSD',
    direction: OrderDirection = OrderDirection.LONG,
    net_pnl: float = 10.0,
    close_reason: CloseReason = CloseReason.TP_TRIGGERED,
    entry_offset_min: int = 0,
    duration_min: int = 30,
    initial_risk: float | None = None,
    mae_price: float = 0.0,
    mfe_price: float = 0.0,
    mae_pnl: float = 0.0,
    mfe_pnl: float = 0.0,
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
        initial_risk=initial_risk,
        mae_price=mae_price, mfe_price=mfe_price, mae_pnl=mae_pnl, mfe_pnl=mfe_pnl,
        close_reason=close_reason, entry_type=EntryType.MARKET,
    )


class TestMapping:
    """TradeRecord → renderable row."""

    def test_builds_rows(self):
        report = build_trade_history_report(_units([_trade(), _trade(position_id='p2')]))
        assert report.count == 2
        assert len(report.trades) == 2

    def test_row_fields_mapped(self):
        report = build_trade_history_report(_units([_trade(net_pnl=12.5, duration_min=15)]))
        row = report.trades[0]
        assert row.direction == 'long'                 # enum → value
        assert row.close_reason == 'tp_triggered'
        assert row.net_pnl == 12.5
        assert row.duration_s == 15 * 60               # exit - entry
        assert row.entry_time.endswith('+00:00')       # ISO-8601 UTC

    def test_short_direction(self):
        report = build_trade_history_report(_units([_trade(direction=OrderDirection.SHORT)]))
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
        report = build_trade_history_report(_units(self._mixed()), symbol='GBPUSD')
        assert report.count == 1
        assert report.trades[0].position_id == 'p2'

    def test_filter_by_close_reason(self):
        report = build_trade_history_report(_units(self._mixed()), close_reason='sl_triggered')
        assert report.count == 2
        assert {r.position_id for r in report.trades} == {'p2', 'p3'}

    def test_filter_by_time_range(self):
        report = build_trade_history_report(
            _units(self._mixed()), start=_T0 + timedelta(minutes=30), end=_T0 + timedelta(minutes=90))
        assert report.count == 1
        assert report.trades[0].position_id == 'p2'


class TestMetadata:
    """Distinct symbols + empty case."""

    def test_distinct_symbols_sorted(self):
        report = build_trade_history_report(_units([
            _trade(symbol='GBPUSD'), _trade(symbol='EURUSD'), _trade(symbol='EURUSD')]))
        assert report.symbols == ['EURUSD', 'GBPUSD']

    def test_empty(self):
        report = build_trade_history_report(_units([]))
        assert report.count == 0
        assert report.trades == []
        assert report.symbols == []

    def test_scenario_totals(self):
        # per-scenario footer totals (the table footer, model-served)
        report = build_trade_history_report(
            _units([_trade(net_pnl=20.0), _trade(position_id='p2', net_pnl=-10.0)]))
        assert len(report.scenario_totals) == 1
        st = report.scenario_totals[0]
        assert st.trade_count == 2
        assert round(st.net_pnl, 2) == 10.0        # 20 - 10
        assert round(st.total_fees, 2) == 1.6      # 0.8 × 2


class TestAnalytics:
    """#389 — R-multiple + pips per row, and the aggregate analytics block."""

    def test_r_multiple(self):
        # net_pnl 20 / initial_risk 10 → R = 2.0
        report = build_trade_history_report(_units([_trade(net_pnl=20.0, initial_risk=10.0)]))
        assert report.trades[0].r_multiple == 2.0

    def test_r_multiple_none_without_sl(self):
        # no initial_risk (trade had no stop loss) → R undefined
        assert build_trade_history_report(_units([_trade()])).trades[0].r_multiple is None

    def test_pips_forex_convention(self):
        # entry 1.1000, digits 5 → pip = 1e-4; |Δprice| / pip
        row = build_trade_history_report(
            _units([_trade(mae_price=1.0990, mfe_price=1.1030)])).trades[0]
        assert round(row.mae_pips, 1) == 10.0      # |1.1000 - 1.0990| / 1e-4
        assert round(row.mfe_pips, 1) == 30.0      # |1.1030 - 1.1000| / 1e-4

    def test_aggregate(self):
        trades = [
            _trade(position_id='w1', net_pnl=20.0, initial_risk=10.0, mae_pnl=-3.0),  # R=2
            _trade(position_id='w2', net_pnl=20.0, initial_risk=10.0, mae_pnl=-5.0),  # R=2
            _trade(position_id='l1', net_pnl=-10.0, initial_risk=10.0, mae_pnl=-12.0, mfe_pnl=4.0),  # R=-1
            _trade(position_id='l2', net_pnl=-10.0, initial_risk=10.0, mae_pnl=-8.0, mfe_pnl=6.0),   # R=-1
        ]
        a = build_trade_history_report(_units(trades)).analytics[0]   # single currency ('')
        assert a.r_trade_count == 4
        assert a.trade_count == 4
        assert a.expectancy == 0.5                 # (2+2-1-1)/4
        assert a.avg_win_r == 2.0
        assert a.avg_loss_r == -1.0
        assert a.avg_mae_winners == -4.0           # (-3 + -5)/2
        assert a.avg_mae_losers == -10.0           # (-12 + -8)/2
        assert a.avg_mfe_losers == 5.0             # (4 + 6)/2
        # per-currency P&L totals (#393 — the trade-table TOTAL, model-served)
        assert round(a.net_pnl, 2) == 20.0         # 20 + 20 - 10 - 10
        assert round(a.total_fees, 2) == 3.2       # 0.8 × 4
        assert round(a.gross_pnl, 2) == 23.2       # net + fees

    def test_empty_analytics(self):
        assert build_trade_history_report(_units([])).analytics == []   # no rows → no currency groups
