"""
Operator Alert Messages — what the operator reads, rendered from what the session decided (#235).

The closing message quotes the run's own grade and the figures the report built; it computes
nothing. The two maps it is built on are declared beside their enums, so a new level or a new
grade cannot arrive without an answer here.
"""

from datetime import date, datetime, timezone
from typing import List, Optional

import pytest

from python.framework.autotrader.operator_alerts.operator_alert_messages import (
    END_LEVEL_BY_OUTCOME,
    LEVEL_MARK,
    day_closed_text,
    days_closed_unheard_text,
    format_duration,
    orders_unanswered_at_stop_text,
    session_ended_text,
    session_started_text,
)
from python.framework.types.api.report_types import RunSummary, RunSummaryCurrency
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.autotrader_types.operator_alert_types import OperatorAlertLevel
from python.framework.types.autotrader_types.shutdown_status_types import StopWaitOutcome
from python.framework.types.run_origin_types import CodeIdentity, RepositoryState
from python.framework.types.run_outcome_types import RunOutcome
from python.framework.types.run_results_types import BookingPeriod, PeriodCloseReason
from python.framework.types.trading_env_types.awaited_order_types import AwaitedOrder
from python.framework.types.trading_env_types.order_types import (
    OrderAction,
    OrderDirection,
    OrderType,
)

_DAY_OPENED = datetime(2026, 10, 20, tzinfo=timezone.utc)


def _summary(net_pnl: float, trades: int, unrealized: float = 0.0) -> RunSummary:
    return RunSummary(run_id='r', currencies=[RunSummaryCurrency(
        currency='USD', net_pnl=net_pnl, profit_factor=None, win_rate=0.0,
        account_max_drawdown=0.0, total_fees=0.0, total_trades=trades, winning_trades=0,
        losing_trades=0, expectancy=0.0, avg_win_r=0.0, avg_loss_r=0.0, r_trade_count=0,
        unrealized_pnl=unrealized)])


def _period(opening: Optional[float] = 1_000.0, closing: Optional[float] = 1_012.4,
            unrealized: float = 0.0) -> BookingPeriod:
    figures = RunSummaryCurrency(
        currency='USD', net_pnl=12.4, profit_factor=None, win_rate=0.0, account_max_drawdown=0.0,
        total_fees=1.85, total_trades=3, winning_trades=2, losing_trades=1, expectancy=0.0,
        avg_win_r=None, avg_loss_r=None, r_trade_count=0, unrealized_pnl=unrealized,
        final_equity=closing)
    return BookingPeriod(
        period_no=15, unit_name='eth_daily',
        opened_at=datetime(2026, 10, 20, tzinfo=timezone.utc),
        closed_at=datetime(2026, 10, 21, tzinfo=timezone.utc),
        reason=PeriodCloseReason.ANCHOR, trade_count=3, figures=figures,
        period_max_equity=1_020.0, period_min_equity=995.0, period_max_drawdown=8.2,
        period_opening_equity=opening)


class TestTheDeclaredMaps:

    def test_every_level_has_a_mark(self):
        assert set(LEVEL_MARK) == set(OperatorAlertLevel)

    def test_every_grade_has_a_level(self):
        assert set(END_LEVEL_BY_OUTCOME) == set(RunOutcome)


class TestTheClosingMessage:

    def test_a_clean_end_quotes_the_grade_and_the_reports_figures(self):
        level, title, detail = session_ended_text(
            AutoTraderResult(shutdown_mode='normal', session_duration_s=187_920.0,
                             ticks_processed=31_204),
            _summary(-12.4, 6))
        assert level is OperatorAlertLevel.INFO
        assert title == 'Session ended — SUCCESS (exit 0)'
        assert detail == ('Duration: 2d 4h 12m\n'
                          'Ticks: 31,204\n'
                          'Trades: 6\n'
                          'Net P&L: -12.40 USD')

    def test_a_failed_end_is_critical_and_says_why(self):
        """A hard stop ends through a session-end request — its reason is the executor's."""
        level, title, detail = session_ended_text(
            AutoTraderResult(shutdown_mode='emergency'), _summary(0.0, 0),
            end_reason='emergency flatten incomplete (max_drawdown)')
        assert level is OperatorAlertLevel.CRITICAL
        assert title == 'Session ended — FAILED (exit 2)'
        assert detail.splitlines()[0] == 'Reason: emergency flatten incomplete (max_drawdown)'

    def test_an_open_holding_is_shown_beside_the_realised_figure(self):
        """Realised and valued are kept apart (#492) — a kept holding's loss is not hidden."""
        _, _, detail = session_ended_text(
            AutoTraderResult(shutdown_mode='normal'), _summary(-0.5, 1, unrealized=-412.3))
        assert 'Net P&L: -0.50 USD\nOpen P&L: -412.30 USD' in detail

    def test_an_operator_stop_reads_as_one(self):
        level, title, _ = session_ended_text(
            AutoTraderResult(shutdown_mode='emergency', operator_interrupted=True), None)
        assert level is OperatorAlertLevel.INFO
        assert title.startswith('Session stopped by the operator')

    def test_missing_figures_are_said_rather_than_shown_as_zero(self):
        _, _, detail = session_ended_text(AutoTraderResult(shutdown_mode='normal'), None)
        assert 'Figures: unavailable' in detail
        assert 'P&L' not in detail


class TestTheStartMessage:

    @pytest.mark.parametrize('adapter, dry_run, mode', [
        ('mock', True, 'mock replay'),
        ('kraken', True, 'dry run'),
        ('kraken', False, 'LIVE — real orders'),
    ])
    def test_it_says_whether_real_orders_go_out(self, adapter, dry_run, mode):
        title, detail = session_started_text(
            AutoTraderConfig(profile_name='eth_daily', adapter_type=adapter), dry_run,
            '20261020_070000_a1b2c3d4', [('framework', '4c61dc93deadbeef')])
        assert title == 'Session started'
        assert detail.startswith(mode)
        assert 'Commit: framework 4c61dc93' in detail and 'deadbeef' not in detail

    @staticmethod
    def _detail(code_identity: CodeIdentity) -> str:
        _, detail = session_started_text(
            AutoTraderConfig(profile_name='eth_daily', adapter_type='mock'), True,
            '20261020_070000_a1b2c3d4', code_identity.labelled_commits())
        return detail

    def test_it_names_the_commits_of_both_repositories(self):
        detail = self._detail(CodeIdentity(
            framework=RepositoryState(root='/srv/release', commit='4c61dc93'),
            repositories=[RepositoryState(root='/srv/home/my_strategy', commit='2f054f5')]))
        assert detail.endswith('Commit: framework 4c61dc93 · strategy 2f054f5')

    def test_several_other_repositories_are_told_apart_by_their_directory(self):
        detail = self._detail(CodeIdentity(
            framework=RepositoryState(root='/srv/release', commit='4c61dc93'),
            repositories=[RepositoryState(root='/srv/home/my_strategy', commit='2f054f5'),
                          RepositoryState(root='/srv/home/my_helpers', commit='7c1e9a0')]))
        assert detail.endswith(
            'Commit: framework 4c61dc93 · strategy my_strategy 2f054f5 · strategy my_helpers 7c1e9a0')

    def test_a_commit_nobody_could_read_is_said(self):
        detail = self._detail(CodeIdentity(
            framework=RepositoryState(root='/srv/release', commit=None),
            repositories=[RepositoryState(root='/srv/home/my_strategy', in_repository=False)]))
        assert detail.endswith('Commit: framework unknown · strategy unknown')

    def test_an_identity_without_this_repository_still_formats(self):
        assert self._detail(CodeIdentity()).endswith('Commit: framework unknown')

    def test_nothing_recorded_prints_no_commit_line(self):
        _, detail = session_started_text(
            AutoTraderConfig(profile_name='eth_daily', adapter_type='mock'), True,
            '20261020_070000_a1b2c3d4', [])
        assert 'Commit' not in detail


@pytest.mark.parametrize('seconds, text', [
    (5, '5s'), (725, '12m 5s'), (15_120, '4h 12m'), (187_920, '2d 4h 12m')])
def test_a_duration_reads_the_way_a_person_says_it(seconds, text):
    assert format_duration(seconds) == text


class TestTheDaysClosedUnheard:
    """The days one late check sealed after the first, said once (#476)."""

    @staticmethod
    def _silent_day(day: int) -> BookingPeriod:
        period = _period(opening=1_012.4, closing=1_012.4)
        period.trading_day = date(2026, 10, day)
        period.trade_count = 0
        return period

    def test_several_days_are_one_message_named_by_their_span(self):
        title, detail = days_closed_unheard_text([self._silent_day(21), self._silent_day(22)])
        assert title == 'Days closed unheard — 2026-10-21 → 2026-10-22'
        assert detail == ('2 trading day(s) closed at once, with no market data\n'
                          'Trades: 0\n'
                          'Equity: 1,012.40 → 1,012.40 USD')

    def test_one_day_is_named_by_itself(self):
        title, _ = days_closed_unheard_text([self._silent_day(21)])
        assert title == 'Days closed unheard — 2026-10-21'


class TestTheDayClose:
    """The sealed booking period, rendered — every figure the period's own (#235)."""

    def test_a_day_the_session_started_in_says_it_covers_only_the_rest(self):
        """A restart at 14:32 seals that day with the figures since 14:32, not the whole day."""
        period = _period()
        period.opened_at = datetime(2026, 10, 20, 14, 32, tzinfo=timezone.utc)
        _, detail = day_closed_text(period, date(2026, 10, 20), _DAY_OPENED)
        assert detail.splitlines()[0] == ('Covers: from 14:32 UTC — the session started '
                                          'during the day')

    def test_a_whole_day_says_nothing_about_coverage(self):
        _, detail = day_closed_text(_period(), date(2026, 10, 20), _DAY_OPENED)
        assert 'Covers' not in detail

    def test_a_closed_day_reads_its_period_and_computes_nothing(self):
        title, detail = day_closed_text(_period(), date(2026, 10, 20), _DAY_OPENED)

        assert title == 'Day closed — 2026-10-20'
        assert detail == ('Trades: 3\n'
                          'Net P&L: +12.40 USD\n'
                          'Fees: 1.85 USD\n'
                          'Equity: 1,000.00 → 1,012.40 USD\n'
                          'Deepest dip: -8.20 USD')

    def test_an_open_holding_is_shown_beside_the_realised_figure(self):
        _, detail = day_closed_text(_period(unrealized=4.1), date(2026, 10, 20), _DAY_OPENED)
        assert 'Net P&L: +12.40 USD\nOpen P&L: +4.10 USD' in detail

    def test_a_value_that_was_not_observed_says_so(self):
        _, detail = day_closed_text(
            _period(opening=None, closing=None), date(2026, 10, 20), _DAY_OPENED)
        assert 'Equity: not observed' in detail

    def test_one_end_observed_says_which(self):
        _, detail = day_closed_text(_period(opening=None), date(2026, 10, 20), _DAY_OPENED)
        assert 'Equity: 1,012.40 USD at the close, the open was not observed' in detail

    def test_a_day_that_never_fell_reads_zero(self):
        period = _period()
        for dip in (0.0, 0.004):
            period.period_max_drawdown = dip
            _, detail = day_closed_text(period, date(2026, 10, 20), _DAY_OPENED)
            assert detail.endswith('Deepest dip: 0.00 USD')


def _order_on_its_way(order_id: str = 'pos_btcusd_1',
                      order_type: OrderType = OrderType.MARKET) -> AwaitedOrder:
    """
    One order a stop waited for.

    Args:
        order_id: Its id
        order_type: What it was submitted as

    Returns:
        Its row
    """
    return AwaitedOrder(
        order_id=order_id, action=OrderAction.OPEN, order_type=order_type,
        direction=OrderDirection.LONG, lots=0.021, symbol='ETHUSD', sent_at_monotonic_s=1.0,
        waits_for='venue has not answered')


class TestOrdersUnansweredAtStop:

    def test_one_order_given_up(self):
        title, detail = orders_unanswered_at_stop_text(
            [_order_on_its_way()], [], 30.2, 'in 50.4 s (supervisor)')

        assert title == 'Stop is not clean — no answer for an order after 30.2 s'
        assert detail.splitlines() == [
            'market BUY 0.021 ETHUSD · pos_btcusd_1 · venue has not answered',
            'Recorded UNACCOUNTED; it may still fill at the venue.',
            'The session keeps stopping, kill in 50.4 s (supervisor).',
            'Check the account after the stop.',
        ]

    def test_a_kill_already_past_says_so(self):
        _, detail = orders_unanswered_at_stop_text(
            [_order_on_its_way()], [], 30.0, 'overdue by 2.0 s (supervisor)')
        assert 'The session keeps stopping, kill overdue by 2.0 s (supervisor).' in (
            detail.splitlines())

    def test_no_kill_declared_names_none(self):
        _, detail = orders_unanswered_at_stop_text([_order_on_its_way()], [], 30.0, None)
        assert 'The session keeps stopping.' in detail.splitlines()

    def test_what_stays_for_the_cleanup_is_told_apart(self):
        title, detail = orders_unanswered_at_stop_text(
            [_order_on_its_way()], [_order_on_its_way('pos_btcusd_2', OrderType.LIMIT)], 30.0, None)

        assert 'no answer for 2 orders' in title
        lines = detail.splitlines()
        assert lines.index('Recorded UNACCOUNTED; it may still fill at the venue.') < lines.index(
            'limit BUY 0.021 ETHUSD · pos_btcusd_2 · venue has not answered')
        assert 'Left to the cleanup, which cancels, leaves or books it.' in lines


class TestTheStopLineOfTheClosingMessage:
    """One line, read from the result, and only where a stop waited for an order (#630)."""

    @staticmethod
    def _lines(outcome: Optional[StopWaitOutcome]) -> List[str]:
        """
        The detail lines of the closing message of a session whose stop came to an outcome.

        Args:
            outcome: What the wait came to

        Returns:
            The lines
        """
        _, _, detail = session_ended_text(
            AutoTraderResult(shutdown_mode='normal', session_duration_s=60.0, stop_wait=outcome),
            _summary(0.0, 0), end_reason='termination signal (SIGTERM)')
        return detail.splitlines()

    def test_nothing_waited_no_line(self):
        assert not [line for line in self._lines(None) if line.startswith('Stop:')]

    def test_one_order_filled(self):
        lines = self._lines(StopWaitOutcome(waited_s=5.2, orders_executed=1))

        assert 'Stop: waited 5.2 s for 1 order, filled and booked' in lines
        assert lines.index('Reason: termination signal (SIGTERM)') < lines.index(
            'Stop: waited 5.2 s for 1 order, filled and booked')

    def test_a_run_out_names_the_unanswered_count(self):
        lines = self._lines(StopWaitOutcome(waited_s=30.0, orders_unaccounted=2))

        assert 'Stop: waited 30.0 s for 2 orders, no answer for 2, recorded unaccounted' in lines

    def test_one_order_ended_without_a_fill(self):
        lines = self._lines(StopWaitOutcome(waited_s=1.5, orders_ended_unfilled=1))

        assert 'Stop: waited 1.5 s for 1 order, ended without a fill' in lines

    def test_one_order_still_open(self):
        lines = self._lines(StopWaitOutcome(waited_s=1.5, orders_still_open=1))

        assert 'Stop: waited 1.5 s for 1 order, still open at the venue' in lines

    def test_one_order_filled_in_part(self):
        lines = self._lines(StopWaitOutcome(waited_s=0.2, orders_filled_in_part=1))

        assert 'Stop: waited 0.2 s for 1 order, filled in part, the part booked' in lines

    def test_each_ending_is_counted_where_there_are_several(self):
        lines = self._lines(StopWaitOutcome(
            waited_s=12.0, orders_executed=1, orders_filled_in_part=5, orders_unaccounted=2,
            orders_ended_unfilled=3, orders_still_open=4))

        assert ('Stop: waited 12.0 s for 15 orders, 1 filled and booked, 5 filled in part, the '
                'part booked, no answer for 2, recorded unaccounted, 3 ended without a fill, '
                '4 still open at the venue') in lines
