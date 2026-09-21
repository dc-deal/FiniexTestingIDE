"""
The sweep's ranked CSV — the export that nothing ever called with a real row.

`_write_csv` built its header from `RunResultRow.model_fields` and its rows from
`model_dump()`. Those two differ by exactly one key: `run_kind` is a `@computed_field`, so it
is in every dumped dict and in no declared-field list — and `csv.DictWriter` refuses a row
carrying a key its header does not name. Every non-empty sweep report therefore raised

    ValueError: dict contains fields not in fieldnames: 'run_kind'

at the last step, after the whole ranking had been computed and printed.

It stood because no test ever wrote the file. The pruning test writes a dummy `ranked.csv` to
check that pruning removes it; the ledger tests assert on `model_fields` without exporting.
This file closes that: it calls the real writer with a real row and reads the file back.
"""

import csv
from pathlib import Path

import pytest

from python.framework.optimization.optimization_report import _write_csv
from python.framework.types.api.report_types import RunResultRow


@pytest.fixture
def sweep_root(tmp_path, monkeypatch):
    """Point the sweeps directory at tmp — the export resolves its root from app config."""
    from python.configuration.app_config_manager import AppConfigManager
    real = AppConfigManager().get_file_logging_config_object()
    isolated = real.model_copy(update={
        'run_logs': real.run_logs.model_copy(update={'sweeps': tmp_path / 'sweeps'})})
    monkeypatch.setattr(
        AppConfigManager, 'get_file_logging_config_object', lambda self: isolated)
    return tmp_path / 'sweeps'


def _row(**kw) -> RunResultRow:
    """A ledger row with the three fields the model requires."""
    base = dict(run_id='20260101_000000', param_hash='p',
                run_timestamp='2026-01-01T00:00:00+00:00')
    base.update(kw)
    return RunResultRow(**base)


def test_a_non_empty_export_writes_rather_than_raising(sweep_root):
    """The defect itself: one real row was enough to break the writer."""
    path = _write_csv([_row(net_pnl=12.5)], 'sweep_20260101_000000')

    assert path.exists()
    rows = list(csv.DictReader(path.open()))
    assert len(rows) == 1
    assert rows[0]['run_id'] == '20260101_000000'


def test_the_computed_column_reaches_the_file(sweep_root):
    """
    `run_kind` is the key the header was missing, and it is worth EXPORTING rather than
    dropping: it is what tells a reader whether a ranked row came from a backtest or a live
    session, and a ranking that mixes the two compares runs that are not comparable.
    """
    path = _write_csv([_row()], 'sweep_20260101_000001')

    header = csv.DictReader(path.open()).fieldnames
    assert 'run_kind' in header
    assert len(header) == len(RunResultRow.model_fields) + 1, (
        'the header should carry every declared field plus the computed one')


def test_an_empty_ranking_still_writes_a_header(sweep_root):
    """
    A sweep whose combinations all failed ranks nothing. It must still leave a readable file —
    a zero-byte CSV and a missing CSV are the same thing to a reader, and one of them means
    the export crashed.
    """
    path = _write_csv([], 'sweep_20260101_000002')

    header = csv.DictReader(path.open()).fieldnames
    assert header is not None and 'run_id' in header
    assert list(csv.DictReader(path.open())) == []
