"""
Where the divergence report reads a deployment's records (#512): a copy of the bot host's data home,
its stores found by their own index files and every run directory its index names moved into the
copy — or this machine's configured stores.
"""

from pathlib import Path

import pandas as pd
import pytest

from python.framework.reporting.builders.divergence.session_records import (
    configured_records,
    copied_records,
)


def _copy(root: Path, run_dirs) -> Path:
    """A copy of a host's data home: a run index naming host paths, a ledger, a run-config store."""
    (root / 'runs' / 'ledger').mkdir(parents=True)
    (root / 'runs' / 'ledger' / 'run_ledger_index.parquet').write_bytes(b'')
    (root / 'run_configs').mkdir()
    (root / 'run_configs' / 'run_configs_index.parquet').write_bytes(b'')
    for run_dir in run_dirs:
        (root / 'runs' / 'autotrader' / 'my_profile' / Path(run_dir).name).mkdir(parents=True)
    pd.DataFrame({'run_id': [Path(d).name for d in run_dirs], 'run_dir': list(run_dirs)}) \
        .to_parquet(root / 'runs' / 'runs_index.parquet')
    return root


class TestACopy:

    def test_every_run_directory_is_moved_into_the_copy(self, tmp_path):
        copy = _copy(tmp_path / 'copy', ['/srv/my_host/home/runs/autotrader/my_profile/r1',
                                         '/srv/my_host/home/runs/autotrader/my_profile/r2'])
        records = copied_records(copy, tmp_path / 'out' / 'live_index.parquet')
        rows = pd.read_parquet(records.index_path)
        assert rows['run_dir'].tolist() == [
            str(copy / 'runs' / 'autotrader' / 'my_profile' / 'r1'),
            str(copy / 'runs' / 'autotrader' / 'my_profile' / 'r2')]
        assert records.root == str(copy)
        assert records.index_path == tmp_path / 'out' / 'live_index.parquet'

    def test_a_run_directory_missing_from_the_copy_is_refused(self, tmp_path):
        copy = _copy(tmp_path / 'copy', ['/srv/my_host/home/runs/autotrader/my_profile/r1'])
        index = pd.read_parquet(copy / 'runs' / 'runs_index.parquet')
        index.loc[len(index)] = ['r9', '/srv/my_host/home/runs/autotrader/my_profile/r9']
        index.to_parquet(copy / 'runs' / 'runs_index.parquet')
        with pytest.raises(FileNotFoundError, match='no end of that path lies'):
            copied_records(copy, tmp_path / 'live_index.parquet')

    def test_a_copy_of_two_data_homes_is_refused(self, tmp_path):
        copy = _copy(tmp_path / 'copy', ['/srv/my_host/home/runs/autotrader/my_profile/r1'])
        (copy / 'old').mkdir()
        (copy / 'old' / 'runs_index.parquet').write_bytes(b'')
        with pytest.raises(FileNotFoundError, match='2 index file'):
            copied_records(copy, tmp_path / 'live_index.parquet')


def test_without_a_copy_the_configured_stores_are_read():
    records = configured_records()
    assert records.root == ''
    assert records.index_path.name == 'runs_index.parquet'
