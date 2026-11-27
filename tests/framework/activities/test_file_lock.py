"""
The kernel lock the activity list rests on (#603): held while its holder lives, free the moment it
dies — however it dies.
"""

import errno
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time

import pytest

from python.framework.exceptions.file_lock_errors import FileLockHeldError
from python.framework.utils import file_lock as file_lock_module
from python.framework.utils.file_lock import FileLock, is_lock_held, read_lock_note


def _hold_in_child(path):
    """Start a process that takes the lock and waits; returns it once the lock is held."""
    code = textwrap.dedent(f'''
        import sys, time
        sys.path.insert(0, {os.getcwd()!r})
        from pathlib import Path
        from python.framework.utils.file_lock import FileLock
        lock = FileLock(Path({str(path)!r}))
        print('held', flush=True)
        time.sleep(60)
    ''')
    child = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, text=True)
    assert child.stdout.readline().strip() == 'held'
    return child


class TestAHolderHoldsTheLock:

    def test_a_taken_lock_is_held_and_a_released_one_is_not(self, tmp_path):
        path = tmp_path / 'a.lock'
        lock = FileLock(path)
        assert is_lock_held(path)
        lock.release()
        assert not is_lock_held(path)

    def test_a_second_taker_is_refused_while_the_first_holds(self, tmp_path):
        path = tmp_path / 'a.lock'
        with FileLock(path):
            with pytest.raises(FileLockHeldError):
                FileLock(path)

    def test_a_second_release_does_nothing(self, tmp_path):
        lock = FileLock(tmp_path / 'a.lock')
        lock.release()
        lock.release()

    def test_asking_never_creates_the_file(self, tmp_path):
        path = tmp_path / 'missing.lock'
        assert not is_lock_held(path)
        assert not path.exists()

    def test_two_readers_asking_do_not_mistake_each_other_for_a_holder(self, tmp_path):
        path = tmp_path / 'a.lock'
        path.touch()
        assert not is_lock_held(path)
        assert not is_lock_held(path)


class TestTheNoteNamesTheHolder:

    def test_a_note_replaces_the_one_before(self, tmp_path):
        path = tmp_path / 'my_bot.lock'
        with FileLock(path) as lock:
            lock.write_note('a longer first note')
            lock.write_note('second')
            assert read_lock_note(path) == 'second'

    def test_a_missing_file_has_no_note_and_is_not_created(self, tmp_path):
        path = tmp_path / 'absent.lock'
        assert read_lock_note(path) == ''
        assert not path.exists()

    def test_the_file_is_readable_by_its_owner_alone(self, tmp_path):
        path = tmp_path / 'my_bot.lock'
        with FileLock(path):
            assert path.stat().st_mode & 0o777 == 0o600

    def test_a_released_lock_writes_nothing(self, tmp_path):
        path = tmp_path / 'my_bot.lock'
        lock = FileLock(path)
        lock.write_note('held')
        lock.release()
        lock.write_note('after the release')
        assert read_lock_note(path) == 'held'


class TestALockThatCannotBeTaken:

    def test_a_mount_without_locks_leaves_no_descriptor_open(self, tmp_path, monkeypatch):
        """ENOLCK and its kin raise as they are — and the file opened for them is closed."""
        def no_locks_here(descriptor, operation):
            raise OSError(errno.ENOLCK, 'No locks available')

        monkeypatch.setattr(file_lock_module.fcntl, 'flock', no_locks_here)
        open_before = len(os.listdir('/proc/self/fd'))
        with pytest.raises(OSError) as refused:
            FileLock(tmp_path / 'x.lock')
        assert refused.value.errno == errno.ENOLCK
        assert len(os.listdir('/proc/self/fd')) == open_before


class TestTheLockDiesWithItsHolder:

    def test_a_holder_in_another_process_is_seen(self, tmp_path):
        path = tmp_path / 'a.lock'
        child = _hold_in_child(path)
        try:
            assert is_lock_held(path)
        finally:
            child.kill()
            child.wait()

    def test_kill_9_frees_the_lock_at_once(self, tmp_path):
        path = tmp_path / 'a.lock'
        child = _hold_in_child(path)
        child.send_signal(signal.SIGKILL)
        child.wait()
        assert not is_lock_held(path)
        FileLock(path).release()


class TestSharedHolders:
    """A store index is read by many and written by one (#650): shared holds side by side, an exclusive alone."""

    def test_two_shared_holders_hold_at_once(self, tmp_path):
        path = tmp_path / 'a.lock'
        first = FileLock(path, shared=True)
        second = FileLock(path, shared=True)
        first.release()
        second.release()

    def test_an_exclusive_request_is_refused_while_a_shared_holder_holds(self, tmp_path):
        path = tmp_path / 'a.lock'
        with FileLock(path, shared=True):
            with pytest.raises(FileLockHeldError):
                FileLock(path)
        FileLock(path).release()

    def test_a_shared_request_is_refused_while_an_exclusive_holder_holds(self, tmp_path):
        path = tmp_path / 'a.lock'
        with FileLock(path):
            with pytest.raises(FileLockHeldError):
                FileLock(path, shared=True)

    def test_a_shared_holder_writes_no_note(self, tmp_path):
        with FileLock(tmp_path / 'a.lock', shared=True) as lock:
            with pytest.raises(ValueError):
                lock.write_note('pid 1')


class TestAWaitingRequest:

    def test_a_wait_ends_when_the_holder_lets_go(self, tmp_path):
        path = tmp_path / 'a.lock'
        holder = FileLock(path)
        threading.Timer(0.2, holder.release).start()
        started = time.monotonic()
        FileLock(path, wait_s=5.0).release()
        assert 0.15 <= time.monotonic() - started < 5.0

    def test_a_wait_that_runs_out_names_the_holder_and_the_wait(self, tmp_path):
        path = tmp_path / 'a.lock'
        with FileLock(path) as holder:
            holder.write_note('pid 4711 · store_cli.py rebuild runs · since 18:02:11 UTC')
            started = time.monotonic()
            with pytest.raises(FileLockHeldError) as refused:
                FileLock(path, shared=True, wait_s=0.3)
            assert time.monotonic() - started >= 0.3
        assert refused.value.note == 'pid 4711 · store_cli.py rebuild runs · since 18:02:11 UTC'
        assert refused.value.waited_s == 0.3
        assert 'pid 4711' in str(refused.value)
        assert 'waited 0.3 s' in str(refused.value)

    def test_a_request_that_does_not_wait_says_nothing_about_waiting(self, tmp_path):
        path = tmp_path / 'a.lock'
        with FileLock(path):
            with pytest.raises(FileLockHeldError) as refused:
                FileLock(path)
        assert 'waited' not in str(refused.value)


class TestAReadOnlyHold:
    """On a data home it may not write, a reader opens the lock file a writer created, or none."""

    def test_an_existing_file_is_held_shared_without_being_written(self, tmp_path):
        path = tmp_path / 'a.lock'
        FileLock(path).release()
        with FileLock(path, shared=True, read_only=True):
            with pytest.raises(FileLockHeldError):
                FileLock(path)

    def test_a_missing_file_is_not_created(self, tmp_path):
        path = tmp_path / 'missing.lock'
        with pytest.raises(FileNotFoundError):
            FileLock(path, shared=True, read_only=True)
        assert not path.exists()

    def test_an_exclusive_hold_cannot_be_read_only(self, tmp_path):
        with pytest.raises(ValueError):
            FileLock(tmp_path / 'a.lock', read_only=True)
