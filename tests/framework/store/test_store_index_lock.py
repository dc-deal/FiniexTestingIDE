"""
A lock per store index: whoever writes it holds it alone, whoever reads it shares it (#650).

The failures it ends were measured between PROCESSES — two API requests rebuilding the ledger index at
once, a run's start writing the run index back without another run's row — so the tests that matter run
real child processes. A child is handed every path it uses: it inherits none of this suite's
redirections, and nothing it does may reach a real store.
"""

import errno
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from typing import List
from unittest.mock import MagicMock

import pandas as pd
import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.framework.autotrader.run_record import session_run_index
from python.framework.autotrader.run_record.session_run_index import write_run_index_for_session
from python.framework.exceptions.file_lock_errors import FileLockHeldError
from python.framework.exceptions.store_errors import LedgerFragmentVanishedError
from python.framework.reporting.store import run_ledger_index
from python.framework.reporting.store.run_index import RunIndex
from python.framework.reporting.store.run_ledger_index import RunLedgerIndex
from python.framework.store import abstract_store_index
from python.framework.store.abstract_store_index import AbstractStoreIndex
from python.framework.store.run_config_store import RunConfigStore
from python.framework.types.run_config_types import RunConfigKind
from python.framework.utils import file_lock as file_lock_module
from python.framework.utils.file_lock import FileLock, is_lock_held, read_lock_note


class _OneColumnIndex(AbstractStoreIndex):
    """The smallest concrete index: one column, rebuilt with one row."""

    COLUMNS: List[str] = ['entry']

    def rebuild(self) -> int:
        self.write(pd.DataFrame({'entry': ['rebuilt']}))
        return 1


def _child(code: str) -> subprocess.Popen:
    """Start a Python child in this tree; its output is read when it ends."""
    prelude = f'import sys\nsys.path.insert(0, {os.getcwd()!r})\n'
    return subprocess.Popen([sys.executable, '-c', prelude + textwrap.dedent(code)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def _finished(child: subprocess.Popen) -> str:
    """Wait for a child, and fail with its output when it failed."""
    output, _ = child.communicate(timeout=120)
    assert child.returncode == 0, output
    return output


def _ledger_fragments(ledger_dir: Path, run_ids: List[str]) -> None:
    """One ledger fragment per run, named the way the ledger names them."""
    ledger_dir.mkdir(parents=True, exist_ok=True)
    for run_id in run_ids:
        pd.DataFrame({'run_id': [run_id]}).to_parquet(ledger_dir / f'sweep_{run_id}.parquet')


class TestTheLockFileStandsBesideTheIndex:

    def test_a_write_takes_the_lock_beside_the_index_and_gives_it_back(self, tmp_path):
        index = _OneColumnIndex(tmp_path / 'one_index.parquet')
        index.rebuild()
        assert index.get_lock_path() == tmp_path / 'one_index.lock'
        assert index.get_lock_path().exists()
        assert not is_lock_held(index.get_lock_path())

    def test_an_index_that_is_not_there_is_read_without_a_lock_file(self, tmp_path):
        index = _OneColumnIndex(tmp_path / 'store' / 'one_index.parquet')
        assert index.read().empty
        assert index.stored_logic_version() is None
        assert not (tmp_path / 'store').exists()

    def test_one_instance_never_waits_for_itself(self, tmp_path):
        index = _OneColumnIndex(tmp_path / 'one_index.parquet')
        started = time.monotonic()
        with index.updating(wait_s=0.0):
            index.write(pd.DataFrame({'entry': ['a']}))
            assert index.read()['entry'].tolist() == ['a']
            assert index.is_current()
            with index.updating(wait_s=0.0):
                index.write_incremental(pd.DataFrame({'entry': ['a', 'b']}))
        assert time.monotonic() - started < 5.0
        assert index.read()['entry'].tolist() == ['a', 'b']


class TestAHolderLeavesNoNoteBehind:

    def test_the_note_names_the_holder_while_it_holds_and_goes_with_it(self, tmp_path):
        index = _OneColumnIndex(tmp_path / 'one_index.parquet')
        with index.updating():
            assert f'pid {os.getpid()}' in read_lock_note(index.get_lock_path())
        assert read_lock_note(index.get_lock_path()) == ''


class TestTwoProcessesCountOneConfiguration:
    """
    A configuration's run count and first sighting live in its index alone: an update lost there is
    lost for good.
    """

    def test_two_processes_noting_runs_of_one_configuration_both_count(self, tmp_path):
        store = RunConfigStore(tmp_path / 'run_configs')
        config_id = store.register_document(
            {'strategy_config': {'a': 1}}, RunConfigKind.AUTOTRADER_RENDERED).config_id
        code = f'''
            from pathlib import Path
            from python.framework.store.run_config_store import RunConfigStore
            store = RunConfigStore(Path({str(tmp_path / 'run_configs')!r}))
            for _ in range(15):
                store.note_run({config_id!r})
            print('done')
        '''
        children = [_child(code) for _ in range(2)]
        for child in children:
            _finished(child)
        assert RunConfigStore(tmp_path / 'run_configs').get_index().by_id(config_id).run_count == 30


class TestTwoRunsRegisterAtOnce:
    """
    A run's start reads the run index, adds its row and writes it back: before the lock, two starts at
    once lost a row.
    """

    def test_every_row_of_two_concurrent_registrations_is_kept(self, tmp_path):
        index_path = tmp_path / 'runs' / 'runs_index.parquet'
        code = '''
            from datetime import datetime, timezone
            from pathlib import Path
            from python.framework.reporting.store.run_index import RunIndex
            from python.framework.types.api.report_types import RunHeader
            index = RunIndex(Path({index!r}))
            for n in range(15):
                run_id = f'20261120_120000_{prefix}{{n:06x}}'
                header = RunHeader(run_id=run_id, start_time=datetime(2026, 11, 20, tzinfo=timezone.utc),
                                   run_type='simulation', run_name='a_set', config_snapshot='a.json',
                                   app_version='1.6.0', git_commit='abc1234')
                index.register_run(header, Path({root!r}) / run_id)
            print('done')
        '''
        children = [_child(code.format(index=str(index_path), root=str(tmp_path / 'runs'), prefix=prefix))
                    for prefix in ('aa', 'bb')]
        for child in children:
            _finished(child)
        run_ids = set(RunIndex(index_path).read()['run_id'])
        assert len(run_ids) == 30
        assert {r[16:18] for r in run_ids} == {'aa', 'bb'}


class TestAReaderAndAWriter:

    def test_a_reader_waits_for_a_writer_and_reads_what_it_wrote(self, tmp_path):
        index = _OneColumnIndex(tmp_path / 'one_index.parquet')
        index.write(pd.DataFrame({'entry': ['before']}))
        child = _child(f'''
            import time
            from pathlib import Path
            from python.framework.utils.file_lock import FileLock
            import pandas as pd
            lock = FileLock(Path({str(index.get_lock_path())!r}))
            print('held', flush=True)
            time.sleep(0.6)
            pd.DataFrame({{'entry': ['after']}}).to_parquet({str(index.get_path())!r})
            lock.release()
        ''')
        assert child.stdout.readline().strip() == 'held'
        started = time.monotonic()
        rows = index.read()
        waited = time.monotonic() - started
        _finished(child)
        assert waited >= 0.4
        assert rows['entry'].tolist() == ['after']

    def test_a_reader_in_the_gap_of_a_replace_waits_instead_of_reading_nothing(self, tmp_path):
        """Between the delete and the rename the index is missing beside its lock file: that is a writer at work."""
        index = _OneColumnIndex(tmp_path / 'one_index.parquet')
        index.write(pd.DataFrame({'entry': ['before']}))
        child = _child(f'''
            import os, time
            from pathlib import Path
            from python.framework.utils.file_lock import FileLock
            import pandas as pd
            lock = FileLock(Path({str(index.get_lock_path())!r}))
            os.remove({str(index.get_path())!r})
            print('held', flush=True)
            time.sleep(0.6)
            pd.DataFrame({{'entry': ['after']}}).to_parquet({str(index.get_path())!r})
            lock.release()
        ''')
        assert child.stdout.readline().strip() == 'held'
        assert not index.get_path().exists()
        rows = index.read()
        _finished(child)
        assert rows['entry'].tolist() == ['after']

    def test_a_wait_that_runs_out_names_the_process_that_holds_the_index(self, tmp_path):
        child = _child(f'''
            import time
            from pathlib import Path
            from python.framework.reporting.store.run_ledger_index import RunLedgerIndex
            index = RunLedgerIndex(Path({str(tmp_path)!r}), ['run_id'])
            with index.updating():
                print('held', flush=True)
                time.sleep(30)
        ''')
        try:
            assert child.stdout.readline().strip() == 'held'
            index = RunLedgerIndex(tmp_path, ['run_id'])
            index.set_lock_wait_s(0.3)
            with pytest.raises(FileLockHeldError) as refused:
                index.write(pd.DataFrame({'run_id': ['x']}))
            assert f'pid {child.pid}' in str(refused.value)
            assert 'waited 0.3 s' in str(refused.value)
        finally:
            child.kill()
            child.wait()
        assert not index.get_path().exists()


class TestAStaleIndexIsReadWithoutFailing:

    def test_two_processes_rebuilding_one_ledger_at_once_both_answer(self, tmp_path):
        ledger_dir = tmp_path / 'ledger'
        _ledger_fragments(ledger_dir, [f'run{n}' for n in range(40)])
        code = f'''
            import os, time
            from pathlib import Path
            from python.framework.reporting.store.run_ledger_index import RunLedgerIndex
            ledger = Path({str(ledger_dir)!r})
            for n in range(15):
                # A fragment rewritten makes the index stale for the next read, as a sealed booking period does
                fragment = ledger / 'sweep_run0.parquet'
                os.utime(fragment)
                rows = RunLedgerIndex(ledger, ['run_id']).read_valid()
                assert len(rows) == 40, len(rows)
            print('done')
        '''
        children = [_child(code) for _ in range(3)]
        for child in children:
            _finished(child)

    def test_a_reader_that_cannot_take_the_index_alone_derives_it(self, tmp_path, monkeypatch):
        ledger_dir = tmp_path / 'ledger'
        _ledger_fragments(ledger_dir, ['run_a', 'run_b'])
        index = RunLedgerIndex(ledger_dir, ['run_id'])
        monkeypatch.setattr(abstract_store_index, 'REBUILD_LOCK_WAIT_S', 0.2)
        other_reader = FileLock(index.get_lock_path(), shared=True)
        release = threading.Timer(3.0, other_reader.release)
        release.start()
        try:
            started = time.monotonic()
            rows = index.read_valid()
            assert time.monotonic() - started < 2.0
        finally:
            release.cancel()
            other_reader.release()
        assert sorted(rows['run_id']) == ['run_a', 'run_b']
        assert not index.get_path().exists()

    def test_a_reader_on_a_data_home_it_may_not_write_derives_the_index(self, tmp_path, monkeypatch):
        ledger_dir = tmp_path / 'ledger'
        _ledger_fragments(ledger_dir, ['run_a'])
        index = RunLedgerIndex(ledger_dir, ['run_id'])
        real_open = file_lock_module.os.open

        def read_only_home(path, flags, *args):
            # The suite runs as root, so a read-only home is a refusal at the open, not a chmod
            if flags & os.O_CREAT:
                raise OSError(errno.EROFS, 'Read-only file system', str(path))
            return real_open(path, flags, *args)

        monkeypatch.setattr(file_lock_module.os, 'open', read_only_home)
        rows = index.read_valid()
        assert rows['run_id'].tolist() == ['run_a']
        assert not index.get_path().exists()
        assert not index.get_lock_path().exists()


class TestAReaderOnAReadOnlyHome:

    def test_an_index_a_writer_reached_is_read_under_the_lock_file_it_left(self, tmp_path, monkeypatch):
        index = _OneColumnIndex(tmp_path / 'one_index.parquet')
        index.rebuild()
        real_open = file_lock_module.os.open

        def read_only_home(path, flags, *args):
            if flags & os.O_CREAT:
                raise OSError(errno.EROFS, 'Read-only file system', str(path))
            return real_open(path, flags, *args)

        monkeypatch.setattr(file_lock_module.os, 'open', read_only_home)
        holder = FileLock(index.get_lock_path(), shared=True, read_only=True)
        try:
            assert index.read()['entry'].tolist() == ['rebuilt']
        finally:
            holder.release()


class TestALedgerFragmentInItsReplace:

    def test_a_fragment_caught_between_delete_and_rename_is_read_again(self, tmp_path, monkeypatch):
        ledger_dir = tmp_path / 'ledger'
        _ledger_fragments(ledger_dir, ['run_a', 'run_b'])
        real_read = pd.read_parquet
        missed = []

        def caught_in_the_gap(path, *args, **kwargs):
            if Path(path).name == 'sweep_run_a.parquet' and len(missed) < 2:
                missed.append(path)
                raise FileNotFoundError(errno.ENOENT, 'No such file or directory', str(path))
            return real_read(path, *args, **kwargs)

        monkeypatch.setattr(run_ledger_index.pd, 'read_parquet', caught_in_the_gap)
        rows = RunLedgerIndex(ledger_dir, ['run_id']).derive()
        assert len(missed) == 2
        assert sorted(rows['run_id']) == ['run_a', 'run_b']

    def test_a_fragment_that_stays_missing_is_named(self, tmp_path, monkeypatch):
        ledger_dir = tmp_path / 'ledger'
        _ledger_fragments(ledger_dir, ['run_a'])
        real_read = pd.read_parquet

        def gone(path, *args, **kwargs):
            if Path(path).name == 'sweep_run_a.parquet':
                raise FileNotFoundError(errno.ENOENT, 'No such file or directory', str(path))
            return real_read(path, *args, **kwargs)

        monkeypatch.setattr(run_ledger_index.pd, 'read_parquet', gone)
        monkeypatch.setattr(run_ledger_index, '_FRAGMENT_REREAD_S', 0.1)
        with pytest.raises(LedgerFragmentVanishedError) as refused:
            RunLedgerIndex(ledger_dir, ['run_id']).derive()
        assert 'sweep_run_a.parquet' in str(refused.value)
        assert isinstance(refused.value, FileNotFoundError)


class TestASessionNeverEndsOverTheIndex:

    def test_a_held_run_index_costs_the_update_and_says_so(self, monkeypatch):
        run_index_path = Path(AppConfigManager().get_file_logging_config_object().run_index)
        monkeypatch.setattr(session_run_index, 'SESSION_INDEX_LOCK_WAIT_S', 0.2)
        logger = MagicMock()
        written = []
        with FileLock(RunIndex(run_index_path).get_lock_path()):
            write_run_index_for_session(
                lambda index: written.append(index.write(pd.DataFrame(columns=RunIndex.COLUMNS))),
                logger, 'this session')
        assert written == []
        warning = logger.warning.call_args.args[0]
        assert 'did not record this session' in warning
        assert 'store_cli.py rebuild runs' in warning
        assert 'The session runs on' in warning
