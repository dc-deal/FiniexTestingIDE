"""Run-results ledger tests (#390) — append per run + read all + filter."""

from python.framework.reporting.io.run_results_ledger import LEDGER_COLUMNS


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
    empty = RunSummary(currencies=[])
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
