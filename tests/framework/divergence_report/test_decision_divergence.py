"""
Measure 1 of the divergence report (#512) — each compared day's decision on both sides.

Frames on both sides with a decision on the day bar's close; each test changes one thing — the
action, an order, the workers' outputs, the account value, the breaker — and the day is compared,
and a one-sided day proposed as the one explained kind that fits.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Dict, Optional

from python.framework.reporting.builders.divergence.decision_divergence_builder import (
    compare_decisions,
    decision_frame_on,
)
from python.framework.types.api.divergence_report_types import (
    DayOrderDivergence,
    DecisionComparisonState,
    ExplainedFlipKind,
    OrderOutcome,
    TradingDayClassification,
)
from python.framework.types.api.state_frame_types import StateFrame, StateFrameCause
from python.framework.types.decision_logic_types import DecisionLogicAction

_A = DecisionLogicAction
_S = DecisionComparisonState
_DAY = date(2026, 11, 26)
_OPENING = datetime(2026, 11, 26, tzinfo=timezone.utc)
_CLOSING = _OPENING + timedelta(days=1)
_DECIDED = _OPENING + timedelta(seconds=3)


def _frame(action: DecisionLogicAction = _A.FLAT, at: datetime = _DECIDED,
           ema: float = 3000.0, equity: float = 1000.0, blocked: bool = False,
           decision_bar: bool = True) -> StateFrame:
    return StateFrame(
        frame_time=at, written_at=at, written_for=StateFrameCause.BAR_CLOSE, ticks_processed=1,
        symbol='ETHUSD', account_currency='USD', last_price=3000.0, balance=equity, equity=equity,
        open_positions=[], safety_blocked=blocked, decision_action=action,
        worker_outputs={'my_trend': {'ema': ema}},
        closed_bars={'D1': at - timedelta(days=1)} if decision_bar else {})


def _day(first: bool = False) -> TradingDayClassification:
    return TradingDayClassification(trading_day=_DAY, opening=_OPENING, closing=_CLOSING,
                                    run_id='s1', included=True, first_of_segment=first)


def _orders(live_only: int = 0, backtest_only: int = 0) -> DayOrderDivergence:
    outcome = OrderOutcome(run_id='x', seq=1)
    return DayOrderDivergence(trading_day=_DAY, closing=_CLOSING,
                              live_one_sided=[outcome] * live_only,
                              backtest_one_sided=[outcome] * backtest_only)


def _compare(live: StateFrame, backtest: StateFrame, orders: Optional[DayOrderDivergence] = None,
             restarts: Optional[Dict] = None, first_tick: Optional[datetime] = None):
    comparisons, table = compare_decisions(
        [_day(first=first_tick is not None)], first_tick, [live], [backtest],
        {_CLOSING: orders or _orders()}, 'D1', restarts or {})
    return comparisons[0], table


class TestTwoDecisions:

    def test_the_same_action_and_orders_agree(self):
        comparison, table = _compare(_frame(), _frame())
        assert comparison.state is _S.AGREE and comparison.proposed_kind is None
        cell, = table.cells
        assert (cell.live_action, cell.backtest_action, cell.days) == (_A.FLAT, _A.FLAT, 1)

    def test_a_different_action_is_one_sided(self):
        comparison, table = _compare(_frame(_A.BUY), _frame())
        assert comparison.state is _S.ONE_SIDED
        assert table.cells[0].live_action is _A.BUY

    def test_an_order_one_side_sent_alone_is_one_sided_though_the_actions_agree(self):
        comparison, _ = _compare(_frame(_A.BUY), _frame(_A.BUY), _orders(backtest_only=1))
        assert comparison.state is _S.ONE_SIDED
        assert (comparison.live.order_count, comparison.backtest.order_count) == (0, 1)

    def test_a_side_without_its_decision_frame_is_not_checked_never_agreeing(self):
        comparison, table = _compare(_frame(decision_bar=False), _frame())
        assert comparison.state is _S.NOT_CHECKED and 'live side' in comparison.detail
        assert table.not_checked == 1 and table.cells == []


class TestTheProposedKind:

    def test_the_live_breaker_held_back_the_backtests_order(self):
        comparison, _ = _compare(_frame(blocked=True), _frame(_A.BUY), _orders(backtest_only=1))
        assert comparison.proposed_kind is ExplainedFlipKind.LIVE_BREAKER
        assert 'circuit breaker' in comparison.detail

    def test_differing_workers_within_a_restarts_warmup_are_a_rewarmup(self):
        restarts = {'s2': (_OPENING - timedelta(hours=10), timedelta(days=2))}
        comparison, _ = _compare(_frame(_A.BUY, ema=2991.0), _frame(ema=2987.4),
                                 restarts=restarts)
        assert comparison.proposed_kind is ExplainedFlipKind.REWARMUP
        assert 's2 restarted' in comparison.detail and 'my_trend.ema 2991.0 / 2987.4' in \
            comparison.detail

    def test_differing_workers_with_no_restart_in_reach_are_a_capture_unconfirmed(self):
        restarts = {'s2': (_OPENING - timedelta(days=9), timedelta(days=2))}
        comparison, _ = _compare(_frame(_A.BUY, ema=2991.0), _frame(ema=2987.4),
                                 restarts=restarts)
        assert comparison.proposed_kind is ExplainedFlipKind.CAPTURE
        assert 'unconfirmed' in comparison.detail

    def test_agreeing_workers_and_differing_account_values_are_the_account_value(self):
        comparison, _ = _compare(_frame(_A.BUY, equity=1012.4), _frame(equity=1004.9))
        assert comparison.proposed_kind is ExplainedFlipKind.ACCOUNT_VALUE
        assert '1012.40 live, 1004.90 backtest' in comparison.detail

    def test_a_one_sided_day_with_nothing_differing_stays_unexplained(self):
        comparison, _ = _compare(_frame(_A.BUY), _frame())
        assert comparison.state is _S.ONE_SIDED and comparison.proposed_kind is None
        assert 'unexplained' in comparison.detail


class TestTheDecisionFrame:

    def test_a_pass_that_closed_no_decision_bar_is_no_decision(self):
        heartbeat = _frame(_A.BUY, at=_DECIDED + timedelta(hours=1), decision_bar=False)
        frames = [heartbeat, _frame(at=_DECIDED + timedelta(hours=2))]
        assert decision_frame_on(frames, _day(), None, 'D1').frame_time == \
            _DECIDED + timedelta(hours=2)

    def test_the_segments_first_day_decides_on_its_first_tick(self):
        first_tick = _OPENING + timedelta(minutes=5)
        frames = [_frame(at=_OPENING + timedelta(seconds=1)),
                  _frame(_A.BUY, at=first_tick, decision_bar=False)]
        assert decision_frame_on(frames, _day(), first_tick, 'D1').decision_action is _A.BUY
