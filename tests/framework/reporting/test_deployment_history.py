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
    render_deployment_history,
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
        net_pnl=0.0, account_max_drawdown=0.0, account_max_drawdown_pct=0.0)
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
    """One run writes one row PER CURRENCY, and a history counts sessions."""

    def test_a_multi_currency_session_appears_once(self):
        """
        Counting rows instead of runs would report a two-currency bot as twice-restarted —
        and every gap between its sessions as zero.
        """
        histories = build_deployment_histories([
            row('r1', '2026-09-01T06:00:00+00:00', currency='USD'),
            row('r1', '2026-09-01T06:00:00+00:00', currency='BTC'),
        ])
        assert len(histories[DEPLOYMENT]) == 1


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
        assert 'gap 2.0 d' in printed
        assert 'strategy changed' in printed
        assert 'operation changed' in printed

    def test_the_drawdown_column_says_it_is_cumulative(self, capsys):
        """
        Without the footnote the column reads as a per-session figure, and the natural
        thing to do with a per-session column is add it up.
        """
        sessions = build_deployment_histories([row('r1', '2026-09-01T06:00:00+00:00')])[DEPLOYMENT]
        render_deployment_history(DEPLOYMENT, sessions)
        assert 'CUMULATIVE' in capsys.readouterr().out
