"""Optimization analysis tests (#390) — ranking + one-factor sensitivity (typed rows)."""

import pytest

from python.framework.optimization.optimization_analysis import rank, sensitivity


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
