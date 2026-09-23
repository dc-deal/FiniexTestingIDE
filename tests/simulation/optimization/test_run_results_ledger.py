"""Run-results ledger tests (#390) — append per run + read all + filter."""

from pathlib import Path

import pandas as pd

from python.framework.reporting.store.run_results_ledger import (
    COLUMN_REDUCTION,
    LEDGER_COLUMNS,
)
from python.framework.types.api.report_types import RunResultRow
from python.framework.types.log_layout_types import RUN_TYPE_LIVE, RUN_TYPE_SIMULATION
from python.framework.types.run_results_types import Reduction

# Every report artifact names its run (#475); the value is opaque to these tests.
_RUN_ID = '20260830_120000_a1b2c3d4'


def test_append_then_read_roundtrip(tmp_ledger, make_run_summary, make_provenance):
    """A run appends one row per currency; read returns it with the KPIs intact."""
    rs = make_run_summary(net_pnl=-76.98, expectancy=-0.125, total_trades=10)
    prov = make_provenance(param_hash='abc', run_id='20260101_000001')
    tmp_ledger.append(rs, prov)

    df = tmp_ledger.read()
    assert len(df) == 1
    row = df.iloc[0]
    assert row['param_hash'] == 'abc'
    assert row['net_pnl'] == -76.98
    assert row['expectancy'] == -0.125
    assert row['total_trades'] == 10
    assert list(df.columns) == LEDGER_COLUMNS


def test_one_fragment_per_run(tmp_ledger, make_run_summary, make_provenance):
    """Distinct runs write distinct fragments; read unions them into one table."""
    tmp_ledger.append(make_run_summary(net_pnl=1.0),
                      make_provenance(run_id='r1', scenario_set_name='s__c000'))
    tmp_ledger.append(make_run_summary(net_pnl=2.0),
                      make_provenance(run_id='r2', scenario_set_name='s__c001'))
    df = tmp_ledger.read()
    assert len(df) == 2
    assert set(df['net_pnl']) == {1.0, 2.0}


def test_same_timestamp_distinct_scenario_set_no_overwrite(
        tmp_ledger, make_run_summary, make_provenance):
    """Two combos finishing in the same second do not overwrite each other."""
    tmp_ledger.append(make_run_summary(net_pnl=1.0),
                      make_provenance(run_id='20260101_000000', scenario_set_name='s__c000'))
    tmp_ledger.append(make_run_summary(net_pnl=2.0),
                      make_provenance(run_id='20260101_000000', scenario_set_name='s__c001'))
    assert len(tmp_ledger.read()) == 2


def test_filter_by_sweep_id(tmp_ledger, make_run_summary, make_provenance):
    """read(sweep_id=...) keeps only that sweep's rows."""
    tmp_ledger.append(make_run_summary(),
                      make_provenance(run_id='r1', sweep_id='sweep_A'))
    tmp_ledger.append(make_run_summary(),
                      make_provenance(run_id='r2', sweep_id='sweep_B'))
    assert len(tmp_ledger.read(sweep_id='sweep_A')) == 1
    assert len(tmp_ledger.read(sweep_id='missing')) == 0


def test_read_empty_ledger(tmp_ledger):
    """Reading a ledger with no fragments returns an empty table with the schema."""
    df = tmp_ledger.read()
    assert df.empty
    assert list(df.columns) == LEDGER_COLUMNS


def test_sweep_params_persisted_as_json(tmp_ledger, make_run_summary, make_provenance):
    """The combination's grid point round-trips as a JSON string column."""
    import json
    prov = make_provenance(sweep_id='s', sweep_params={'decision_logic_config.sl_pips': 100})
    tmp_ledger.append(make_run_summary(), prov)
    row = tmp_ledger.read().iloc[0]
    assert json.loads(row['sweep_params']) == {'decision_logic_config.sl_pips': 100}


def test_read_rows_typed(tmp_ledger, make_run_summary, make_provenance):
    """read_rows returns typed RunResultRows with the JSON columns parsed back to structures."""
    tmp_ledger.append(
        make_run_summary(currency='USD', net_pnl=-76.98, total_trades=10),
        make_provenance(param_hash='abc', run_id='r1', sweep_id='s',
                        sweep_params={'decision_logic_config.sl_pips': 100}))
    rows = tmp_ledger.read_rows()
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, RunResultRow)
    assert row.param_hash == 'abc'
    assert row.net_pnl == -76.98
    assert row.total_trades == 10                       # int, not numpy/float
    assert row.sweep_params == {'decision_logic_config.sl_pips': 100}   # parsed dict
    assert row.worker_versions == {'rsi_fast': '1.0.0'}                 # parsed dict
    assert row.symbols == ['BTCUSD']                                    # parsed list


def test_read_rows_nullable_fields(tmp_ledger, make_run_summary, make_provenance):
    """A non-sweep run reads back with sweep_id/sweep_params = None (not NaN)."""
    tmp_ledger.append(make_run_summary(), make_provenance(run_id='r2'))  # no sweep tag
    row = tmp_ledger.read_rows()[0]
    assert row.sweep_id is None
    assert row.sweep_params is None
    assert row.sweep_objective is None
    assert row.sweep_maximize is None


def test_sweep_objective_persisted(tmp_ledger, make_run_summary, make_provenance):
    """The sweep spec's objective + direction round-trip so report can default to them."""
    tmp_ledger.append(make_run_summary(net_pnl=1.0), make_provenance(
        run_id='r1', sweep_id='s', sweep_params={'decision_logic_config.x': 1},
        sweep_objective='net_pnl', sweep_maximize=False))
    row = tmp_ledger.read_rows()[0]
    assert row.sweep_objective == 'net_pnl'
    assert row.sweep_maximize is False        # False (not None) survives the round-trip


def test_explicit_error_is_flagged_on_the_row_and_keeps_its_figures(
        tmp_ledger, make_run_summary, make_provenance):
    """
    status='error' marks the row; it no longer decides whether the row has figures.

    This test used to assert `net_pnl == 0.0` with the comment "no false KPIs", and for the
    case it was written for — a sweep combination rejected at VALIDATION — that was right: it
    never ran, so any figure would be invented. The reasoning does not generalise. A live
    session has exactly one unit, so any uncaught exception (including one in the shutdown
    path) marks the whole session a total failure, and the figures it really produced were
    replaced by zeros on the way to the books.

    The distinction now lives where it belongs: a run with NO currencies writes the
    figureless error row (`test_no_currencies_writes_error_row` below); a run that produced
    figures keeps them AND carries its status.
    """
    tmp_ledger.append(
        make_run_summary(net_pnl=999.0),
        make_provenance(run_id='r1', sweep_id='s',
                        sweep_params={'decision_logic_config.touch_zone': 0.6},
                        status='error', error="'touch_zone' value 0.6 above maximum 0.5"))
    row = tmp_ledger.read_rows()[0]
    assert row.status == 'error'
    assert '0.6 above maximum' in row.error
    assert row.net_pnl == 999.0
    assert row.sweep_params == {'decision_logic_config.touch_zone': 0.6}   # which combo failed


def test_no_currencies_writes_error_row(tmp_ledger, make_provenance):
    """A run with no usable data (no currencies) is recorded as an error row, never absent."""
    from python.framework.types.api.report_types import RunSummary
    empty = RunSummary(run_id=_RUN_ID, currencies=[])
    tmp_ledger.append(empty, make_provenance(run_id='r1'))
    rows = tmp_ledger.read_rows()
    assert len(rows) == 1
    assert rows[0].status == 'error'
    assert rows[0].error                               # a default reason is recorded


def test_read_handles_schema_evolution(tmp_path, tmp_ledger, make_run_summary, make_provenance):
    """Fragments written before a column existed (no 'status') still read — never collapse the
    whole read to a stripped common schema (the bug that hid error rows). Old → defaults 'ok'."""
    # A current fragment (carries status/error) — flagged as error.
    tmp_ledger.append(make_run_summary(),
                      make_provenance(run_id='new', scenario_set_name='s__new',
                                      status='error', error='boom'))
    # Simulate an OLD fragment from before status/error existed (fewer columns).
    old = pd.DataFrame([{
        'param_hash': 'old', 'run_id': 'old', 'run_timestamp': '2026-01-01T00:00:00+00:00',
        'sweep_id': None, 'sweep_params': None, 'scenario_set_name': 's__old',
        'currency': 'USD', 'net_pnl': 2.0}])
    (tmp_path / 'run_results').mkdir(parents=True, exist_ok=True)
    old.to_parquet(tmp_path / 'run_results' / 's__old_old.parquet', index=False)

    rows = {r.run_id: r for r in tmp_ledger.read_rows()}
    assert rows['old'].status == 'ok'        # missing status column → default, not dropped
    assert rows['old'].net_pnl == 2.0
    assert rows['new'].status == 'error'     # the current fragment's status survives the union
    assert rows['new'].error == 'boom'


def test_what_a_run_consumed_reaches_the_row(tmp_ledger, make_run_summary, make_provenance):
    """
    The consumption record (#518) survives into the ledger, where a ranking can see it.

    Recorded here rather than in the run header: the header is written at the run's START,
    before anything is mounted, and cannot know. This row is written from a finished run.
    """
    prov = make_provenance(
        run_id='r1', input_plane='archive', data_format_versions='1.5.0,1.7.0',
        origin_classes='production', origin_evidence_grades='attested,stamped',
        input_files=41, unstamped_input_files=3, price_bases='order_driven')
    tmp_ledger.append(make_run_summary(), prov)

    row = tmp_ledger.read().iloc[0]
    assert row['input_plane'] == 'archive'
    assert row['data_format_versions'] == '1.5.0,1.7.0'
    assert row['origin_classes'] == 'production'
    assert row['origin_evidence_grades'] == 'attested,stamped'
    assert row['input_files'] == 41
    assert row['unstamped_input_files'] == 3
    assert row['price_bases'] == 'order_driven'


def test_a_failed_run_still_records_what_it_read(tmp_ledger, make_run_summary, make_provenance):
    """
    An error row carries provenance too — and that is the row where it matters most.

    A run that failed over development data and one that failed over production data are
    different failures, and the ledger is the only place that distinction survives.
    """
    prov = make_provenance(run_id='r1', status='error', error='boom',
                           input_plane='archive', origin_classes='development',
                           input_files=7, unstamped_input_files=7,
                           price_bases='quote_driven')
    tmp_ledger.append(make_run_summary(), prov)

    row = tmp_ledger.read().iloc[0]
    assert row['status'] == 'error'
    assert row['origin_classes'] == 'development'
    assert row['unstamped_input_files'] == 7
    assert row['price_bases'] == 'quote_driven'


def test_every_ledger_column_is_declared_on_the_typed_row():
    """
    The projection must cover the table, or a column is written and reaches no reader.

    `RunResultRow` is the typed read of a ledger row AND the field list the optimizer's CSV
    export is built from. Pydantic ignores an unknown key without a word, so a column added to
    `LEDGER_COLUMNS` and forgotten here lands on disk, parses cleanly, and is invisible to
    every consumer — which is what had happened to the six #518 columns and to
    r_win_count / r_loss_count. A pass count cannot catch that; only this comparison can.
    """
    missing = [c for c in LEDGER_COLUMNS if c not in RunResultRow.model_fields]

    assert missing == [], (
        f'{missing} are written to the ledger and dropped on the way back in — the data is on '
        f'disk and no typed reader or exported CSV can see it')


def test_every_declared_column_is_a_typed_field():
    """
    A column on disk that the typed row does not declare is written and read by nobody.

    This is the shape of a defect that had been standing for months: eight columns were
    appended to `LEDGER_COLUMNS`, written into every fragment, and silently dropped on the
    way in — Pydantic discards an unknown key without a word, so the parquet held the answer
    and every reader saw a default. `_write_csv` builds the optimizer's export from the
    model's field list too, which is the second surface the same omission reaches.

    The guard is a derivation rather than a copy: it asks the two declarations to agree,
    so a column added tomorrow is covered the moment it is added.
    """
    missing = [c for c in LEDGER_COLUMNS if c not in RunResultRow.model_fields]
    assert not missing, (
        f'declared in LEDGER_COLUMNS but not on RunResultRow, so written and never read: '
        f'{missing}')


def test_a_rate_is_recoverable_from_its_components_across_rows(
        tmp_ledger, make_run_summary, make_provenance):
    """
    The reason `gross_profit` / `gross_loss` are carried at all — measured, not asserted.

    A rate cannot be folded out of two rows, but it CAN be re-derived from its numerator and
    denominator when both are SUM columns. `win_rate` always had that property through
    winning_trades / total_trades; `profit_factor` did not, so a session's profit factor was
    not recoverable from its booking segments and a deployment's not from its sessions (#537).

    Two rows of UNEQUAL size, because equal ones make the two methods agree by accident:

        row 1   gross 300 / 100   pf 3.00   6 of 10 won
        row 2   gross  50 / 200   pf 0.25   1 of  4 won
        folding the rates  ->  pf 1.625   win_rate 42.5 %
        from the components ->  pf 1.167   win_rate 50.0 %   <- the truth
    """
    for i, (gp, gl, won, trades) in enumerate([(300.0, 100.0, 6, 10), (50.0, 200.0, 1, 4)], 1):
        tmp_ledger.append(
            make_run_summary(gross_profit=gp, gross_loss=gl, net_pnl=gp - gl,
                             winning_trades=won, total_trades=trades,
                             profit_factor=gp / gl, win_rate=won / trades * 100),
            make_provenance(run_id=f'r{i}', scenario_set_name=f'seg{i}'))

    df = tmp_ledger.read()
    from_components = df.gross_profit.sum() / df.gross_loss.sum()
    folded = df.profit_factor.mean()

    assert round(from_components, 4) == round(350.0 / 300.0, 4)
    assert round(folded, 4) != round(from_components, 4), (
        'the fixture no longer separates the two methods — pick sizes that make them diverge, '
        'or this test proves nothing')


def test_net_pnl_is_the_difference_of_the_two_gross_halves(
        tmp_ledger, make_run_summary, make_provenance):
    """
    The three gross halves survive the parquet round trip and stay consistent.

    HONEST LIMIT, because the first version of this docstring overclaimed: for a currency row
    the identity holds BY CONSTRUCTION — `report_aggregators` builds `net_profit` as
    `total_profit - total_loss` (:180) — so this cannot fail on a freshly built row and is a
    round-trip pin rather than an audit. It earns its place because the three now travel
    through parquet and back separately, and a column dropped on the way would break it.

    The real control total is #537's `trade_count` against the records the figures were
    derived from, which is a number the row cannot produce from itself.
    """
    tmp_ledger.append(
        make_run_summary(gross_profit=300.0, gross_loss=200.0, net_pnl=100.0),
        make_provenance(run_id='r1'))

    row = tmp_ledger.read().iloc[0]
    assert row['net_pnl'] == row['gross_profit'] - row['gross_loss']


def test_an_errored_run_keeps_the_figures_it_produced(
        tmp_ledger, make_run_summary, make_provenance):
    """
    `status` is a FLAG on the row, not a reason to throw the row's money away.

    The defect this replaces, measured on a real-money field-study session: the run's own
    `io/run_summary.json` held a final equity of 71.97 USD read from Kraken, and its ledger row
    said 0 — because `append` branched on `provenance.status` and `_error_row` filled every
    unset column with a literal zero. Live has exactly one unit, so ANY uncaught exception,
    including one in the shutdown path, marks the whole session a total failure.

    A ranking is unaffected: `optimization_analysis` filters on `status == 'ok'`.
    """
    tmp_ledger.append(
        make_run_summary(net_pnl=-12.5, total_trades=4, gross_profit=30.0, gross_loss=42.5),
        make_provenance(status='error', error='died in shutdown'))

    row = tmp_ledger.read().iloc[0]
    assert row['status'] == 'error'
    assert row['error'] == 'died in shutdown'
    assert row['net_pnl'] == -12.5, 'the figures the run produced must survive its status'
    assert row['total_trades'] == 4
    assert row['gross_profit'] == 30.0


def test_every_column_declares_how_it_reduces():
    """
    A reader combining rows has to know per COLUMN, and the dangerous pairs look identical.

    `net_pnl` and `win_rate` are both aggregates: one sums, the other cannot be combined at
    all. `account_max_drawdown` and `total_fees` are both numbers that grow: one takes max(),
    the other sum(), and summing the first counts one decline once per row that was still
    inside it. None of that is visible from a column name, and before this map it was written
    down for exactly one column, as a comment in a console renderer.

    The guard is the same derivation the two tests above use: the map and the table have to
    agree, so a column added tomorrow cannot arrive without an answer.
    """
    unclassified = [c for c in LEDGER_COLUMNS if c not in COLUMN_REDUCTION]
    stale = [c for c in COLUMN_REDUCTION if c not in LEDGER_COLUMNS]

    assert unclassified == [], (
        f'{unclassified} are written to the ledger with no declared reduction — a reader '
        f'combining rows has to guess, and the wrong guess is silent')
    assert stale == [], (
        f'{stale} declare a reduction and are no longer columns — the map outlived the table')


def test_the_cumulative_extrema_are_not_summable():
    """
    The one classification whose wrong answer corrupts rather than merely confuses.

    A live row carries the RUNNING decline against the inherited peak (#497), so adding two
    of them counts one decline twice. This pins the three columns where that is true, because
    the mistake is arithmetically invisible: the sum of two drawdowns is a plausible drawdown.
    """
    assert COLUMN_REDUCTION['account_max_drawdown'] is Reduction.MAX_ABS
    # And the other two are COMPANIONS, not independent maxima. Taking each by its own max
    # pairs one row's trough with another's peak — the defect #497 removed one layer down,
    # and a map that said MAX three times would have walked back into it.
    assert COLUMN_REDUCTION['max_equity'] is Reduction.COMPANION
    assert COLUMN_REDUCTION['account_max_drawdown_pct'] is Reduction.COMPANION


def test_a_rate_is_never_combined_from_row_values():
    """
    A rate over a wider window is re-derived from the records, never folded out of two rows.

    Two segments of EQUAL size make the trap invisible: 10 trades with 6 winners and 10 with
    4 read 60 % and 40 %, their average is 50 %, and the pair really is 10/20 = 50 %. Change
    the second segment to 4 trades with 1 winner and the two answers part company:

        average of the rates :  (60 % + 25 %) / 2  =  42.5 %
        re-derived           :   7 / 14           =  50.0 %

    Nothing in either number says which one you are looking at, and the equal-size case is
    common enough that the wrong method survives a long time before it is noticed.
    """
    for column in ('win_rate', 'profit_factor', 'expectancy', 'avg_win_r', 'avg_loss_r'):
        assert COLUMN_REDUCTION[column] is Reduction.DERIVE, (
            f'{column} is a rate; combining it from row values rather than re-deriving it '
            f'over the records is how a period report acquires a number about nothing')


def test_a_live_row_carries_its_deployment_and_profile_hash(
        tmp_ledger, make_run_summary, make_provenance):
    """
    The two columns #497 appended survive the round trip through parquet.

    They are what makes a restarted bot readable as one history, and they are written by the
    LIVE path only — which has no sweep id, so nothing else in the row groups it.
    """
    tmp_ledger.append(
        make_run_summary(net_pnl=12.5),
        make_provenance(run_id='20260917_080000_aaaabbbb',
                        deployment_id='deploy_20260917_080000_ab12',
                        profile_hash='opa1b2c3'))

    row = tmp_ledger.read_rows()[0]
    assert row.deployment_id == 'deploy_20260917_080000_ab12'
    assert row.profile_hash == 'opa1b2c3'


def test_a_one_off_row_names_no_deployment(tmp_ledger, make_run_summary, make_provenance):
    """
    A session that stands alone writes an EMPTY deployment, never a placeholder.

    The empty value is what a history reader skips on. Inventing an identity for an
    ungrouped session would manufacture exactly the continuity nobody declared.
    """
    tmp_ledger.append(make_run_summary(), make_provenance(run_id='20260917_090000_ccccdddd'))
    assert tmp_ledger.read_rows()[0].deployment_id == ''


def test_a_row_says_which_pipeline_produced_it(tmp_ledger, make_run_summary, make_provenance):
    """
    `run_type` is DECLARED, not inferred.

    Before it, telling a backtest from a live session meant reading `input_plane` — a field
    that answers a different question and arrived only with #518, so 520 of 616 rows could
    not say what they were. The value comes from the same constants the run tree is laid out
    with, so the ledger, the run index and the directory on disk cannot drift apart.
    """
    tmp_ledger.append(make_run_summary(),
                      make_provenance(run_id='r_sim', run_type=RUN_TYPE_SIMULATION))
    tmp_ledger.append(make_run_summary(),
                      make_provenance(run_id='r_live', scenario_set_name='my_bot',
                                      run_type=RUN_TYPE_LIVE))

    by_run = {row.run_id: row for row in tmp_ledger.read_rows()}
    assert by_run['r_sim'].run_type == RUN_TYPE_SIMULATION
    assert by_run['r_live'].run_type == RUN_TYPE_LIVE


def test_the_kind_is_derived_from_what_the_row_already_carries(
        tmp_ledger, make_run_summary, make_provenance):
    """
    The SUBTYPE is not a column, and must not become one.

    `sweep_id` and `deployment_id` already carry it; a stored subtype would be the same fact
    written twice, and the copy nobody maintains is the one that eventually disagrees (§19).
    Derived, it cannot drift — the same reason `RunInfo.has_reports` is computed.
    """
    tmp_ledger.append(make_run_summary(),
                      make_provenance(run_id='r_plain', run_type=RUN_TYPE_SIMULATION))
    tmp_ledger.append(make_run_summary(),
                      make_provenance(run_id='r_sweep', scenario_set_name='set__c000',
                                      sweep_id='sweep_1', run_type=RUN_TYPE_SIMULATION))
    tmp_ledger.append(make_run_summary(),
                      make_provenance(run_id='r_deployed', scenario_set_name='my_bot',
                                      deployment_id='deploy_1', run_type=RUN_TYPE_LIVE))

    by_run = {row.run_id: row for row in tmp_ledger.read_rows()}
    assert by_run['r_plain'].run_kind == 'single_run'
    assert by_run['r_sweep'].run_kind == 'sweep'
    assert by_run['r_deployed'].run_kind == 'continuous'


def test_an_untyped_row_claims_no_kind_either(tmp_ledger, make_run_summary, make_provenance):
    """
    A fragment written before the column existed reads back as UNKNOWN, never as a guess —
    and the derived kind refuses to answer as well, rather than reporting 'single_run' for a
    row whose pipeline nobody knows.
    """
    tmp_ledger.append(make_run_summary(), make_provenance(run_id='r_old'))
    row = tmp_ledger.read_rows()[0]
    assert row.run_type == ''
    assert row.run_kind == ''


def test_a_swept_run_records_how_many_candidates_it_beat(
        tmp_ledger, make_run_summary, make_provenance):
    # The one input to the Deflated Sharpe Ratio that cannot be recovered afterwards: it
    # describes the SEARCH, and a search leaves no other trace once it is over (#32).
    tmp_ledger.append(
        make_run_summary(),
        make_provenance(sweep_id='sweep_1', sweep_params={'a': 1}, trial_count=500))
    assert tmp_ledger.read_rows()[0].trial_count == 500


def test_an_unswept_run_says_one_candidate_rather_than_nothing(
        tmp_ledger, make_run_summary, make_provenance):
    # 1 is a statement — this run WAS the only candidate. It must not read like the None an
    # older fragment answers, which means "nobody recorded it".
    tmp_ledger.append(make_run_summary(), make_provenance())
    assert tmp_ledger.read_rows()[0].trial_count == 1


def test_an_older_fragment_claims_no_trial_count(tmp_path, tmp_ledger, make_run_summary,
                                                 make_provenance):
    # Schema evolution: the column simply is not there, and the typed row must answer UNKNOWN
    # rather than inventing the 1 a fresh run would write.
    tmp_ledger.append(make_run_summary(), make_provenance(run_id='old'))
    fragment = next(Path(tmp_path / 'run_results').glob('*_old.parquet'))
    frame = pd.read_parquet(fragment).drop(columns=['trial_count'])
    frame.to_parquet(fragment, index=False)
    tmp_ledger._index.rebuild()
    assert tmp_ledger.read_rows()[0].trial_count is None


def test_a_fresh_row_claims_no_pruning(tmp_ledger, make_run_summary, make_provenance):
    tmp_ledger.append(make_run_summary(), make_provenance())
    assert tmp_ledger.read_rows()[0].records_pruned_at == ''


def test_pruning_stamps_the_row_and_keeps_its_figures(
        tmp_ledger, make_run_summary, make_provenance):
    # The row survives its evidence on purpose. What changes is what it CLAIMS: with the stamp
    # set, nothing can re-derive these figures from the records, and the row says so (§48).
    tmp_ledger.append(make_run_summary(net_pnl=412.0, total_trades=7),
                      make_provenance(run_id='gone'))
    assert tmp_ledger.mark_records_pruned(['gone']) == 1
    row = tmp_ledger.read_rows()[0]
    assert row.records_pruned_at != ''
    assert row.net_pnl == 412.0 and row.total_trades == 7


def test_pruning_marks_only_the_runs_it_was_given(
        tmp_ledger, make_run_summary, make_provenance):
    tmp_ledger.append(make_run_summary(), make_provenance(run_id='gone'))
    tmp_ledger.append(make_run_summary(), make_provenance(run_id='kept'))
    tmp_ledger.mark_records_pruned(['gone'])
    stamped = {r.run_id: bool(r.records_pruned_at) for r in tmp_ledger.read_rows()}
    assert stamped == {'gone': True, 'kept': False}


def test_pruning_a_run_that_never_booked_changes_nothing(
        tmp_ledger, make_run_summary, make_provenance):
    # The ordinary case for a session killed before its close: it has no fragment at all, and
    # the prune must not treat that as a failure.
    tmp_ledger.append(make_run_summary(), make_provenance(run_id='booked'))
    assert tmp_ledger.mark_records_pruned(['never_booked']) == 0


def test_the_stamp_survives_the_index(tmp_ledger, make_run_summary, make_provenance):
    # Rewriting a fragment moves its mtime, which is exactly what makes the index stale — so
    # `mark_records_pruned` rebuilds it. Without that the reader would serve the old rows.
    tmp_ledger.append(make_run_summary(), make_provenance(run_id='gone'))
    tmp_ledger.read_rows()                      # build the index against the unstamped state
    tmp_ledger.mark_records_pruned(['gone'])
    assert tmp_ledger.read_rows()[0].records_pruned_at != ''

