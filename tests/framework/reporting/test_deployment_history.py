"""
Reading a restarted bot's ledger rows back as ONE history (#497).

A thirty-day live run restarts — #476 rehearses it on purpose — and every restart writes its
own ledger row under its own run id. Until the deployment identity those rows were a pile: the
ledger's only other reader filters on `sweep_id`, which a live session does not have, so a live
row was written and unreachable (§44 calls that a store with no read path).

What is pinned here is the GROUPING and what it refuses to do. It groups, it orders, it names
the gaps and the two kinds of change — and it judges none of them. Whether eleven hours between
two sessions was acceptable is a statement about the market and the operator's night, not about
the data, and a threshold here would quietly turn a record into a verdict.

The drawdown column deserves its own warning and gets one below: a live row carries the RUNNING
figure against the inherited peak, so the reduction over a deployment is max(), and a sum
double-counts every session's share of the same decline.
"""

from python.framework.reporting.console.deployment_history_summary import (
    build_deployment_histories,
    deployment_comparability_advisory,
    render_deployment_history,
    render_deployment_list,
    summarize_deployments,
)
from python.framework.types.api.report_types import RunResultRow

DEPLOYMENT = 'deploy_20260901_060000_ab12'


def row(run_id: str, timestamp: str, deployment: str = DEPLOYMENT, **overrides) -> RunResultRow:
    """
    One ledger row as a live session writes it.

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
        summaries = summarize_deployments(histories, {DEPLOYMENT: None})
        assert len(summaries) == 2
        assert {s.currency for s in summaries} == {'USD', 'BTC'}
        assert all(s.sessions == 1 for s in summaries)


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
        summary = summarize_deployments(histories, {DEPLOYMENT: None})[0]

        assert summary.sessions == 3
        assert summary.net_pnl == 30.0
        assert summary.max_drawdown == -450.0, 'the drawdown was summed instead of maxed'

    def test_it_marks_a_deployment_whose_configuration_moved(self):
        rows = [row('r1', '2026-09-01T06:00:00+00:00'),
                row('r2', '2026-09-02T06:00:00+00:00', param_hash='strategy_v2')]
        histories = build_deployment_histories(rows)
        advisories = {DEPLOYMENT: deployment_comparability_advisory(rows)}

        assert summarize_deployments(histories, advisories)[0].changed is True

    def test_the_list_points_at_the_detail_view(self, capsys):
        """An overview that does not say how to descend is a dead end."""
        histories = build_deployment_histories([row('r1', '2026-09-01T06:00:00+00:00')])
        render_deployment_list(summarize_deployments(histories, {DEPLOYMENT: None}))
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
        """The id is what opens `runs/live/<profile>/<run id>/`; a position number is not."""
        sessions = build_deployment_histories(
            [row('20260916_060500_8e10', '2026-09-16T06:05:00+00:00')])[DEPLOYMENT]
        render_deployment_history(DEPLOYMENT, sessions)

        printed = capsys.readouterr().out
        assert '20260916_060500_8e10' in printed
        assert 'runs/live/<profile>/<run id>/' in printed

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
                scenario_set_name='dotusd_live'),
            row('new1', '2026-09-01T06:00:00+00:00', deployment='deploy_new',
                scenario_set_name='dotusd_live'),
            row('other', '2026-08-20T09:00:00+00:00', deployment='deploy_other',
                scenario_set_name='ethusd_live'),
        ]
        histories = build_deployment_histories(rows)
        render_deployment_list(summarize_deployments(
            histories, {k: None for k in histories}))

        printed = capsys.readouterr().out
        lines = [line for line in printed.splitlines() if 'deploy_' in line and 'usd_live' in line]
        assert [l.split()[1] for l in lines] == ['ethusd_live', 'dotusd_live', 'dotusd_live'], (
            'the two deployments of one bot did not end up adjacent')
        assert '--new-deployment' in printed, 'the list does not say what two rows mean'
