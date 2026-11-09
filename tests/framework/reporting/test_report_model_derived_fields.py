"""
Report Model Derived Fields Tests (#557).

A figure the consoles print that comes from fields of one model is a computed field ON that model:
derived where the model is, served with it, and unable to drift from the fields it is read from.
These tests hold each such field to its definition — the shares, the differences and the counts
that a renderer used to compute while it printed.
"""

import pytest

from python.framework.reporting.aggregation.aggregation_fold import fold_group
from python.framework.reporting.aggregation.tables.deployment_figure_table import (
    DEPLOYMENT_CAPTION_TABLE,
)
from python.framework.types.api.report_types import (
    AggregatedPortfolioRow,
    BookingPeriodRow,
    BookingPeriodsReport,
    BrokerInfoRow,
    ColdStartReport,
    ColdStartSkippedRow,
    DeploymentCaption,
    DeploymentSessionRow,
    FeedStabilitySourceRow,
    PerformanceTracking,
    RobustnessReport,
    RobustnessRoleAggregate,
    RunMetaReport,
    SafetyReport,
    SafetyDayRow,
    ScenarioDetailsReport,
    ScenarioDetailsRow,
    SessionBookingState,
    SignalSourceRow,
    SignalUsageRow,
    UnitErrorRow,
    RunSummary,
    TradeHistoryRow,
    WalkForwardClass,
    WarningsErrorsOutcome,
    WarningsErrorsReport,
    WorkerDecisionReport,
)

# Every report artifact names its run (#475); the value is opaque to these tests.
_RUN_ID = '20260830_120000_a1b2c3d4'

# The headline an aggregated row requires, as an old artifact would carry it
_HEADLINE = {'currency': 'USD', 'unit_count': 1, 'total_trades': 0, 'winning_trades': 0,
             'losing_trades': 0, 'win_rate': 0.0, 'profit_factor': None, 'total_profit': 0.0,
             'total_loss': 0.0, 'net_profit': 0.0, 'account_max_drawdown': 0.0, 'total_fees': 0.0}


class TestScenarioDetails:
    """The per-scenario signal shares and the report's completion counts."""

    def test_the_non_flat_signals_and_both_shares(self):
        row = ScenarioDetailsRow(name='s1', symbol='EURUSD', ticks_processed=200,
                                 buy_signals=6, sell_signals=4, flat_signals=190,
                                 trades_requested=5)
        assert (row.non_flat_signals, row.non_flat_signal_pct, row.trades_requested_pct) == (
            10, 5.0, 2.5)

    def test_uncounted_decisions_have_no_share(self):
        row = ScenarioDetailsRow(name='s1', symbol='EURUSD', ticks_processed=200)
        assert (row.non_flat_signals, row.non_flat_signal_pct,
                row.trades_requested_pct) == (None, None, None)

    def test_no_tick_is_a_share_of_zero(self):
        row = ScenarioDetailsRow(name='s1', symbol='EURUSD', ticks_processed=0,
                                 buy_signals=0, sell_signals=0, trades_requested=0)
        assert (row.non_flat_signal_pct, row.trades_requested_pct) == (0.0, 0.0)

    def test_the_report_counts_its_units(self):
        report = ScenarioDetailsReport(run_id=_RUN_ID, units=[
            ScenarioDetailsRow(name='a', symbol='EURUSD'),
            ScenarioDetailsRow(name='b', symbol='EURUSD', status='failed'),
            ScenarioDetailsRow(name='c', symbol='EURUSD', status='hybrid')])
        assert (report.scenario_count, report.failed_scenario_count,
                report.completed_scenario_count) == (3, 2, 1)


class TestFeedStability:
    """The share of a source's counted ticks that were fresh."""

    def test_the_fresh_share_counts_blind_ticks_too(self):
        row = FeedStabilitySourceRow(source='src', fresh_ticks=60, stale_ticks=30, blind_ticks=10)
        assert row.fresh_tick_pct == 60.0

    def test_no_counter_is_no_share(self):
        assert FeedStabilitySourceRow(source='src').fresh_tick_pct is None


class TestBookingPeriods:
    """The period and unit counts and how far the periods lie from the run's figure."""

    def _period(self, unit, number, net_pnl):
        return BookingPeriodRow(
            unit_name=unit, period_no=number, opened_at='2026-01-01T00:00:00+00:00',
            closed_at='2026-01-02T00:00:00+00:00', reason='day_end', currency='USD',
            trade_count=1, net_pnl=net_pnl, total_fees=0.0, win_rate=1.0, profit_factor=None)

    def test_the_counts(self):
        report = BookingPeriodsReport(run_id=_RUN_ID, periods=[
            self._period('a', 1, 1.0), self._period('a', 2, 2.0), self._period('b', 1, 3.0)])
        assert (report.period_count, report.period_unit_count) == (3, 2)

    def test_the_reconciliation_difference(self):
        report = BookingPeriodsReport(run_id=_RUN_ID, total_net_pnl=10.0, run_net_pnl=7.5)
        assert report.reconciliation_difference == 2.5

    def test_no_run_figure_is_no_difference(self):
        assert BookingPeriodsReport(run_id=_RUN_ID).reconciliation_difference is None


class TestTheCountsOfTheLists:
    """Every count a console prints of a served list is a field beside the list (678.b)."""

    def test_cold_start_and_safety(self):
        assert ColdStartReport(run_id=_RUN_ID).adopted_count == 0
        skipped = [ColdStartSkippedRow(reason='foreign_key', broker_ref=ref) for ref in ('o1', 'o2')]
        assert ColdStartReport(run_id=_RUN_ID, skipped=skipped).skipped_count == 2
        assert SafetyReport(run_id=_RUN_ID, days=[SafetyDayRow(day='2026-01-01')]).day_count == 1

    def test_errors_signal_and_broker(self):
        row = UnitErrorRow(name='s1', validation_errors=['a', 'b', 'c', 'd'])
        assert (row.validation_error_count, row.logged_error_count) == (4, 0)
        assert WarningsErrorsReport(run_id=_RUN_ID, errors=[row]).error_unit_count == 1
        assert SignalSourceRow(source='src', usages=[SignalUsageRow(scenario='s1')]).usage_count == 1
        assert BrokerInfoRow(broker_type='mt5', scenarios=['s1', 's2']).scenario_count == 2

    def test_the_counts_are_served(self):
        dumped = BrokerInfoRow(broker_type='mt5', scenarios=['s1']).model_dump(mode='json')
        assert dumped['scenario_count'] == 1

    def test_the_first_failure_s_further_lines(self):
        outcome = WarningsErrorsOutcome(failed_count=1, total_units=1,
                                        first_failure_error='one\ntwo\nthree')
        assert outcome.first_failure_more_messages == 2
        assert WarningsErrorsOutcome().first_failure_more_messages == 0


class TestTheWalkForwardClass:
    """One classification of the efficiency — the console prints it, the validator fires on it."""

    @pytest.mark.parametrize('is_n, oos_n, efficiency, expected', [
        (2, 5, 1.0, WalkForwardClass.INCONCLUSIVE),     # a bucket below the minimum
        (5, 5, None, WalkForwardClass.UNDEFINED),
        (5, 5, 0.8, WalkForwardClass.ROBUST),           # at the robust threshold
        (5, 5, 0.6, WalkForwardClass.MODERATE),
        (5, 5, 0.4, WalkForwardClass.OVERFIT),
    ])
    def test_the_classes(self, is_n, oos_n, efficiency, expected):
        assert WalkForwardClass.of(is_n, oos_n, 3, efficiency, 0.8, 0.5) is expected

    def test_the_report_serves_its_class_and_its_drift(self):
        report = RobustnessReport(
            run_id=_RUN_ID, min_windows=3, walk_forward_efficiency=0.4,
            in_sample=RobustnessRoleAggregate(role='in_sample', window_count=4),
            out_of_sample=RobustnessRoleAggregate(role='out_of_sample', window_count=4),
            drifting_windows=['w2'])
        assert report.walk_forward_class is WalkForwardClass.OVERFIT
        assert report.drift_count == 1
        assert report.model_dump(mode='json')['walk_forward_class'] == 'overfit'

    def test_a_missing_bucket_is_inconclusive(self):
        assert RobustnessReport(run_id=_RUN_ID).walk_forward_class is WalkForwardClass.INCONCLUSIVE


class TestTheDeploymentCaption:
    """The detail view's caption, folded through its table."""

    def _session(self, run_id, currency, bot_id=''):
        return DeploymentSessionRow(
            index=1, run_id=run_id, started=None, net_pnl=None, max_drawdown=None,
            max_drawdown_pct=None, currency=currency, bot_id=bot_id,
            booking_state=SessionBookingState.BOOKED)

    def test_distinct_runs_currencies_and_the_first_bot_id(self):
        caption = DeploymentCaption(**fold_group([
            self._session('r1', ''), self._session('r1', 'USD', 'bot_a'),
            self._session('r2', 'BTC', 'bot_b')], DEPLOYMENT_CAPTION_TABLE))
        assert caption.session_count == 2
        assert caption.currencies == ['BTC', 'USD']
        assert caption.bot_id == 'bot_a'

    def test_no_session_names_nothing(self):
        caption = DeploymentCaption(**fold_group([], DEPLOYMENT_CAPTION_TABLE))
        assert (caption.session_count, caption.currencies, caption.bot_id) == (0, [], '')


class TestThePerformanceTrackingClass:
    """Which tracking layers carried data — a description the executive prints, never a warning."""

    @pytest.mark.parametrize('worker_on, profiling_on, expected', [
        (True, True, PerformanceTracking.BOTH_ON),
        (False, True, PerformanceTracking.WORKER_OFF),
        (True, False, PerformanceTracking.PROFILING_OFF),
        (False, False, PerformanceTracking.ALL_OFF),
    ])
    def test_the_classes(self, worker_on, profiling_on, expected):
        assert PerformanceTracking.of(worker_on, profiling_on) is expected
        meta = RunMetaReport(run_id=_RUN_ID, worker_tracking_on=worker_on,
                             profiling_tracking_on=profiling_on)
        assert meta.performance_tracking is expected


class TestARecordWrittenBeforeContract52:
    """
    A figure read from fields of its own model is computed wherever the model is read, so a run
    recorded before the figure existed answers it rightly; a figure folded over rows it cannot
    rebuild is null there, never a zero that claims a measurement.
    """

    def test_the_cost_total_and_the_execution_rate_read_from_the_old_fields(self):
        row = AggregatedPortfolioRow.model_validate({
            'headline': _HEADLINE, 'total_spread_cost': 1.0, 'maker_fee': 0.5, 'taker_fee': 2.0})
        assert row.total_costs == 3.5
        summary = RunSummary.model_validate({'run_id': _RUN_ID, 'currencies': [],
                                             'orders_submitted': 4, 'orders_executed': 3})
        assert summary.execution_rate_pct == 75.0

    def test_a_folded_aggregate_is_null_on_an_old_record(self):
        report = WorkerDecisionReport.model_validate({'run_id': _RUN_ID, 'units': []})
        assert report.decision_totals is None and report.bottlenecks is None


class TestADurationAcrossARestart:
    """A position restored at boot keeps the entry index of the session that opened it (F4)."""

    def test_an_exit_before_its_entry_has_no_duration(self):
        row = TradeHistoryRow(
            position_id='p1', symbol='BTCUSD', direction='long', lots=0.1, entry_price=1.0,
            entry_time='2026-01-01T00:00:00+00:00', exit_price=1.1,
            exit_time='2026-01-02T00:00:00+00:00', duration_s=86400.0, close_reason='',
            gross_pnl=1.0, total_fees=0.0, net_pnl=1.0, entry_tick_index=50000,
            exit_tick_index=120)
        assert row.duration_ticks is None
