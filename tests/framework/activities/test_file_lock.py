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

import pytest

from python.framework.exceptions.file_lock_errors import FileLockHeldError
from python.framework.utils import file_lock as file_lock_module
from python.framework.utils.file_lock import FileLock, is_lock_held


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
