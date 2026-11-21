"""
An index that vanishes under its reader or writer says so (#650).

Every writer holds the index's lock, so the file can vanish between two steps of a reader or writer
only where something outside the lock removed it. Each test below makes the file really vanish at
that step — nothing is mocked into raising — and holds the error to what the operator reads: which
index, at which step, what can still cause it, and that nothing is lost.
"""

import os
from pathlib import Path
from typing import List

import pandas as pd
import pytest

from python.framework.exceptions.store_errors import StoreIndexReplacedError
from python.framework.reporting.store.run_ledger_index import RunLedgerIndex
from python.framework.store import abstract_store_index
from python.framework.store.abstract_store_index import AbstractStoreIndex


class _OneColumnIndex(AbstractStoreIndex):
    """The smallest concrete index: one column, rebuilt empty."""

    COLUMNS: List[str] = ['entry']

    def rebuild(self) -> int:
        self.write(pd.DataFrame(columns=self.COLUMNS))
        return 0


def _assert_names_the_collision(error: StoreIndexReplacedError, path: Path, step: str) -> None:
    """The error is still a FileNotFoundError, and its text says what happened and what is lost."""
    assert isinstance(error, FileNotFoundError)
    assert error.path == path
    text = str(error)
    assert str(path) in text
    assert f'being {step}' in text
    assert 'deleted by hand' in text
    assert 'Nothing is lost' in text


class TestTheReaderLandsInTheGap:

    def test_an_index_gone_between_the_check_and_the_open(self, tmp_path, monkeypatch):
        index = _OneColumnIndex(tmp_path / 'one_index.parquet')
        index.rebuild()
        real_read = pd.read_parquet

        def other_process_deletes_first(path, *args, **kwargs):
            # The delete half of another process's replace, after the reader's existence check
            os.remove(path)
            return real_read(path, *args, **kwargs)

        monkeypatch.setattr(abstract_store_index.pd, 'read_parquet', other_process_deletes_first)
        with pytest.raises(StoreIndexReplacedError) as caught:
            index.read()
        _assert_names_the_collision(caught.value, index.get_path(), 'read')


class TestTwoWritersShareOneTemporaryFile:

    def test_the_second_replace_finds_its_file_taken(self, tmp_path, monkeypatch):
        index = _OneColumnIndex(tmp_path / 'one_index.parquet')
        real_write_table = abstract_store_index.pq.write_table

        def other_writer_replaces_first(table, where, *args, **kwargs):
            real_write_table(table, where, *args, **kwargs)
            # The other writer's replace takes the one temporary file both of them wrote
            os.replace(where, index.get_path())

        monkeypatch.setattr(abstract_store_index.pq, 'write_table', other_writer_replaces_first)
        with pytest.raises(StoreIndexReplacedError) as caught:
            index.rebuild()
        _assert_names_the_collision(caught.value, index.get_path(), 'replaced')


class TestTheLedgerStampFindsNoIndex:

    def test_the_index_vanishes_between_its_write_and_its_date(self, tmp_path, monkeypatch):
        index = RunLedgerIndex(tmp_path, ['run_id'])
        real_write = index.write

        def written_then_deleted_by_another_process(frame, logic_version=None):
            real_write(frame, logic_version)
            os.remove(index.get_path())

        monkeypatch.setattr(index, 'write', written_then_deleted_by_another_process)
        with pytest.raises(StoreIndexReplacedError) as caught:
            index.rebuild()
        _assert_names_the_collision(caught.value, index.get_path(), 'dated after its rebuild')
