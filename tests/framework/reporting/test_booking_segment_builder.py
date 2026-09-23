"""
One booking period's figures, derived from the records that fall inside it.

This is the Hauptbuch step: the trade records are the Grundbuch, a segment is the period summary
over them, and everything above it is derived in turn. The rule that makes the periods a
PARTITION — a trade belongs to the period it was CLOSED in, window end exclusive — is what these
tests pin, because every case that matters is a trade that crosses a boundary.

The six shapes a trade can have against two periods:

    T5  opened before the first period, closed inside it     → period 1
    T1  wholly inside the first                              → period 1
    T2  opened in the first, closed in the second            → period 2   (the exit decides)
    T3  wholly inside the second                             → period 2
    T4  opened in the second, still open at the end          → no period; a holding
    T0  closed exactly ON the boundary                       → period 2   (end is exclusive)
"""

from datetime import datetime, timedelta, timezone

from python.framework.reporting.builders.booking_segment_builder import (
    SegmentSnapshot,
    derive_booking_segment,
    describe_segment,
)
from python.framework.types.portfolio_types.portfolio_trade_record_types import (
    CloseReason,
    CloseType,
    EntryType,
    TradeRecord,
)
from python.framework.types.run_results_types import SegmentCloseReason
from python.framework.types.trading_env_types.order_types import OrderDirection

# Monday 00:00 UTC — the first period opens here, the second at Tuesday 00:00.
_MON = datetime(2026, 9, 21, tzinfo=timezone.utc)
_TUE = _MON + timedelta(days=1)
_WED = _TUE + timedelta(days=1)


def _trade(position_id: str, entry: datetime, exit_at: datetime, net_pnl: float,
           mae_pnl: float = 0.0, mfe_pnl: float = 0.0) -> TradeRecord:
    """
    A closed trade with an explicit entry and exit instant.

    Args:
        position_id: Its identity
        entry: When it opened
        exit_at: When it was realised — the instant that decides its period
        net_pnl: Its realised result
        mae_pnl / mfe_pnl: Excursion P&L, for the excursion figures

    Returns:
        The record
    """
    return TradeRecord(
        position_id=position_id, symbol='BTCUSD', direction=OrderDirection.LONG, lots=0.1,
        close_type=CloseType.FULL,
        entry_price=100.0, entry_time=entry,
        entry_tick_value=1.0, entry_bid=99.9, entry_ask=100.1,
        exit_price=101.0, exit_time=exit_at, exit_tick_value=1.0,
        entry_tick_index=0, exit_tick_index=100,
        digits=2, contract_size=1,
        spread_cost=0.0, commission_cost=0.0, swap_cost=0.0, total_fees=1.0,
        gross_pnl=net_pnl + 1.0, net_pnl=net_pnl,
        initial_risk=None,
        mae_price=0.0, mfe_price=0.0, mae_pnl=mae_pnl, mfe_pnl=mfe_pnl,
        close_reason=CloseReason.TP_TRIGGERED, entry_type=EntryType.MARKET,
        pip_size=0.01, price_unit='pip', account_currency='USD',
    )


def _snapshot(**overrides) -> SegmentSnapshot:
    """A stock snapshot in USD, overridable per test."""
    return SegmentSnapshot(currency='USD', **overrides)


# The six-case fixture used across the partition tests.
def _six_cases():
    """The trades of the doc example, minus the one that never closes."""
    return [
        _trade('T5', _MON - timedelta(days=3), _MON + timedelta(hours=8), +10.0),
        _trade('T1', _MON + timedelta(hours=9), _MON + timedelta(hours=15), +50.0),
        _trade('T2', _MON + timedelta(hours=20), _TUE + timedelta(hours=10), -30.0),
        _trade('T3', _TUE + timedelta(hours=11), _TUE + timedelta(hours=14), +20.0),
    ]


class TestThePeriodsPartitionTheRecords:

    def test_a_trade_belongs_to_the_period_it_closed_in(self):
        first = derive_booking_segment(
            'session', 1, _MON, _TUE, SegmentCloseReason.ANCHOR, _six_cases(), _snapshot())
        second = derive_booking_segment(
            'session', 2, _TUE, _WED, SegmentCloseReason.ANCHOR, _six_cases(), _snapshot())

        assert first.trade_count == 2 and first.figures.net_pnl == 60.0    # T5 + T1
        assert second.trade_count == 2 and second.figures.net_pnl == -10.0  # T2 + T3

    def test_a_trade_opened_before_the_period_still_books_in_it(self):
        # T5 entered the previous Friday and was carried over. Realisation is what counts, so
        # it belongs to Monday — a period may legitimately book a position it never opened.
        first = derive_booking_segment(
            'session', 1, _MON, _TUE, SegmentCloseReason.ANCHOR, _six_cases(), _snapshot())
        assert 'T5' in {row.position_id for row in _rows(first, _six_cases(), _MON, _TUE)}

    def test_a_crossing_trade_books_its_whole_result_in_the_later_period(self):
        # T2 worked overnight and pays out on Tuesday. The ENTIRE −30 lands there; Monday shows
        # nothing of it. Correct bookkeeping, and the reason a segment also carries its equity
        # band: the realised figure is not what the account did that day.
        first = derive_booking_segment(
            'session', 1, _MON, _TUE, SegmentCloseReason.ANCHOR, _six_cases(), _snapshot())
        second = derive_booking_segment(
            'session', 2, _TUE, _WED, SegmentCloseReason.ANCHOR, _six_cases(), _snapshot())
        assert first.figures.net_pnl == 60.0      # not 55 — no part of T2 is here
        assert second.figures.gross_loss == 30.0  # all of it is here

    def test_the_two_periods_add_up_to_the_whole(self):
        # The control total, which is the point of the construction: nothing counted twice,
        # nothing lost between the periods.
        trades = _six_cases()
        first = derive_booking_segment(
            'session', 1, _MON, _TUE, SegmentCloseReason.ANCHOR, trades, _snapshot())
        second = derive_booking_segment(
            'session', 2, _TUE, _WED, SegmentCloseReason.ANCHOR, trades, _snapshot())
        assert first.trade_count + second.trade_count == len(trades)
        assert (first.figures.net_pnl + second.figures.net_pnl
                == sum(t.net_pnl for t in trades))

    def test_a_trade_closing_exactly_on_the_boundary_falls_in_the_later_period(self):
        # End exclusive. Without it the trade would be in both periods or in neither, and the
        # partition — the property every figure above rests on — would be gone.
        boundary = [_trade('T0', _MON + timedelta(hours=1), _TUE, +7.0)]
        first = derive_booking_segment(
            'session', 1, _MON, _TUE, SegmentCloseReason.ANCHOR, boundary, _snapshot())
        second = derive_booking_segment(
            'session', 2, _TUE, _WED, SegmentCloseReason.ANCHOR, boundary, _snapshot())
        assert first.trade_count == 0
        assert second.trade_count == 1

    def test_a_position_still_open_belongs_to_no_period(self):
        # T4 never closed. It is a HOLDING, reported as stock (open_position_count,
        # unrealized_pnl) and never as a period's realised result.
        still_open = _six_cases()
        first = derive_booking_segment(
            'session', 1, _MON, _WED, SegmentCloseReason.SESSION_END, still_open,
            _snapshot(open_position_count=1, unrealized_pnl=-4.0))
        assert first.trade_count == 4
        assert first.figures.open_position_count == 1
        assert first.figures.unrealized_pnl == -4.0


class TestAPeriodThatTradedNothing:

    def test_is_a_period_rather_than_an_absence(self):
        quiet = derive_booking_segment(
            'session', 7, _MON, _TUE, SegmentCloseReason.ANCHOR, _six_cases(),
            _snapshot(final_equity=10_000.0))
        # Monday's trades exist, so take a window with none in it instead.
        empty = derive_booking_segment(
            'session', 8, _WED, _WED + timedelta(days=1), SegmentCloseReason.ANCHOR,
            _six_cases(), _snapshot(final_equity=10_000.0))
        assert quiet.trade_count == 2
        assert empty.trade_count == 0
        assert empty.figures.net_pnl == 0.0
        # The account value is still read: a day without a trade still has a balance.
        assert empty.figures.final_equity == 10_000.0

    def test_has_no_profit_factor_rather_than_a_zero_one(self):
        # A measured 0.0 would claim the bot made no profit; None says the quotient is
        # undefined, which is what an empty period actually is.
        empty = derive_booking_segment(
            'session', 1, _WED, _WED + timedelta(days=1), SegmentCloseReason.ANCHOR,
            _six_cases(), _snapshot())
        assert empty.figures.profit_factor is None


class TestTheStockHalfIsReadNeverDerived:

    def test_the_cumulative_and_the_own_drawdown_are_both_carried(self):
        # 1.c: the cumulative figure keeps max() correct across rows; the own figure answers
        # how far THIS day fell. Neither is derivable from the other.
        segment = derive_booking_segment(
            'session', 3, _MON, _TUE, SegmentCloseReason.ANCHOR, _six_cases(),
            _snapshot(account_max_drawdown=-180.0, max_equity=11_000.0,
                      account_max_dd_pct=1.64, segment_max_drawdown=40.0,
                      segment_max_equity=10_120.0, segment_min_equity=10_080.0))
        assert segment.figures.account_max_drawdown == -180.0   # over the deployment
        # A MAGNITUDE since #539 — the sign is a display decision (see the recorder).
        assert segment.segment_max_drawdown == 40.0             # inside this day

    def test_the_low_is_not_the_peak_minus_the_drawdown(self):
        # The peak and the trough are different moments: 100 → 90 → 120 gives a peak of 120,
        # a drawdown of 10 against the peak that stood THEN, and a low of 90 — not 110.
        segment = derive_booking_segment(
            'session', 1, _MON, _TUE, SegmentCloseReason.ANCHOR, [],
            _snapshot(segment_max_equity=120.0, segment_max_drawdown=10.0,
                      segment_min_equity=90.0))
        assert segment.segment_min_equity == 90.0
        assert segment.segment_max_equity - abs(segment.segment_max_drawdown) == 110.0


class TestTheLogLine:

    def test_carries_what_the_row_will_carry(self):
        # The rows are written once at the end, so a process that dies before that must leave
        # its periods recoverable. This line is that record.
        segment = derive_booking_segment(
            'session', 19, _MON, _TUE, SegmentCloseReason.ANCHOR, _six_cases(),
            _snapshot(final_equity=10_369.90))
        line = describe_segment(segment)
        assert '019' in line and 'anchor' in line
        assert '+60.00 USD' in line
        assert '10369.90' in line
        assert _MON.isoformat() in line and _TUE.isoformat() in line


def _rows(segment, trades, start, end):
    """Re-window the trades the way the derivation did, for a test that inspects membership."""
    from python.framework.reporting.builders.run_unit import RunUnit
    from python.framework.reporting.builders.trade_history_report_builder import (
        TradeWindowBasis,
        build_trade_history_report,
    )
    return build_trade_history_report(
        run_id='', units=[RunUnit(name=segment.unit_name, symbol='', trade_history=trades)],
        start=start, end=end, window_basis=TradeWindowBasis.EXIT).trades
