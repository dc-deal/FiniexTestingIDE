"""
The Day-Close Message — a live session tells its operator how each trading day closed (#235).

The booking period the day's boundary sealed is the record: the session log carries its line and
the ledger books it. The operator's message is one more distribution of that period — sent for a
trading-day boundary only, switchable, and never a reason for the session to end.
"""

from datetime import datetime, timezone

from python.framework.autotrader import autotrader_tick_loop as tick_loop_module
from python.framework.autotrader.autotrader_tick_loop import AutotraderTickLoop
from python.framework.types.api.report_types import RunSummaryCurrency
from python.framework.types.autotrader_types.operator_alert_types import OperatorAlertKind
from python.framework.types.config_types.market_config_types import DayAnchorConfig
from python.framework.types.run_results_types import BookingPeriod, PeriodCloseReason
from tests.shared.operator_alert_recorder import OperatorAlertRecorder
from tests.shared.recording_logger import RecordingLogger


class _LoopStub:
    """The attributes the handler reads, and nothing else."""

    def __init__(self, daily_digest: bool = True):
        self._daily_digest = daily_digest
        self._day_anchor = DayAnchorConfig(local_time='00:00', timezone='UTC')
        self._operator_alerts = OperatorAlertRecorder()
        self._logger = RecordingLogger()

    def announce(self, period: BookingPeriod) -> None:
        AutotraderTickLoop._announce_day_closed(self, period)


def _period(reason: PeriodCloseReason = PeriodCloseReason.ANCHOR) -> BookingPeriod:
    figures = RunSummaryCurrency(
        currency='USD', net_pnl=12.4, profit_factor=None, win_rate=0.0, account_max_drawdown=0.0,
        total_fees=1.85, total_trades=3, winning_trades=2, losing_trades=1, expectancy=0.0,
        avg_win_r=None, avg_loss_r=None, r_trade_count=0, final_equity=1_012.4)
    return BookingPeriod(
        period_no=15, unit_name='eth_daily',
        opened_at=datetime(2026, 10, 20, tzinfo=timezone.utc),
        closed_at=datetime(2026, 10, 21, tzinfo=timezone.utc),
        reason=reason, trade_count=3, figures=figures, period_opening_equity=1_000.0)


def test_a_closed_trading_day_is_one_message_named_by_its_day():
    stub = _LoopStub()
    stub.announce(_period())

    assert stub._operator_alerts.kinds == [OperatorAlertKind.DAY_CLOSED]
    assert stub._operator_alerts.titles == ['Day closed — 2026-10-20']


def test_the_session_end_is_not_a_day_close():
    """The session's end has its own closing message — its last period is not announced twice."""
    stub = _LoopStub()
    stub.announce(_period(PeriodCloseReason.SESSION_END))

    assert stub._operator_alerts.kinds == []


def test_switched_off_it_stays_silent():
    stub = _LoopStub(daily_digest=False)
    stub.announce(_period())

    assert stub._operator_alerts.kinds == []


def test_a_message_that_cannot_be_written_does_not_end_the_session(monkeypatch):
    def broken(period, trading_day, day_opened_at):
        raise KeyError('figures')

    monkeypatch.setattr(tick_loop_module, 'day_closed_text', broken)
    stub = _LoopStub()
    stub.announce(_period())

    assert stub._operator_alerts.kinds == []
    assert 'day-close message for booking period 15' in stub._logger.text_at('error')
