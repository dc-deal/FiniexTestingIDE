"""Optimization analysis tests (#390) — ranking + one-factor sensitivity (typed rows)."""

from datetime import datetime, timezone

import pytest

from python.framework.optimization.optimization_analysis import (
    degenerate_ranking_advisory,
    mixed_logic_version_advisory,
    rank,
    sensitivity,
    summarize_sweeps,
)
from python.framework.reporting.store.run_ledger_index import RunLedgerIndex


@pytest.fixture
def sweep_rows(tmp_ledger, make_run_summary, make_provenance):
    """A 4-row sweep ledger (2x2 grid sl_pips × tp_pips), read back as typed RunResultRows."""
    rows = [
        ({'decision_logic_config.sl_pips': 100, 'decision_logic_config.tp_pips': 200}, -10.0),
        ({'decision_logic_config.sl_pips': 100, 'decision_logic_config.tp_pips': 300}, -5.0),
        ({'decision_logic_config.sl_pips': 150, 'decision_logic_config.tp_pips': 200}, 4.0),
        ({'decision_logic_config.sl_pips': 150, 'decision_logic_config.tp_pips': 300}, 9.0),
    ]
    for i, (params, pnl) in enumerate(rows):
        tmp_ledger.append(
            make_run_summary(net_pnl=pnl),
            make_provenance(param_hash=f'h{i}', run_id=f'r{i}',
                            scenario_set_name=f's__c{i:03d}',
                            sweep_id='sweep_X', sweep_params=params))
    return tmp_ledger.read_rows(sweep_id='sweep_X')


def test_rank_maximize_best_first(sweep_rows):
    """Maximizing net_pnl puts the highest first."""
    ranked = rank(sweep_rows, 'net_pnl', maximize=True)
    assert [r.net_pnl for r in ranked] == [9.0, 4.0, -5.0, -10.0]


def test_rank_minimize(sweep_rows):
    """Minimizing puts the lowest first (e.g. for a drawdown objective)."""
    ranked = rank(sweep_rows, 'net_pnl', maximize=False)
    assert [r.net_pnl for r in ranked] == [-10.0, -5.0, 4.0, 9.0]


def test_rank_deterministic(sweep_rows):
    """Ranking the same rows twice gives the same order (pairs with #368)."""
    a = rank(sweep_rows, 'net_pnl', maximize=True)
    b = rank(sweep_rows, 'net_pnl', maximize=True)
    assert [r.run_id for r in a] == [r.run_id for r in b]


def test_rank_unknown_objective_raises(sweep_rows):
    """An objective that is not a RunResultRow field is a hard error."""
    with pytest.raises(ValueError):
        rank(sweep_rows, 'nonexistent_kpi', maximize=True)


def test_rank_rejects_an_objective_that_can_be_undefined(sweep_rows):
    """A KPI that may be None cannot order a ranking — the comparison would raise."""
    for objective in ('profit_factor', 'avg_win_r', 'avg_loss_r', 'signal_fresh_ratio'):
        with pytest.raises(ValueError, match='cannot produce a total ranking'):
            rank(sweep_rows, objective, maximize=True)


def test_rank_keeps_the_always_measured_kpis(sweep_rows):
    """The sweep objective and the P&L KPI stay rankable — this guards the exclusion."""
    assert rank(sweep_rows, 'expectancy', maximize=True)
    assert rank(sweep_rows, 'net_pnl', maximize=True)


def test_rows_are_typed(sweep_rows):
    """read_rows returns typed RunResultRows with parsed sweep_params (not DataFrame cells)."""
    from python.framework.types.api.report_types import RunResultRow
    assert all(isinstance(r, RunResultRow) for r in sweep_rows)
    assert all(isinstance(r.sweep_params, dict) for r in sweep_rows)


def test_sensitivity_ranks_by_influence(sweep_rows):
    """sl_pips moves net_pnl more than tp_pips → higher influence, ranked first."""
    # sl_pips levels: 100→mean(-10,-5)=-7.5, 150→mean(4,9)=6.5  → spread 14.0
    # tp_pips levels: 200→mean(-10,4)=-3.0, 300→mean(-5,9)=2.0  → spread 5.0
    sens = sensitivity(sweep_rows, 'net_pnl')
    assert sens[0].param == 'decision_logic_config.sl_pips'
    assert sens[0].influence == pytest.approx(14.0)
    assert sens[1].param == 'decision_logic_config.tp_pips'
    assert sens[1].influence == pytest.approx(5.0)


def test_sensitivity_level_means(sweep_rows):
    """Each parameter reports the mean objective per level."""
    sens = {s.param: s for s in sensitivity(sweep_rows, 'net_pnl')}
    sl = sens['decision_logic_config.sl_pips']
    assert sl.level_means['100'] == pytest.approx(-7.5)
    assert sl.level_means['150'] == pytest.approx(6.5)


def test_error_rows_excluded_from_ranking(tmp_ledger, make_run_summary, make_provenance):
    """Error-flagged rows are recorded but never rank or feed the sensitivity (#1)."""
    tmp_ledger.append(make_run_summary(net_pnl=5.0),
                      make_provenance(run_id='ok1', sweep_id='s',
                                      sweep_params={'decision_logic_config.x': 1}))
    tmp_ledger.append(make_run_summary(net_pnl=9.0),
                      make_provenance(run_id='ok2', sweep_id='s',
                                      sweep_params={'decision_logic_config.x': 2}))
    tmp_ledger.append(make_run_summary(),
                      make_provenance(run_id='bad', sweep_id='s',
                                      sweep_params={'decision_logic_config.x': 3},
                                      status='error', error='x out of range'))

    rows = tmp_ledger.read_rows(sweep_id='s')
    assert len(rows) == 3                                   # all recorded (incl. the error)

    ranked = rank(rows, 'net_pnl', maximize=True)
    assert [r.run_id for r in ranked] == ['ok2', 'ok1']    # the error row is excluded
    sens = sensitivity(rows, 'net_pnl')
    # only the two ok levels (x=1, x=2) contribute; x=3 (error) never appears
    assert all(set(s.level_means) == {'1', '2'} for s in sens)


def _utc(h, m, s):
    return datetime(2026, 1, 1, h, m, s, tzinfo=timezone.utc)


def test_summarize_sweeps(tmp_ledger, make_run_summary, make_provenance):
    """summarize_sweeps groups rows per sweep with start/duration, run counts, algo, objective."""
    # Sweep A: 2 ok runs, 10s apart, objective net_pnl
    tmp_ledger.append(make_run_summary(net_pnl=1.0), make_provenance(
        run_id='a0', scenario_set_name='base__sweep_A_c000', sweep_id='sweep_A',
        sweep_params={'decision_logic_config.x': 1}, sweep_objective='net_pnl',
        sweep_maximize=True, run_timestamp=_utc(0, 0, 0)))
    tmp_ledger.append(make_run_summary(net_pnl=2.0), make_provenance(
        run_id='a1', scenario_set_name='base__sweep_A_c001', sweep_id='sweep_A',
        sweep_params={'decision_logic_config.x': 2}, sweep_objective='net_pnl',
        sweep_maximize=True, run_timestamp=_utc(0, 0, 10)))
    # Sweep B: 1 ok + 1 error, objective expectancy (minimize)
    tmp_ledger.append(make_run_summary(net_pnl=5.0), make_provenance(
        run_id='b0', scenario_set_name='base__sweep_B_c000', sweep_id='sweep_B',
        sweep_params={'decision_logic_config.x': 1}, sweep_objective='expectancy',
        sweep_maximize=False, run_timestamp=_utc(1, 0, 0)))
    tmp_ledger.append(make_run_summary(), make_provenance(
        run_id='b1', scenario_set_name='base__sweep_B_c001', sweep_id='sweep_B',
        sweep_params={'decision_logic_config.x': 9}, status='error', error='x out of range',
        sweep_objective='expectancy', sweep_maximize=False, run_timestamp=_utc(1, 0, 5)))

    summaries = {s.sweep_id: s for s in summarize_sweeps(tmp_ledger.read_rows())}
    assert set(summaries) == {'sweep_A', 'sweep_B'}

    a = summaries['sweep_A']
    assert (a.run_count, a.ok_count, a.error_count) == (2, 2, 0)
    assert a.duration_s == 10.0
    assert a.base_config == 'base'                          # per-combo sweep tag stripped
    assert a.objective == 'net_pnl' and a.maximize is True
    assert a.decision_logic_type == 'CORE/aggressive_trend'

    b = summaries['sweep_B']
    assert (b.run_count, b.ok_count, b.error_count) == (2, 1, 1)
    assert b.maximize is False


def test_summarize_sweeps_ignores_non_sweep_runs(tmp_ledger, make_run_summary, make_provenance):
    """A plain (non-sweep) run is not a sweep → never appears in the sweep list."""
    tmp_ledger.append(make_run_summary(), make_provenance(run_id='plain'))  # no sweep_id
    assert summarize_sweeps(tmp_ledger.read_rows()) == []


class TestARankingWhoseWinnerNeverTraded:
    """
    A combination that never opened a position scores perfectly on every measure of loss.

    Pardo names it for drawdown: *"minimum drawdown is not enough as a sole criterion, since
    a drawdown of zero occurs when a model has no losing trades and possibly no winning
    trades."* Making the measure honest does not help — a model that did nothing genuinely
    has no drawdown, and minimising genuinely prefers it.

    So the detector names no KPI. The ranking is misleading exactly when its WINNER did
    nothing, whatever it was ranked by, and that is what is tested here (#497).
    """

    @pytest.fixture
    def drawdown_sweep(self, tmp_ledger, make_run_summary, make_provenance):
        """Two combinations that never traded, two that did — ranked by drawdown ascending."""
        combos = [
            ({'decision_logic_config.min_confidence': 0.99}, 0.0, 0),
            ({'decision_logic_config.min_confidence': 0.95}, 0.0, 0),
            ({'decision_logic_config.min_confidence': 0.60}, 412.5, 38),
            ({'decision_logic_config.min_confidence': 0.40}, 980.0, 71),
        ]
        for i, (params, dd, trades) in enumerate(combos):
            tmp_ledger.append(
                make_run_summary(max_drawdown=dd, total_trades=trades, net_pnl=trades * 1.5),
                make_provenance(param_hash=f'd{i}', run_id=f'd{i}',
                                scenario_set_name=f's__c{i:03d}',
                                sweep_id='sweep_D', sweep_params=params))
        return tmp_ledger.read_rows(sweep_id='sweep_D')

    def test_it_fires_and_names_what_the_reader_needs(self, drawdown_sweep):
        ranked = rank(drawdown_sweep, 'account_max_drawdown', maximize=False)
        advisory = degenerate_ranking_advisory(ranked, 'account_max_drawdown')

        assert advisory is not None, (
            'the two zero-trade combinations rank first on a minimised drawdown — if this '
            'passes silently, the sweep recommends the strategy that does not trade')
        assert advisory.zero_trade_count == 2
        assert len(advisory.zero_trade_leaders) == 2
        assert advisory.best_trading_row is not None
        assert advisory.best_trading_row.total_trades == 38, (
            'the shallowest drawdown among those that traded is the row the reader wants')
        assert advisory.best_trading_rank == 3
        assert advisory.total_ranked == 4

    def test_it_stays_silent_when_the_winner_traded(self, drawdown_sweep):
        """The guard against a warning that cries on every sweep — it must be rare."""
        ranked = rank(drawdown_sweep, 'net_pnl', maximize=True)

        assert degenerate_ranking_advisory(ranked, 'net_pnl') is None

    def test_it_names_no_KPI_of_its_own(self, drawdown_sweep):
        """
        The condition is 'the winner did nothing', not 'the objective was account_max_drawdown'.

        Minimising net_pnl is a different objective with the same failure, and a detector
        keyed on a list of KPI names would miss it.
        """
        ranked = rank(drawdown_sweep, 'total_trades', maximize=False)

        assert degenerate_ranking_advisory(ranked, 'total_trades') is not None

    def test_a_sweep_where_nothing_traded_says_so(
            self, tmp_ledger, make_run_summary, make_provenance):
        for i in range(3):
            tmp_ledger.append(
                make_run_summary(max_drawdown=0.0, total_trades=0),
                make_provenance(param_hash=f'z{i}', run_id=f'z{i}',
                                scenario_set_name=f's__c{i:03d}', sweep_id='sweep_Z',
                                sweep_params={'decision_logic_config.min_confidence': 0.99}))
        ranked = rank(tmp_ledger.read_rows(sweep_id='sweep_Z'), 'account_max_drawdown', maximize=False)

        advisory = degenerate_ranking_advisory(ranked, 'account_max_drawdown')

        assert advisory is not None
        assert advisory.best_trading_row is None, (
            'there is no row to point the reader at, and the message has to say that rather '
            'than leaving the field blank')
        assert advisory.zero_trade_count == 3

    def test_an_empty_ranking_is_not_a_warning(self):
        assert degenerate_ranking_advisory([], 'account_max_drawdown') is None


class TestARankingThatSpansLogicVersions:
    """
    A ledger column can keep its name while the measure behind it changes.

    That is not hypothetical: the account drawdown changed from "the largest decline across
    closed trades" to "the largest decline of the equity curve" under a stable column name,
    and nothing in a fragment said which one it held. A ranking across that boundary is
    best-first over entries that answer different questions, and it looks exactly like a
    valid ranking — which is why the row carries its producing version (#497).
    """

    def _sweep(self, tmp_ledger, make_run_summary, make_provenance, versions):
        for i, version in enumerate(versions):
            tmp_ledger.append(
                make_run_summary(net_pnl=float(i)),
                make_provenance(param_hash=f'v{i}', run_id=f'v{i}',
                                scenario_set_name=f's__c{i:03d}', sweep_id='sweep_V',
                                sweep_params={'decision_logic_config.sl_pips': 10 + i}))
        rows = tmp_ledger.read_rows(sweep_id='sweep_V')
        for row, version in zip(rows, versions):
            row.logic_version = version
        return rows

    def test_one_version_is_not_a_warning(self, tmp_ledger, make_run_summary, make_provenance):
        rows = self._sweep(tmp_ledger, make_run_summary, make_provenance, [3, 3, 3])

        assert mixed_logic_version_advisory(rows) is None

    def test_two_versions_are(self, tmp_ledger, make_run_summary, make_provenance):
        rows = self._sweep(tmp_ledger, make_run_summary, make_provenance, [2, 3, 3])

        advisory = mixed_logic_version_advisory(rows)

        assert advisory is not None
        assert advisory.versions == [2, 3]
        assert advisory.counts == [1, 2]
        assert advisory.unknown_count == 0

    def test_an_unversioned_row_sorts_first_and_is_counted_as_unknown(
            self, tmp_ledger, make_run_summary, make_provenance):
        """
        None means the version was never recorded, not that the row is old.

        Sorting it first is what a reader needs: it is the entry nothing can resolve
        automatically, so it is the one that decides whether the ranking is usable.
        """
        rows = self._sweep(tmp_ledger, make_run_summary, make_provenance, [3, None, 3])

        advisory = mixed_logic_version_advisory(rows)

        assert advisory is not None
        assert advisory.versions == [None, 3]
        assert advisory.unknown_count == 1

    def test_an_empty_ranking_is_not_a_warning(self):
        assert mixed_logic_version_advisory([]) is None


class TestTheLedgerStampsTheVersionItWroteWith:
    """The column is only worth having if the writer fills it — and from ONE source."""

    def test_a_freshly_written_row_carries_the_index_logic_version(
            self, tmp_ledger, make_run_summary, make_provenance):
        tmp_ledger.append(make_run_summary(net_pnl=1.0), make_provenance())

        row = tmp_ledger.read_rows()[0]

        assert row.logic_version == RunLedgerIndex.LOGIC_VERSION, (
            'the row and the index must be stamped from the same constant, or the two '
            'disagree about which logic produced the data the index describes')
