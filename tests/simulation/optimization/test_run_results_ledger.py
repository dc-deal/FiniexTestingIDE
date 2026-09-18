"""Run-results ledger tests (#390) — append per run + read all + filter."""

from python.framework.reporting.store.run_results_ledger import LEDGER_COLUMNS
from python.framework.types.api.report_types import RunResultRow
from python.framework.types.log_layout_types import RUN_TYPE_LIVE, RUN_TYPE_SIMULATION

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
    from python.framework.types.api.report_types import RunResultRow
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


def test_explicit_error_writes_error_row(tmp_ledger, make_run_summary, make_provenance):
    """A provenance status='error' → one error-flagged row (recorded, no KPIs), not silently absent."""
    tmp_ledger.append(
        make_run_summary(net_pnl=999.0),   # KPIs ignored on an error run
        make_provenance(run_id='r1', sweep_id='s',
                        sweep_params={'decision_logic_config.touch_zone': 0.6},
                        status='error', error="'touch_zone' value 0.6 above maximum 0.5"))
    row = tmp_ledger.read_rows()[0]
    assert row.status == 'error'
    assert '0.6 above maximum' in row.error
    assert row.net_pnl == 0.0                          # no false KPIs
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
    import pandas as pd
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
