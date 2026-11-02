"""
Reading a restarted bot's ledger rows back as ONE history (#497).

A thirty-day live run restarts — #476 rehearses it on purpose — and every restart writes its
own ledger row under its own run id. Until the deployment identity those rows were a pile: the
ledger's only other reader filters on `sweep_id`, which an AutoTrader session does not have, so
its row was written and unreachable (§44 calls that a store with no read path).

What is pinned here is the GROUPING and what it refuses to do. It groups, it orders, it names
the gaps and the two kinds of change — and it judges none of them. Whether eleven hours between
two sessions was acceptable is a statement about the market and the operator's night, not about
the data, and a threshold here would quietly turn a record into a verdict.

The drawdown column deserves its own warning and gets one below: a live row carries the RUNNING
figure against the inherited peak, so the reduction over a deployment is max(), and a sum
double-counts every session's share of the same decline.
"""

from datetime import datetime, timezone
from typing import Optional
from unittest.mock import MagicMock, patch

from python.cli.run_index_cli import RunIndexCli
from python.framework.reporting.builders.deployment_history_builder import (
    build_deployment_histories,
    deployment_code_summary,
    deployment_comparability_advisory,
    summarize_deployments,
)
from python.framework.reporting.console.deployment_history_summary import (
    render_deployment_history,
    render_deployment_list,
)
from python.framework.types.api.report_types import (
    CodeChangeDirection,
    DeploymentCodeChange,
    DeploymentSessionRow,
    DeploymentSummary,
    RunResultRow,
)
from python.framework.types.run_kind_types import OrdersTo
from python.framework.types.run_origin_types import CodeStand, RepositoryStand

DEPLOYMENT = 'deploy_20260901_060000_ab12'


def row(run_id: str, timestamp: str, deployment: str = DEPLOYMENT, **overrides) -> RunResultRow:
    """
    One ledger row as an AutoTrader session writes it.

    Args:
        run_id: The session's own identity
        timestamp: Its start, ISO-8601, stored verbatim the way the ledger stores it
        deployment: The deployment it names, '' for a session that stands alone
        overrides: Any other column the case cares about

    Returns:
        The typed row
    """
    fields = dict(
        param_hash='strategy_v1', run_id=run_id, run_timestamp=timestamp,
        deployment_id=deployment, profile_hash='operation_v1', currency='USD',
        net_pnl=0.0, account_max_drawdown=0.0, account_max_drawdown_pct=0.0,
        recorded_at_utc='', scenario_set_name='a_bot')
    fields.update(overrides)
    return RunResultRow(**fields)


class TestGrouping:
    """Which rows form a history, and which form none."""

    def test_sessions_of_one_deployment_become_one_history(self):
        histories = build_deployment_histories([
            row('r1', '2026-09-01T06:00:00+00:00'),
            row('r2', '2026-09-02T06:00:00+00:00'),
        ])
        assert list(histories) == [DEPLOYMENT]
        assert [s.run_id for s in histories[DEPLOYMENT]] == ['r1', 'r2']

    def test_two_deployments_stay_apart(self):
        histories = build_deployment_histories([
            row('r1', '2026-09-01T06:00:00+00:00'),
            row('r2', '2026-09-01T07:00:00+00:00', deployment='deploy_other'),
        ])
        assert set(histories) == {DEPLOYMENT, 'deploy_other'}

    def test_a_one_off_row_joins_nothing(self):
        """
        Not even a placeholder group.

        A session that declared no deployment belongs to no history, and collecting the
        ungrouped ones under a synthetic key would assert a continuity nobody declared —
        the same untruth as a default for `deployment.continuous`.
        """
        histories = build_deployment_histories([
            row('r1', '2026-09-01T06:00:00+00:00', deployment=''),
            row('r2', '2026-09-02T06:00:00+00:00'),
        ])
        assert list(histories) == [DEPLOYMENT]
        assert [s.run_id for s in histories[DEPLOYMENT]] == ['r2']

    def test_an_empty_ledger_is_an_empty_answer(self):
        assert build_deployment_histories([]) == {}

    def test_sessions_are_ordered_by_start_not_by_arrival(self):
        """The ledger is a set of fragments; nothing about a read returns them in order."""
        histories = build_deployment_histories([
            row('later', '2026-09-03T06:00:00+00:00'),
            row('first', '2026-09-01T06:00:00+00:00'),
            row('middle', '2026-09-02T06:00:00+00:00'),
        ])
        assert [s.index for s in histories[DEPLOYMENT]] == [1, 2, 3]
        assert [s.run_id for s in histories[DEPLOYMENT]] == ['first', 'middle', 'later']


class TestMultiCurrency:
    """
    One run writes one row PER CURRENCY, and both of them are a real result.

    This used to keep the FIRST row per run and drop the rest, which counted a two-currency bot
    correctly as one session and silently lost half its money. The fix is not to count
    differently but to keep both and separate them where they are added up — a P&L column over
    two currencies is not a number.
    """

    def test_both_currencies_of_a_session_survive(self):
        histories = build_deployment_histories([
            row('r1', '2026-09-01T06:00:00+00:00', currency='USD'),
            row('r1', '2026-09-01T06:00:00+00:00', currency='BTC'),
        ])
        assert sorted(s.currency for s in histories[DEPLOYMENT]) == ['BTC', 'USD']

    def test_the_overview_reports_one_line_per_currency(self):
        # Counting the rows of both currencies as sessions would report a two-currency bot as
        # twice-restarted, with every gap between its "sessions" at zero.
        histories = build_deployment_histories([
            row('r1', '2026-09-01T06:00:00+00:00', currency='USD'),
            row('r1', '2026-09-01T06:00:00+00:00', currency='BTC'),
        ])
        summaries = summarize_deployments(histories, {DEPLOYMENT: None}, {})
        assert len(summaries) == 2
        assert {s.currency for s in summaries} == {'USD', 'BTC'}
        assert all(s.sessions == 1 for s in summaries)

    def test_each_currency_is_its_own_series(self):
        # The first session of the second currency has no predecessor in ITS currency: no gap
        # and no change mark may be measured against the other currency's last session.
        histories = build_deployment_histories([
            row('r1', '2026-09-01T06:00:00+00:00', currency='BTC'),
            row('r2', '2026-09-02T06:00:00+00:00', currency='BTC', param_hash='changed'),
            row('r1', '2026-09-01T06:00:00+00:00', currency='USD'),
        ])
        usd = [s for s in histories[DEPLOYMENT] if s.currency == 'USD']
        btc = [s for s in histories[DEPLOYMENT] if s.currency == 'BTC']

        assert (usd[0].index, usd[0].gap_hours, usd[0].strategy_changed) == (1, None, False)
        assert [s.index for s in btc] == [1, 2]


class TestWhatChangedBetweenSessions:
    """The two fingerprints, and why there are two of them."""

    def test_a_changed_strategy_is_marked(self):
        histories = build_deployment_histories([
            row('r1', '2026-09-01T06:00:00+00:00', param_hash='strategy_v1'),
            row('r2', '2026-09-02T06:00:00+00:00', param_hash='strategy_v2'),
        ])
        sessions = histories[DEPLOYMENT]
        assert sessions[0].strategy_changed is False
        assert sessions[1].strategy_changed is True

    def test_a_changed_operation_is_marked_separately(self):
        """
        A raised stop level is not a different strategy.

        That separation is the whole reason for two hashes: were they one, every safety
        tweak would read as a strategy change and put the run beyond comparison with its
        backtest for no reason (#512).
        """
        histories = build_deployment_histories([
            row('r1', '2026-09-01T06:00:00+00:00'),
            row('r2', '2026-09-02T06:00:00+00:00', profile_hash='operation_v2'),
        ])
        sessions = histories[DEPLOYMENT]
        assert sessions[1].operation_changed is True
        assert sessions[1].strategy_changed is False

    def test_the_first_session_changed_nothing(self):
        """There is nothing before it to differ from — not an unknown, an absence."""
        only = build_deployment_histories([row('r1', '2026-09-01T06:00:00+00:00')])[DEPLOYMENT][0]
        assert only.strategy_changed is False
        assert only.operation_changed is False


class TestGaps:
    """How long the bot was not running, reported and never judged."""

    def test_the_gap_is_measured_between_consecutive_sessions(self):
        sessions = build_deployment_histories([
            row('r1', '2026-09-01T06:00:00+00:00'),
            row('r2', '2026-09-01T18:00:00+00:00'),
        ])[DEPLOYMENT]
        assert sessions[0].gap_hours is None
        assert sessions[1].gap_hours == 12.0

    def test_an_unreadable_timestamp_yields_no_gap_rather_than_a_wrong_one(self):
        """
        A row that cannot say when it started produces no number.

        The same rule as a missing monotonic stamp: an unmeasurable duration reported as
        unmeasured costs a blank; reported as a made-up figure costs an investigation.
        """
        sessions = build_deployment_histories([
            row('r1', 'not-a-timestamp'),
            row('r2', '2026-09-02T06:00:00+00:00'),
        ])[DEPLOYMENT]
        assert all(s.gap_hours is None for s in sessions)


class TestDrawdownIsCarriedNotSummed:
    """The column that is easiest to add up and must not be."""

    def test_the_rows_carry_the_running_figure(self):
        """
        Each live row holds the drawdown against the INHERITED peak, so the deepest row IS
        the deployment's reduction. This test states the shape the renderer's footnote
        warns about, so a future aggregation cannot claim it was never written down.
        """
        sessions = build_deployment_histories([
            row('r1', '2026-09-01T06:00:00+00:00', account_max_drawdown=-120.0),
            row('r2', '2026-09-02T06:00:00+00:00', account_max_drawdown=-450.0),
            row('r3', '2026-09-03T06:00:00+00:00', account_max_drawdown=-450.0),
        ])[DEPLOYMENT]
        assert max(abs(s.max_drawdown) for s in sessions) == 450.0
        assert sum(abs(s.max_drawdown) for s in sessions) == 1020.0


class TestADrawdownShareNobodyMeasuredStaysUnknown:
    """
    A session whose rows carry no drawdown share — every day it sealed came before its account
    curve began (#476), or its rows predate the column — reads as unknown, never as zero, and
    neither the history, the overview nor the console stops on it.
    """

    def test_the_session_and_its_deployment_carry_none(self):
        histories = build_deployment_histories([
            row('r1', '2026-09-01T06:00:00+00:00', account_max_drawdown_pct=None),
        ])
        assert histories[DEPLOYMENT][0].max_drawdown_pct is None
        assert summarize_deployments(histories, {DEPLOYMENT: None}, {})[0].max_drawdown_pct is None

    def test_the_console_prints_a_dash(self, capsys):
        sessions = build_deployment_histories([
            row('r1', '2026-09-01T06:00:00+00:00', account_max_drawdown_pct=None),
        ])[DEPLOYMENT]
        render_deployment_history(DEPLOYMENT, sessions)
        line = next(text for text in capsys.readouterr().out.splitlines()
                    if text.startswith('r1 '))
        assert '—' in line
        assert '%' not in line


class TestADrawdownNobodyMeasuredStaysUnknown:
    """
    A session whose rows carry no drawdown — every day it sealed came before its account's first
    valuation — reads as unknown, never as a decline of zero (#476).
    """

    def test_the_session_carries_none_and_the_deepest_is_taken_over_the_others(self):
        histories = build_deployment_histories([
            row('r1', '2026-09-01T06:00:00+00:00', account_max_drawdown=None,
                account_max_drawdown_pct=None),
            row('r2', '2026-09-02T06:00:00+00:00', account_max_drawdown=-300.0,
                account_max_drawdown_pct=3.0),
        ])
        assert histories[DEPLOYMENT][0].max_drawdown is None
        summary = summarize_deployments(histories, {DEPLOYMENT: None}, {})[0]
        assert (summary.max_drawdown, summary.max_drawdown_pct) == (-300.0, 3.0)

    def test_a_deployment_nobody_measured_has_no_deepest(self, capsys):
        histories = build_deployment_histories([
            row('r1', '2026-09-01T06:00:00+00:00', account_max_drawdown=None,
                account_max_drawdown_pct=None),
        ])
        summaries = summarize_deployments(histories, {DEPLOYMENT: None}, {})
        assert summaries[0].max_drawdown is None
        render_deployment_list(summaries)
        render_deployment_history(DEPLOYMENT, histories[DEPLOYMENT])
        line = next(text for text in capsys.readouterr().out.splitlines()
                    if text.startswith('r1 '))
        assert line.split()[-2:] == ['—', '—'], 'the decline and its share read as unknown'


class TestADeclineIsShownNegativeAndZeroAsZero:
    """
    A decline is stored either way round, and the console shows it negative either way; a
    measured zero reads 0.00 — `-abs(0.0)` is -0.0, which printed as "-0.00".
    """

    def test_a_measured_zero_prints_as_zero(self, capsys):
        histories = build_deployment_histories([row('r1', '2026-09-01T06:00:00+00:00')])
        render_deployment_list(summarize_deployments(histories, {DEPLOYMENT: None}, {}))
        render_deployment_history(DEPLOYMENT, histories[DEPLOYMENT])
        printed = capsys.readouterr().out
        assert '-0.00' not in printed
        line = next(text for text in printed.splitlines() if text.startswith('r1 '))
        assert line.split()[-2:] == ['0.00', '0.00%']

    def test_either_sign_prints_negative(self, capsys):
        histories = build_deployment_histories([
            row('r1', '2026-09-01T06:00:00+00:00', account_max_drawdown=120.0),
            row('r2', '2026-09-02T06:00:00+00:00', account_max_drawdown=-450.0),
        ])
        render_deployment_history(DEPLOYMENT, histories[DEPLOYMENT])
        lines = {text.split()[0]: text for text in capsys.readouterr().out.splitlines()
                 if text.startswith(('r1 ', 'r2 '))}
        assert ' -120.00 ' in lines['r1'] and ' -450.00 ' in lines['r2']


class TestRendering:
    """PRESENT formats what DERIVE already decided (§12)."""

    def test_the_table_names_the_sessions_the_gap_and_both_changes(self, capsys):
        sessions = build_deployment_histories([
            row('r1', '2026-09-01T06:00:00+00:00'),
            row('r2', '2026-09-03T06:00:00+00:00',
                param_hash='strategy_v2', profile_hash='operation_v2'),
        ])[DEPLOYMENT]
        render_deployment_history(DEPLOYMENT, sessions)

        printed = capsys.readouterr().out
        assert DEPLOYMENT in printed
        assert '2 session(s)' in printed
        assert 'idle 2.0 d' in printed
        # The change is drawn as a SEPARATOR between the rows, not as a mark on one of them:
        # it belongs between two sessions, and that is also what it does to the numbers.
        assert 'STRATEGY and OPERATION CHANGED HERE' in printed
        assert 'answer different questions' in printed

    def test_the_drawdown_column_says_it_is_cumulative(self, capsys):
        """
        Without the footnote the column reads as a per-session figure, and the natural
        thing to do with a per-session column is add it up.
        """
        sessions = build_deployment_histories([row('r1', '2026-09-01T06:00:00+00:00')])[DEPLOYMENT]
        render_deployment_history(DEPLOYMENT, sessions)
        assert 'CUMULATIVE' in capsys.readouterr().out


class TestTheGapIsDowntimeNotTheSpanBetweenStarts:
    """
    What an operator means by "the bot was down for N hours".

    Measuring start to start counts the PREVIOUS session's whole runtime as downtime — on a
    bot that runs twelve hours a day that is the difference between "one hour" and "thirteen".
    The end comes from `recorded_at_utc`, written when the row was, which is within seconds of
    when the session closed.
    """

    def test_it_runs_from_the_predecessor_s_record_to_this_start(self):
        sessions = build_deployment_histories([
            row('r1', '2026-09-01T06:00:00+00:00',
                recorded_at_utc='2026-09-01T18:00:00+00:00'),
            row('r2', '2026-09-01T19:00:00+00:00'),
        ])[DEPLOYMENT]
        assert sessions[1].gap_hours == 1.0, (
            'measured start-to-start this reads 13 h — the twelve hours the bot was RUNNING')
        assert sessions[1].gap_between_starts is False

    def test_a_row_without_the_stamp_falls_back_and_says_so(self):
        """
        An older fragment has no end. The figure is still shown — it is the best available —
        but it is MARKED, because silently mixing two measures under one column heading is
        how a number stops meaning anything.
        """
        sessions = build_deployment_histories([
            row('r1', '2026-09-01T06:00:00+00:00'),
            row('r2', '2026-09-01T19:00:00+00:00'),
        ])[DEPLOYMENT]
        assert sessions[1].gap_hours == 13.0
        assert sessions[1].gap_between_starts is True

    def test_the_label_reaches_the_rendered_table(self, capsys):
        sessions = build_deployment_histories([
            row('r1', '2026-09-01T06:00:00+00:00'),
            row('r2', '2026-09-01T19:00:00+00:00'),
        ])[DEPLOYMENT]
        render_deployment_history(DEPLOYMENT, sessions)
        assert 'between starts' in capsys.readouterr().out


class TestASessionEndsWithTheRowOfItsClose:
    """
    A session writes a ledger row at every trading day's seal (#476), and every write stamps
    `recorded_at_utc`. So the stamp is the session's end only once the row of its close is
    there — before that the session is running or died, and its end is unknown rather than the
    moment its last day sealed.
    """

    @staticmethod
    def _day(run_id: str, start: str, no: int, reason: str, recorded: str) -> RunResultRow:
        return row(run_id, start, unit_name='session', period_no=no,
                   period_opened_at=f'2026-09-0{no}T00:00:00+00:00',
                   period_closed_at=f'2026-09-0{no + 1}T00:00:00+00:00',
                   period_close_reason=reason, recorded_at_utc=recorded)

    def test_a_session_with_only_sealed_days_has_no_end(self):
        sessions = build_deployment_histories([
            self._day('r1', '2026-09-01T06:00:00+00:00', 1, 'anchor', '2026-09-02T00:00:01+00:00'),
            self._day('r1', '2026-09-01T06:00:00+00:00', 2, 'anchor', '2026-09-03T00:00:01+00:00'),
        ])[DEPLOYMENT]
        assert sessions[0].ended is None
        assert sessions[0].ran_hours is None

    def test_the_row_of_its_close_ends_it(self):
        sessions = build_deployment_histories([
            self._day('r1', '2026-09-01T06:00:00+00:00', 1, 'anchor', '2026-09-02T18:00:00+00:00'),
            self._day('r1', '2026-09-01T06:00:00+00:00', 2, 'session_end',
                      '2026-09-02T18:00:00+00:00'),
        ])[DEPLOYMENT]
        assert sessions[0].ran_hours == 36.0

    def test_the_heading_never_adds_the_unfinished_to_the_sessions(self, capsys):
        """A session that sealed a day and never closed is in the table AND unfinished."""
        sessions = build_deployment_histories([
            self._day('r1', '2026-09-01T06:00:00+00:00', 1, 'anchor', '2026-09-02T00:00:01+00:00'),
        ])[DEPLOYMENT]
        render_deployment_history(DEPLOYMENT, sessions, unfinished=1)
        printed = capsys.readouterr().out
        assert '1 session(s) in the ledger · 1 never completed' in printed
        assert '+' not in printed.splitlines()[1]

    def test_the_gap_after_a_session_that_never_closed_is_marked(self):
        """Its last sealed day is not its end: the gap falls back to the starts and says so."""
        sessions = build_deployment_histories([
            self._day('r1', '2026-09-01T06:00:00+00:00', 1, 'anchor', '2026-09-02T00:00:01+00:00'),
            row('r2', '2026-09-03T06:00:00+00:00'),
        ])[DEPLOYMENT]
        assert sessions[1].gap_between_starts is True
        assert sessions[1].gap_hours == 48.0


class TestTheComparabilityAdvisory:
    """
    Whether the history may be read as ONE series — answered ABOVE the table.

    The per-session marks say where something changed. On eleven sessions with three changes
    the reader has usually summed the P&L column before reaching them, which is exactly the
    mistake the advisory exists to prevent. Sibling of `mixed_logic_version_advisory` (#390).
    """

    def test_an_unchanged_deployment_raises_nothing(self):
        """A warning that fires on the normal case is a warning that gets skipped."""
        assert deployment_comparability_advisory([
            row('r1', '2026-09-01T06:00:00+00:00'),
            row('r2', '2026-09-02T06:00:00+00:00'),
        ]) is None

    def test_a_single_session_raises_nothing(self):
        assert deployment_comparability_advisory([row('r1', '2026-09-01T06:00:00+00:00')]) is None

    def test_it_counts_both_kinds_of_stand(self):
        advisory = deployment_comparability_advisory([
            row('r1', '2026-09-01T06:00:00+00:00'),
            row('r2', '2026-09-02T06:00:00+00:00', param_hash='strategy_v2'),
            row('r3', '2026-09-03T06:00:00+00:00', param_hash='strategy_v2',
                profile_hash='operation_v2'),
        ])
        assert advisory is not None
        assert advisory.sessions == 3
        assert advisory.strategy_stands == 2
        assert advisory.operation_stands == 2

    def test_it_is_printed_above_the_table(self, capsys):
        rows = [row('r1', '2026-09-01T06:00:00+00:00'),
                row('r2', '2026-09-02T06:00:00+00:00', param_hash='strategy_v2')]
        sessions = build_deployment_histories(rows)[DEPLOYMENT]
        render_deployment_history(DEPLOYMENT, sessions,
                                  deployment_comparability_advisory(rows))

        printed = capsys.readouterr().out
        assert 'SPANS MORE THAN ONE CONFIGURATION' in printed
        assert printed.index('SPANS MORE THAN ONE') < printed.index('net P&L'), (
            'the question has to reach the reader before the numbers do')


class TestWhereTheOrdersWent:
    """
    Whether a session traded real money (#362) — on each session, on the overview line, and in
    the advisory when a deployment mixes a rehearsal with real money: its P&L column would add
    simulated fills to real ones, although strategy and operation never moved.
    """

    _ROWS = [row('r1', '2026-09-01T06:00:00+00:00', orders_to=OrdersTo.SIMULATED),
             row('r2', '2026-09-02T06:00:00+00:00', orders_to=OrdersTo.SIMULATED),
             row('r3', '2026-09-03T06:00:00+00:00', orders_to=OrdersTo.VENUE)]

    def test_each_session_says_it_and_the_switch_is_marked(self):
        sessions = build_deployment_histories(self._ROWS)[DEPLOYMENT]

        assert [s.orders_to for s in sessions] == [
            OrdersTo.SIMULATED, OrdersTo.SIMULATED, OrdersTo.VENUE]
        assert [s.orders_to_changed for s in sessions] == [False, False, True]

    def test_a_mix_alone_raises_the_advisory(self):
        advisory = deployment_comparability_advisory(self._ROWS)

        assert advisory is not None
        assert (advisory.strategy_stands, advisory.operation_stands) == (1, 1)
        assert advisory.orders_to == [OrdersTo.SIMULATED, OrdersTo.VENUE]

    def test_the_overview_names_both_and_marks_the_deployment(self):
        histories = build_deployment_histories(self._ROWS)
        summary = summarize_deployments(
            histories, {DEPLOYMENT: deployment_comparability_advisory(self._ROWS)}, {})[0]

        assert summary.orders_to == [OrdersTo.SIMULATED, OrdersTo.VENUE]
        assert summary.changed

    def test_an_older_row_says_nothing_and_changes_nothing(self):
        """Not recorded is not a change — and not a mix."""
        rows = [row('r1', '2026-09-01T06:00:00+00:00'),
                row('r2', '2026-09-02T06:00:00+00:00', orders_to=OrdersTo.VENUE)]
        sessions = build_deployment_histories(rows)[DEPLOYMENT]

        assert [s.orders_to_changed for s in sessions] == [False, False]
        assert deployment_comparability_advisory(rows) is None
        assert summarize_deployments({DEPLOYMENT: sessions}, {DEPLOYMENT: None}, {})[0].orders_to == [
            OrdersTo.VENUE]

    def test_the_console_says_it(self, capsys):
        histories = build_deployment_histories(self._ROWS)
        advisory = deployment_comparability_advisory(self._ROWS)
        render_deployment_history(DEPLOYMENT, histories[DEPLOYMENT], advisory)
        render_deployment_list(summarize_deployments(histories, {DEPLOYMENT: advisory}, {}))

        printed = capsys.readouterr().out
        assert 'simulated fills to real money' in printed
        assert 'ORDERS TO CHANGED HERE' in printed
        assert 'simulated,venue' in printed


class TestTheOverviewHalf:
    """One line per deployment — which one to open, not what happened inside it."""

    def test_pnl_sums_and_drawdown_does_not(self):
        """
        The two columns reduce differently, and getting it wrong is silent: P&L is per
        session, the drawdown is the RUNNING figure against the inherited peak.
        """
        rows = [
            row('r1', '2026-09-01T06:00:00+00:00', net_pnl=40.0, account_max_drawdown=-120.0),
            row('r2', '2026-09-02T06:00:00+00:00', net_pnl=-15.0, account_max_drawdown=-450.0),
            row('r3', '2026-09-03T06:00:00+00:00', net_pnl=5.0, account_max_drawdown=-450.0),
        ]
        histories = build_deployment_histories(rows)
        summary = summarize_deployments(histories, {DEPLOYMENT: None}, {})[0]

        assert summary.sessions == 3
        assert summary.net_pnl == 30.0
        assert summary.max_drawdown == -450.0, 'the drawdown was summed instead of maxed'

    def test_it_marks_a_deployment_whose_configuration_moved(self):
        rows = [row('r1', '2026-09-01T06:00:00+00:00'),
                row('r2', '2026-09-02T06:00:00+00:00', param_hash='strategy_v2')]
        histories = build_deployment_histories(rows)
        advisories = {DEPLOYMENT: deployment_comparability_advisory(rows)}

        assert summarize_deployments(histories, advisories, {})[0].changed is True

    def test_the_list_points_at_the_detail_view(self, capsys):
        """An overview that does not say how to descend is a dead end."""
        histories = build_deployment_histories([row('r1', '2026-09-01T06:00:00+00:00')])
        render_deployment_list(summarize_deployments(histories, {DEPLOYMENT: None}, {}))
        assert '--id' in capsys.readouterr().out


class TestTheDetailViewDescendsFromTheList:
    """
    The last step of the hierarchy: deployments → this deployment's sessions → THIS run.

    Which is why the table is keyed by the run id rather than by a position, and why it reads
    newest first: the first question about a deployment is what it is doing now, and the id is
    the directory name the next question needs.
    """

    def test_the_newest_session_comes_first(self, capsys):
        sessions = build_deployment_histories([
            row('oldest', '2026-09-01T06:00:00+00:00'),
            row('newest', '2026-09-03T06:00:00+00:00'),
        ])[DEPLOYMENT]
        render_deployment_history(DEPLOYMENT, sessions)

        printed = capsys.readouterr().out
        assert printed.index('newest') < printed.index('oldest')

    def test_each_line_is_keyed_by_the_run_id(self, capsys):
        """The id is what opens `runs/autotrader/<profile>/<run id>/`; a position number is not."""
        sessions = build_deployment_histories(
            [row('20260916_060500_8e10', '2026-09-16T06:05:00+00:00')])[DEPLOYMENT]
        render_deployment_history(DEPLOYMENT, sessions)

        printed = capsys.readouterr().out
        assert '20260916_060500_8e10' in printed
        assert 'runs/autotrader/<profile>/<run id>/' in printed

    def test_a_session_reports_how_long_it_ran(self):
        sessions = build_deployment_histories([
            row('r1', '2026-09-01T06:00:00+00:00',
                recorded_at_utc='2026-09-01T18:00:00+00:00'),
        ])[DEPLOYMENT]
        assert sessions[0].ran_hours == 12.0

    def test_an_unmeasurable_duration_reports_as_unmeasured(self, capsys):
        """
        A row written before the end stamp existed has no length, and a made-up one would
        land in a column a reader adds up. The same rule as a missing monotonic stamp.
        """
        sessions = build_deployment_histories(
            [row('r1', '2026-09-01T06:00:00+00:00')])[DEPLOYMENT]
        assert sessions[0].ran_hours is None
        render_deployment_history(DEPLOYMENT, sessions)
        assert '—' in capsys.readouterr().out


class TestTheBreakIsDrawnBetweenTheRows:
    """
    A configuration change belongs BETWEEN two sessions, not on one of them.

    A mark at the end of a line reads as a property of that session. A line across the table
    reads as what it is: everything above was produced by a different configuration from
    everything below. On a history long enough to matter, the eye has to be stopped.
    """

    def test_the_separator_sits_between_the_two_sessions(self, capsys):
        sessions = build_deployment_histories([
            row('older', '2026-09-01T06:00:00+00:00'),
            row('newer', '2026-09-03T06:00:00+00:00', param_hash='strategy_v2'),
        ])[DEPLOYMENT]
        render_deployment_history(DEPLOYMENT, sessions)

        printed = capsys.readouterr().out
        assert printed.index('newer') < printed.index('CHANGED HERE') < printed.index('older')

    def test_an_unchanged_history_draws_none(self, capsys):
        sessions = build_deployment_histories([
            row('r1', '2026-09-01T06:00:00+00:00'),
            row('r2', '2026-09-03T06:00:00+00:00'),
        ])[DEPLOYMENT]
        render_deployment_history(DEPLOYMENT, sessions)
        assert 'CHANGED HERE' not in capsys.readouterr().out


class TestTheOverviewNamesTheBot:
    """
    `--new-deployment` mints a fresh identity and stores no link to the one it replaced.

    Without the bot's name a deliberate restart reads as two unrelated deployments among all
    the others — which is exactly the situation an operator is in after resetting one.
    """

    def test_two_deployments_of_one_bot_stand_together(self, capsys):
        rows = [
            row('old1', '2026-07-12T05:15:00+00:00', deployment='deploy_old',
                scenario_set_name='dotusd_production'),
            row('new1', '2026-09-01T06:00:00+00:00', deployment='deploy_new',
                scenario_set_name='dotusd_production'),
            row('other', '2026-08-20T09:00:00+00:00', deployment='deploy_other',
                scenario_set_name='ethusd_production'),
        ]
        histories = build_deployment_histories(rows)
        render_deployment_list(summarize_deployments(
            histories, {k: None for k in histories}, {}))

        printed = capsys.readouterr().out
        lines = [line for line in printed.splitlines() if 'deploy_' in line and 'usd_production' in line]
        assert [l.split()[1] for l in lines] == ['ethusd_production', 'dotusd_production', 'dotusd_production'], (
            'the two deployments of one bot did not end up adjacent')
        assert '--new-deployment' in printed, 'the list does not say what two rows mean'


# A strategy repository's root, as a stand names it; invented like every value here.
STRATEGY = '/srv/home/user_algos/my_strategy'
# The moment the injected clock reads, twenty-two days after the change the cases below draw.
NOW = datetime(2026, 12, 31, 12, 0, tzinfo=timezone.utc)
FIRST_START = '2026-12-01T00:05:00+00:00'
SECOND_START = '2026-12-09T00:12:00+00:00'
THIRD_START = '2026-12-16T00:07:00+00:00'


def framework(commit: Optional[str] = 'a1b2c3d4aaaa', **overrides) -> RepositoryStand:
    """
    This repository's entry in a code stand.

    Args:
        commit: Its commit, None when unreadable
        overrides: Any other field the case cares about

    Returns:
        The entry, clean unless the case says otherwise
    """
    return RepositoryStand(repository='framework', commit=commit,
                           dirty=overrides.pop('dirty', False), **overrides)


def strategy(commit: Optional[str] = '2f054f5', **overrides) -> RepositoryStand:
    """
    A strategy repository's entry in a code stand.

    Args:
        commit: Its commit, None when unreadable
        overrides: Any other field the case cares about

    Returns:
        The entry, clean unless the case says otherwise
    """
    return RepositoryStand(repository=overrides.pop('repository', STRATEGY), commit=commit,
                           dirty=overrides.pop('dirty', False), **overrides)


def on_code(run_id: str, timestamp: str, *repositories: RepositoryStand, **overrides) -> RunResultRow:
    """
    One session's ledger row, with the code it started on.

    Args:
        run_id: The session's own identity
        timestamp: Its start
        repositories: Its code stand, one entry per repository
        overrides: Any other column the case cares about

    Returns:
        The typed row
    """
    return row(run_id, timestamp, code_stand=CodeStand(repositories=list(repositories)), **overrides)


def changes_between(older: CodeStand, newer: CodeStand) -> list:
    """
    The code changes the history draws on the later of two sessions.

    Args:
        older: The earlier session's stand
        newer: The later session's stand

    Returns:
        The later session's `code_changes`
    """
    return build_deployment_histories([
        row('older', FIRST_START, code_stand=older),
        row('newer', SECOND_START, code_stand=newer),
    ])[DEPLOYMENT][1].code_changes


def detail_of(rows: list, capsys) -> str:
    """
    What the detail view prints for a deployment's rows.

    Args:
        rows: The deployment's ledger rows
        capsys: The capture fixture

    Returns:
        The printed text
    """
    render_deployment_history(
        DEPLOYMENT, build_deployment_histories(rows)[DEPLOYMENT],
        deployment_comparability_advisory(rows),
        code_summary=deployment_code_summary(rows, NOW))
    return capsys.readouterr().out


class TestTwoSessionsOnDifferentCode:
    """
    A strategy loaded from another repository changes neither `git_commit` nor `git_dirty`, so the
    series broke unseen until the live-vs-backtest comparison at the end (#559).
    """

    ONLY_THE_STRATEGY_MOVED = [
        on_code('older', FIRST_START, framework(), strategy('2f054f5')),
        on_code('newer', SECOND_START, framework(),
                strategy('7c1e9a0', subject='Size the order from the equity',
                         recent_commits=['7c1e9a0', '2f054f5'])),
    ]

    def test_a_moved_strategy_repository_draws_the_line_and_the_warning(self, capsys):
        sessions = build_deployment_histories(self.ONLY_THE_STRATEGY_MOVED)[DEPLOYMENT]
        assert sessions[0].code_changes == []
        assert sessions[1].code_changes == [DeploymentCodeChange(
            repository_label='strategy', old_commit='2f054f5', new_commit='7c1e9a0',
            commits_between=1, change_direction=CodeChangeDirection.FORWARD,
            subject='Size the order from the equity')]

        printed = detail_of(self.ONLY_THE_STRATEGY_MOVED, capsys)
        assert printed.index('newer') < printed.index('code changed here') < printed.index('older')
        assert '    strategy   2f054f5 → 7c1e9a0    +1 commit   "Size the order from the equity"' \
            in printed.splitlines()
        assert 'Code changed 22 d ago' in printed

    def test_identical_stands_draw_neither(self, capsys):
        rows = [on_code('older', SECOND_START, framework(), strategy()),
                on_code('newer', THIRD_START, framework(), strategy())]
        assert build_deployment_histories(rows)[DEPLOYMENT][1].code_changes == []
        assert deployment_code_summary(rows, NOW) is None
        printed = detail_of(rows, capsys)
        assert 'code changed here' not in printed
        assert 'Code changed' not in printed

    def test_the_rows_read_as_drawn(self, capsys):
        rows = [
            on_code('older', SECOND_START, framework('a1b2c3d4aaaa'), strategy('2f054f5')),
            on_code('newer', THIRD_START,
                    framework('e5f6a7b8bbbb', subject='Fix the fee rounding',
                              recent_commits=['e5f6a7b8bbbb', '111111111111', '222222222222',
                                              'a1b2c3d4aaaa']),
                    strategy('7c1e9a0', subject='Size the order from the equity',
                             recent_commits=['7c1e9a0', '2f054f5'])),
        ]
        lines = detail_of(rows, capsys).splitlines()
        at = next(i for i, line in enumerate(lines) if line.startswith('··· code changed here ·'))
        assert lines[at + 1:at + 3] == [
            '    framework  a1b2c3d4 → e5f6a7b8  +3 commits  "Fix the fee rounding"',
            '    strategy   2f054f5 → 7c1e9a0    +1 commit   "Size the order from the equity"',
        ]
        assert lines[at + 3].startswith('older')

    def test_only_the_console_cuts_a_hash(self):
        changes = changes_between(
            CodeStand(repositories=[framework('a1b2c3d4aaaa')]),
            CodeStand(repositories=[framework('e5f6a7b8bbbb')]))
        assert (changes[0].old_commit, changes[0].new_commit) == ('a1b2c3d4aaaa', 'e5f6a7b8bbbb')

    def test_the_line_follows_the_configuration_separator(self, capsys):
        rows = [on_code('older', SECOND_START, framework()),
                on_code('newer', THIRD_START, framework('e5f6a7b8bbbb'), param_hash='strategy_v2')]
        printed = detail_of(rows, capsys)
        assert printed.index('STRATEGY CHANGED HERE') < printed.index('code changed here')
        assert printed.index('code changed here') < printed.index('older')

    def test_each_currency_compares_within_itself(self):
        sessions = build_deployment_histories([
            on_code('r1', FIRST_START, framework(), currency='BTC'),
            on_code('r2', SECOND_START, framework('e5f6a7b8bbbb'), currency='BTC'),
            on_code('r3', THIRD_START, framework(), currency='USD'),
        ])[DEPLOYMENT]
        by_run = {session.run_id: session for session in sessions}
        assert len(by_run['r2'].code_changes) == 1
        assert by_run['r3'].code_changes == [], (
            'the first session of a currency has no predecessor in it')

    def test_a_session_of_several_booking_periods_is_compared_once(self):
        def day(run_id: str, start: str, no: int, *repositories: RepositoryStand) -> RunResultRow:
            return on_code(run_id, start, *repositories, unit_name='session', period_no=no,
                           period_opened_at=f'2026-12-0{no}T00:00:00+00:00',
                           period_closed_at=f'2026-12-0{no + 1}T00:00:00+00:00',
                           period_close_reason='anchor')

        rows = [day('r1', FIRST_START, 1, framework()), day('r1', FIRST_START, 2, framework()),
                day('r2', SECOND_START, 3, framework('e5f6a7b8bbbb')),
                day('r2', SECOND_START, 4, framework('e5f6a7b8bbbb'))]
        sessions = build_deployment_histories(rows)[DEPLOYMENT]
        assert [len(session.code_changes) for session in sessions] == [0, 1]
        summary = deployment_code_summary(rows, NOW)
        assert (summary.code_stands, summary.sessions) == (2, 2)


class TestHowFarTheCodeMoved:
    """Where one stand's commit stands in the other's recorded history, and nothing else."""

    def test_a_rollback_reads_back_n_with_the_message_it_went_back_from(self):
        (change,) = changes_between(
            CodeStand(repositories=[strategy('7c1e9a0', subject='Size the order from the equity',
                                             recent_commits=['7c1e9a0', 'bbbbbbb', '2f054f5'])]),
            CodeStand(repositories=[strategy('2f054f5', subject='Keep the order size fixed',
                                             recent_commits=['2f054f5'])]))
        assert change.change_direction is CodeChangeDirection.BACK
        assert change.commits_between == 2
        assert change.subject == 'Size the order from the equity'

    def test_a_commit_missing_from_the_list_reads_unknown(self):
        """A clone too shallow to tell; a wrong number cannot come out of it."""
        (change,) = changes_between(
            CodeStand(repositories=[strategy('2f054f5', recent_commits=['2f054f5'])]),
            CodeStand(repositories=[strategy('7c1e9a0', subject='Size the order from the equity',
                                             recent_commits=['7c1e9a0'])]))
        assert change.change_direction is CodeChangeDirection.UNKNOWN
        assert change.commits_between is None
        assert change.subject == 'Size the order from the equity'

    def test_a_history_nobody_read_reads_unknown(self):
        (change,) = changes_between(CodeStand(repositories=[strategy('2f054f5')]),
                                    CodeStand(repositories=[strategy('7c1e9a0')]))
        assert change.change_direction is CodeChangeDirection.UNKNOWN

    def test_the_count_reads_a_longer_prefix_against_a_short_hash(self):
        (change,) = changes_between(
            CodeStand(repositories=[strategy('2f054f5')]),
            CodeStand(repositories=[strategy('7c1e9a0aaaaa',
                                             recent_commits=['7c1e9a0aaaaa', '2f054f5bbbbb'])]))
        assert (change.change_direction, change.commits_between) == (CodeChangeDirection.FORWARD, 1)

    def test_two_abbreviation_lengths_of_one_commit_are_no_change(self):
        rows = [on_code('r1', FIRST_START, strategy('5d9b4017')),
                on_code('r2', SECOND_START, strategy('5d9b401')),
                on_code('r3', THIRD_START, strategy('5d9b40171234'))]
        sessions = build_deployment_histories(rows)[DEPLOYMENT]
        assert [session.code_changes for session in sessions] == [[], [], []]
        assert deployment_code_summary(rows, NOW) is None

    def test_an_uncommitted_tree_is_a_change_with_no_count(self, capsys):
        rows = [on_code('older', SECOND_START, strategy('7c1e9a0')),
                on_code('newer', THIRD_START,
                        strategy('7c1e9a0', dirty=True, diff_hash='d1', subject='Unrelated'))]
        (change,) = build_deployment_histories(rows)[DEPLOYMENT][1].code_changes
        assert change.change_direction is CodeChangeDirection.UNCOMMITTED
        assert (change.commits_between, change.subject) == (None, None)
        assert '    strategy   7c1e9a0 → 7c1e9a0    uncommitted changes' \
            in detail_of(rows, capsys).splitlines()

    def test_the_same_uncommitted_changes_are_the_same_code(self):
        one = CodeStand(repositories=[strategy('7c1e9a0', dirty=True, diff_hash='d1')])
        assert changes_between(one, one) == []

    def test_other_uncommitted_changes_on_one_commit_are_a_change(self):
        (change,) = changes_between(
            CodeStand(repositories=[strategy('7c1e9a0', dirty=True, diff_hash='d1')]),
            CodeStand(repositories=[strategy('7c1e9a0', dirty=True, diff_hash='d2')]))
        assert change.change_direction is CodeChangeDirection.UNCOMMITTED

    def test_a_dirty_tree_with_no_digest_is_never_the_same(self):
        """Unknown is not clean: no digest says nothing about what the two trees held."""
        unread = CodeStand(repositories=[strategy('7c1e9a0', dirty=True)])
        clean = CodeStand(repositories=[strategy('7c1e9a0')])
        assert changes_between(unread, unread)[0].change_direction is CodeChangeDirection.UNCOMMITTED
        assert changes_between(clean, unread)[0].change_direction is CodeChangeDirection.UNCOMMITTED
        assert changes_between(unread, clean)[0].change_direction is CodeChangeDirection.UNCOMMITTED

    def test_an_unreadable_commit_is_its_own_stand_also_against_another(self, capsys):
        rows = [on_code('older', SECOND_START, framework(None, dirty=True)),
                on_code('newer', THIRD_START, framework(None, dirty=True))]
        (change,) = build_deployment_histories(rows)[DEPLOYMENT][1].code_changes
        assert change.change_direction is CodeChangeDirection.UNKNOWN
        assert deployment_code_summary(rows, NOW).code_stands == 2
        assert '    framework  unknown → unknown    +?' in detail_of(rows, capsys).splitlines()

    def test_an_unreadable_older_commit_is_not_read_as_a_new_repository(self, capsys):
        rows = [on_code('older', SECOND_START, framework(None, dirty=True)),
                on_code('newer', THIRD_START, framework('e5f6a7b8bbbb'))]
        lines = detail_of(rows, capsys).splitlines()
        assert '    framework  unknown → e5f6a7b8   +?' in lines
        assert not any('(new)' in line for line in lines)

    def test_an_unreadable_newer_commit_is_said(self, capsys):
        rows = [on_code('older', SECOND_START, framework('a1b2c3d4aaaa')),
                on_code('newer', THIRD_START, framework(None, dirty=True))]
        assert '    framework  a1b2c3d4 → unknown   +?' in detail_of(rows, capsys).splitlines()

    def test_a_repository_only_the_newer_session_used_reads_new(self, capsys):
        rows = [on_code('older', SECOND_START, framework()),
                on_code('newer', THIRD_START, framework(),
                        strategy('7c1e9a0', subject='First commit'))]
        (change,) = build_deployment_histories(rows)[DEPLOYMENT][1].code_changes
        assert (change.change_direction, change.old_commit) == (CodeChangeDirection.ADDED, None)
        assert f'    strategy   (new) → 7c1e9a0{" " * 18}"First commit"' \
            in detail_of(rows, capsys).splitlines()

    def test_a_repository_only_the_older_session_used_is_a_change_too(self, capsys):
        """Otherwise a strategy moved out of its repository would count as a stand and draw nothing."""
        rows = [on_code('older', SECOND_START, framework(), strategy('7c1e9a0')),
                on_code('newer', THIRD_START, framework())]
        (change,) = build_deployment_histories(rows)[DEPLOYMENT][1].code_changes
        assert (change.change_direction, change.new_commit) == (CodeChangeDirection.REMOVED, None)
        assert deployment_code_summary(rows, NOW).code_stands == 2
        assert '    strategy   7c1e9a0 → (gone)' in detail_of(rows, capsys).splitlines()

    def test_several_other_repositories_are_told_apart_by_their_directory(self, capsys):
        rows = [on_code('older', SECOND_START, strategy('2f054f5'),
                        strategy('aaaaaaa', repository='/srv/home/user_algos/my_helpers')),
                on_code('newer', THIRD_START, strategy('7c1e9a0'),
                        strategy('bbbbbbb', repository='/srv/home/user_algos/my_helpers'))]
        changes = build_deployment_histories(rows)[DEPLOYMENT][1].code_changes
        assert [c.repository_label for c in changes] == ['strategy my_strategy', 'strategy my_helpers']
        printed = detail_of(rows, capsys)
        assert '    strategy my_strategy  2f054f5 → 7c1e9a0    +?' in printed
        assert '/srv/home' not in printed

    def test_a_framework_moved_to_a_new_tree_is_the_same_repository(self):
        """Its stand names it `framework`: a deploy moves its root, and the code did not move."""
        one = CodeStand(repositories=[framework('a1b2c3d4aaaa')])
        assert changes_between(one, CodeStand(repositories=[framework('a1b2c3d4aaaa')])) == []

    def test_a_row_that_predates_the_column_draws_nothing(self, capsys):
        rows = [row('older', SECOND_START), on_code('newer', THIRD_START, framework())]
        assert [s.code_changes for s in build_deployment_histories(rows)[DEPLOYMENT]] == [[], []]
        assert deployment_code_summary(rows, NOW) is None
        printed = detail_of(rows, capsys)
        assert 'code changed here' not in printed and 'Code changed' not in printed

    def test_a_row_that_predates_the_column_hides_no_change_across_it(self):
        rows = [on_code('r1', FIRST_START, framework()), row('r2', SECOND_START),
                on_code('r3', THIRD_START, framework('e5f6a7b8bbbb'))]
        sessions = build_deployment_histories(rows)[DEPLOYMENT]
        assert [len(session.code_changes) for session in sessions] == [0, 0, 1]
        summary = deployment_code_summary(rows, NOW)
        assert summary.code_stands == 2
        assert summary.sessions == 3
        assert summary.last_change_at == datetime.fromisoformat(THIRD_START)


class TestEveryDirectionHasItsWords:
    """A direction with no branch would print nothing, with no symptom anywhere."""

    CASES = {
        CodeChangeDirection.FORWARD: ('a1b2c3d4', 'e5f6a7b8', 3, '+3 commits'),
        CodeChangeDirection.BACK: ('a1b2c3d4', 'e5f6a7b8', 2, 'back 2'),
        CodeChangeDirection.UNKNOWN: ('a1b2c3d4', 'e5f6a7b8', None, '+?'),
        CodeChangeDirection.UNCOMMITTED: ('a1b2c3d4', 'a1b2c3d4', None, 'uncommitted changes'),
        CodeChangeDirection.ADDED: (None, 'e5f6a7b8', None, '(new) → e5f6a7b8'),
        CodeChangeDirection.REMOVED: ('a1b2c3d4', None, None, 'a1b2c3d4 → (gone)'),
    }

    def test_the_cases_name_every_direction(self):
        assert set(self.CASES) == set(CodeChangeDirection)

    def test_a_single_commit_is_singular(self, capsys):
        sessions = [DeploymentSessionRow(
            index=1, run_id='r1', started=None, net_pnl=0.0, max_drawdown=None,
            max_drawdown_pct=None, currency='USD', code_changes=[DeploymentCodeChange(
                repository_label='strategy', old_commit='a', new_commit='b', commits_between=1,
                change_direction=CodeChangeDirection.FORWARD)])]
        render_deployment_history(DEPLOYMENT, sessions)
        assert '+1 commit' in capsys.readouterr().out

    def test_each_one_prints_its_form(self, capsys):
        for direction, (old, new, between, words) in self.CASES.items():
            sessions = [DeploymentSessionRow(
                index=1, run_id='r1', started=None, net_pnl=0.0, max_drawdown=None,
                max_drawdown_pct=None, currency='USD', code_changes=[DeploymentCodeChange(
                    repository_label='strategy', old_commit=old, new_commit=new,
                    commits_between=between, change_direction=direction)])]
            render_deployment_history(DEPLOYMENT, sessions)
            assert words in capsys.readouterr().out, direction


class TestTheCodeSummary:
    """Every figure the dated line shows is derived once, and `now` comes from the reader."""

    MOVED_ONCE = [
        on_code('r1', FIRST_START, framework()),
        on_code('r2', SECOND_START, framework('e5f6a7b8bbbb')),
        on_code('r3', THIRD_START, framework('e5f6a7b8bbbb')),
    ]

    def test_it_counts_the_stands_and_dates_the_last_change(self):
        summary = deployment_code_summary(self.MOVED_ONCE, NOW)
        assert summary.code_stands == 2
        assert summary.sessions == 3
        assert summary.last_change_at == datetime.fromisoformat(SECOND_START)
        assert summary.age_days == 22

    def test_the_warning_reads_as_drawn(self, capsys):
        assert ('⚠️  Code changed 22 d ago (2026-12-09) — 2 code stands over 3 sessions; net P&L '
                'adds them up') in detail_of(self.MOVED_ONCE, capsys).splitlines()

    def test_one_stand_and_one_session_raise_nothing(self):
        assert deployment_code_summary([on_code('r1', FIRST_START, framework())], NOW) is None
        assert deployment_code_summary([row('r1', FIRST_START)], NOW) is None

    def test_a_rollback_adds_a_change_and_no_stand(self):
        summary = deployment_code_summary([
            on_code('r1', FIRST_START, framework('a1b2c3d4aaaa')),
            on_code('r2', SECOND_START, framework('e5f6a7b8bbbb')),
            on_code('r3', THIRD_START, framework('a1b2c3d4aaaa')),
        ], NOW)
        assert summary.code_stands == 2
        assert summary.last_change_at == datetime.fromisoformat(THIRD_START)

    def test_a_two_currency_session_is_one_session(self):
        rows = [on_code('r1', FIRST_START, framework(), currency='USD'),
                on_code('r1', FIRST_START, framework(), currency='BTC'),
                on_code('r2', SECOND_START, framework('e5f6a7b8bbbb'), currency='USD'),
                on_code('r2', SECOND_START, framework('e5f6a7b8bbbb'), currency='BTC')]
        summary = deployment_code_summary(rows, NOW)
        assert (summary.code_stands, summary.sessions) == (2, 2)

    def test_a_start_nobody_can_read_dates_nothing(self, capsys):
        rows = [on_code('r1', FIRST_START, framework()),
                on_code('r2', 'not-a-timestamp', framework('e5f6a7b8bbbb'))]
        summary = deployment_code_summary(rows, NOW)
        assert summary.code_stands == 2
        assert (summary.last_change_at, summary.age_days) == (None, None)
        assert 'Code changed on an unknown date — 2 code stands' in detail_of(rows, capsys)

    def test_a_stored_start_with_no_zone_is_read_as_utc(self):
        rows = [on_code('r1', '2026-12-01T00:05:00', framework()),
                on_code('r2', '2026-12-09T00:12:00', framework('e5f6a7b8bbbb'))]
        summary = deployment_code_summary(rows, NOW)
        assert summary.last_change_at == datetime(2026, 12, 9, 0, 12, tzinfo=timezone.utc)
        assert summary.age_days == 22

    def test_a_start_ahead_of_the_reader_is_no_negative_age(self):
        summary = deployment_code_summary(self.MOVED_ONCE, datetime(2026, 12, 8, tzinfo=timezone.utc))
        assert summary.age_days == 0

    def test_a_half_year_old_change_prints_one_line_and_not_the_block(self, capsys):
        rows = [on_code('r1', FIRST_START, framework()),
                on_code('r2', '2027-01-12T00:05:00+00:00', framework('e5f6a7b8bbbb'))]
        render_deployment_history(
            DEPLOYMENT, build_deployment_histories(rows)[DEPLOYMENT],
            deployment_comparability_advisory(rows),
            code_summary=deployment_code_summary(
                rows, datetime(2027, 7, 12, 12, 0, tzinfo=timezone.utc)))
        printed = capsys.readouterr().out
        assert 'Code changed 181 d ago (2027-01-12) — 2 code stands over 2 sessions' in printed
        assert 'SPANS MORE THAN ONE CONFIGURATION' not in printed

    def test_the_warning_stands_above_the_block_and_the_table(self, capsys):
        rows = [on_code('r1', FIRST_START, framework()),
                on_code('r2', SECOND_START, framework('e5f6a7b8bbbb'), param_hash='strategy_v2')]
        printed = detail_of(rows, capsys)
        assert (printed.index('Code changed') < printed.index('SPANS MORE THAN ONE')
                < printed.index('run id'))


class TestAFailedStartIsNoCodeStand:
    """
    A start that failed writes a row with no currency, and its code never ran as a session.

    A deploy stepped back after a failed start is the documented case. Counted, it raised a warning
    that stays for good over a history that draws no line.
    """

    @staticmethod
    def _stepped_back() -> list:
        """
        A deploy that failed to start and was taken back.

        Returns:
            The rows: on one commit, a failed start on another, on the first one again
        """
        return [on_code('first', FIRST_START, framework(), strategy('2f054f5')),
                on_code('failed', SECOND_START, framework(), strategy('7c1e9a0'), currency=''),
                on_code('third', THIRD_START, framework(), strategy('2f054f5'))]

    def test_a_step_back_draws_nothing_and_warns_of_nothing(self, capsys):
        rows = self._stepped_back()
        assert deployment_code_summary(rows, NOW) is None
        sessions = build_deployment_histories(rows)[DEPLOYMENT]
        assert all(session.code_changes == [] for session in sessions)
        printed = detail_of(rows, capsys)
        assert 'Code changed' not in printed
        assert 'code changed here' not in printed

    def test_a_move_across_a_failed_start_is_still_a_move(self):
        rows = [on_code('first', FIRST_START, framework(), strategy('2f054f5')),
                on_code('failed', SECOND_START, framework(), strategy('7c1e9a0'), currency=''),
                on_code('third', THIRD_START, framework(), strategy('7c1e9a0'))]
        summary = deployment_code_summary(rows, NOW)
        assert summary.code_stands == 2
        assert summary.last_change_at == datetime.fromisoformat(THIRD_START)
        sessions = build_deployment_histories(rows)[DEPLOYMENT]
        assert [session.run_id for session in sessions if session.code_changes] == ['third']

    def test_two_failed_starts_draw_no_line_in_their_own_series(self):
        rows = [on_code('failed_one', FIRST_START, framework(), strategy('2f054f5'), currency=''),
                on_code('failed_two', SECOND_START, framework(), strategy('7c1e9a0'), currency='')]
        assert deployment_code_summary(rows, NOW) is None
        sessions = build_deployment_histories(rows)[DEPLOYMENT]
        assert all(session.code_changes == [] for session in sessions)

    def test_a_change_of_currency_is_no_move_no_line_shows(self):
        rows = [on_code('in_dollars', FIRST_START, framework(), strategy('2f054f5')),
                on_code('in_euros', SECOND_START, framework(), strategy('7c1e9a0'), currency='EUR')]
        assert deployment_code_summary(rows, NOW) is None

    def test_a_move_counts_once_over_two_currencies(self):
        rows = [on_code('older', FIRST_START, framework(), strategy('2f054f5'), currency=currency)
                for currency in ('USD', 'BTC')]
        rows += [on_code('newer', SECOND_START, framework(), strategy('7c1e9a0'), currency=currency)
                 for currency in ('USD', 'BTC')]
        summary = deployment_code_summary(rows, NOW)
        assert (summary.code_stands, summary.sessions) == (2, 2)
        assert summary.last_change_at == datetime.fromisoformat(SECOND_START)


class TestASessionNobodyCouldReadNamesNoRepository:
    """
    A session whose code identity nobody could read records this repository with no commit.

    Its other repositories are unknown, not absent: the words for a repository only one session
    used would claim something nobody knows.
    """

    UNREAD = (framework(None, dirty=True),)
    READ = (framework(), strategy('7c1e9a0'))

    def test_read_then_unread_says_unknown_and_not_gone(self, capsys):
        rows = [on_code('older', SECOND_START, *self.READ), on_code('newer', THIRD_START, *self.UNREAD)]
        changes = build_deployment_histories(rows)[DEPLOYMENT][1].code_changes
        assert [change.change_direction for change in changes] == [CodeChangeDirection.UNKNOWN] * 2
        lines = detail_of(rows, capsys).splitlines()
        assert '    strategy   7c1e9a0 → unknown    +?' in lines
        assert not any('(gone)' in line for line in lines)

    def test_unread_then_read_says_unknown_and_not_new(self, capsys):
        rows = [on_code('older', SECOND_START, *self.UNREAD), on_code('newer', THIRD_START, *self.READ)]
        changes = build_deployment_histories(rows)[DEPLOYMENT][1].code_changes
        assert [change.change_direction for change in changes] == [CodeChangeDirection.UNKNOWN] * 2
        lines = detail_of(rows, capsys).splitlines()
        assert '    strategy   unknown → 7c1e9a0    +?' in lines
        assert not any('(new)' in line for line in lines)

    def test_a_stand_with_a_second_repository_is_not_unread(self):
        assert not CodeStand(repositories=[framework(None, dirty=True), strategy()]).is_unread()
        assert not CodeStand(repositories=[framework()]).is_unread()
        assert CodeStand(repositories=list(self.UNREAD)).is_unread()


class TestTheDetailViewSpeaksForTheWholeDeployment:
    """The heading and the footer read no single row: the first one may be a failed start."""

    def test_a_failed_start_does_not_move_since_or_blank_the_currency(self, capsys):
        rows = [on_code('first', FIRST_START, framework()),
                on_code('failed', SECOND_START, framework(), currency=''),
                on_code('third', THIRD_START, framework())]
        printed = detail_of(rows, capsys)
        assert '3 session(s) · since 2026-12-01 00:05 UTC' in printed
        assert 'Amounts in USD. ' in printed

    def test_two_currencies_are_named_and_a_session_counts_once(self, capsys):
        rows = [row(run_id, start, currency=currency)
                for run_id, start in (('first', FIRST_START), ('second', SECOND_START))
                for currency in ('USD', 'BTC')]
        printed = detail_of(rows, capsys)
        assert '2 session(s) · since' in printed
        assert 'Amounts in BTC, USD. ' in printed
        table = [line for line in printed.splitlines() if line.startswith(('first', 'second'))]
        assert len(table) == 4
        assert sum(' BTC' in line for line in table) == 2
        assert sum(' USD' in line for line in table) == 2

    def test_one_currency_is_not_repeated_on_every_row(self, capsys):
        printed = detail_of([row('first', FIRST_START)], capsys)
        line = next(line for line in printed.splitlines() if line.startswith('first'))
        assert not line.rstrip().endswith('USD')

    def test_a_deployment_that_only_failed_names_no_currency(self, capsys):
        printed = detail_of([row('failed', FIRST_START, currency='')], capsys)
        assert 'Amounts in —. ' in printed


class TestTheListSaysWhichMoved:
    """`changed` keeps one bit for a reader scanning; the mark tells the two reasons apart."""

    MOVED_CONFIGURATION = [row('r1', FIRST_START),
                           row('r2', SECOND_START, param_hash='strategy_v2')]
    MOVED_CODE = [on_code('r1', FIRST_START, framework()),
                  on_code('r2', SECOND_START, framework('e5f6a7b8bbbb'))]
    MOVED_BOTH = [on_code('r1', FIRST_START, framework()),
                  on_code('r2', SECOND_START, framework('e5f6a7b8bbbb'), param_hash='strategy_v2')]
    MOVED_NEITHER = [row('r1', FIRST_START), row('r2', SECOND_START)]

    @staticmethod
    def _summary(rows: list) -> DeploymentSummary:
        return summarize_deployments(
            build_deployment_histories(rows),
            {DEPLOYMENT: deployment_comparability_advisory(rows)},
            {DEPLOYMENT: deployment_code_summary(rows, NOW)})[0]

    def _printed(self, rows: list, capsys) -> tuple:
        render_deployment_list([self._summary(rows)])
        printed = capsys.readouterr().out
        line = next(text for text in printed.splitlines() if text.startswith(DEPLOYMENT))
        return line, printed

    def test_the_three_forms_of_the_mark(self, capsys):
        assert self._printed(self.MOVED_CONFIGURATION, capsys)[0].endswith(' ⚠')
        assert self._printed(self.MOVED_CODE, capsys)[0].endswith(' ⚠ code 22 d ago')
        assert self._printed(self.MOVED_BOTH, capsys)[0].endswith(' ⚠ · code 22 d ago')
        assert '⚠' not in self._printed(self.MOVED_NEITHER, capsys)[0]

    def test_a_change_of_code_alone_is_changed_with_no_advisory(self):
        summary = self._summary(self.MOVED_CODE)
        assert deployment_comparability_advisory(self.MOVED_CODE) is None
        assert (summary.changed, summary.configuration_changed) == (True, False)
        assert summary.code_summary.code_stands == 2

    def test_a_change_of_configuration_alone_carries_no_code_summary(self):
        summary = self._summary(self.MOVED_CONFIGURATION)
        assert (summary.changed, summary.configuration_changed) == (True, True)
        assert summary.code_summary is None

    def test_nothing_moved_is_not_changed(self):
        summary = self._summary(self.MOVED_NEITHER)
        assert (summary.changed, summary.configuration_changed) == (False, False)

    def test_the_legend_names_only_the_marks_on_the_page(self, capsys):
        _, printed = self._printed(self.MOVED_CODE, capsys)
        assert '⚠ code N d ago =' in printed
        assert '⚠ = the sessions were not all produced' not in printed
        _, printed = self._printed(self.MOVED_CONFIGURATION, capsys)
        assert '⚠ code N d ago =' not in printed
        assert '⚠ = the sessions were not all produced' in printed

    def test_a_change_nobody_can_date_still_says_code(self, capsys):
        rows = [on_code('r1', FIRST_START, framework()),
                on_code('r2', 'not-a-timestamp', framework('e5f6a7b8bbbb'))]
        assert self._printed(rows, capsys)[0].endswith(' ⚠ code')


class TestTheCommandCarriesIt:
    """The list and the detail both reach the code summary through the command."""

    ROWS = [on_code('older', FIRST_START, framework()),
            on_code('newer', SECOND_START, framework('e5f6a7b8bbbb'))]

    def _run(self, deployment: str, capsys) -> str:
        ledger = MagicMock()
        ledger.read_rows.return_value = self.ROWS
        index = MagicMock()
        index.list_runs.return_value = []
        with patch('python.cli.run_index_cli.RunResultsLedger', return_value=ledger), \
                patch('python.cli.run_index_cli.RunIndex', return_value=index):
            assert RunIndexCli().cmd_deployments(deployment) == 0
        return capsys.readouterr().out

    def test_the_list(self, capsys):
        assert ' ⚠ code ' in self._run('', capsys)

    def test_the_detail(self, capsys):
        printed = self._run(DEPLOYMENT, capsys)
        assert '2 code stands over 2 sessions' in printed
        assert 'code changed here' in printed
