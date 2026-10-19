"""
The list of what runs on this machine (#603): an entry is listed while its lock is held, leaves when
its activity ends, and is cleared by the next reader when its process was killed — and only then.
"""

import json
import os
import signal
import subprocess
import sys
import textwrap
import time

import psutil

from python.framework.activities.activity_registry import ActivityRegistry
from python.framework.activities.activity_registry_reader import ActivityRegistryReader
from python.framework.activities.activity_snapshot import take_activity_snapshot
from python.framework.types.activity_registry_types import (
    ACTIVITY_ENTRY_SCHEMA,
    ActivityKind,
    ActivityState,
)
from python.framework.utils.file_lock import HeldFileLock
from python.framework.utils.machine_resources_reader import MachineResourcesReader


class _Warnings:
    """A stand-in logger that keeps the warnings it is given."""

    def __init__(self):
        self.messages = []

    def warning(self, message):
        self.messages.append(message)


def _register_in_child(directory, label):
    """Start a process that registers an activity and waits; returns it once listed."""
    code = textwrap.dedent(f'''
        import sys, time
        sys.path.insert(0, {os.getcwd()!r})
        from pathlib import Path
        from python.framework.activities.activity_registry import ActivityRegistry
        from python.framework.types.activity_registry_types import ActivityKind
        with ActivityRegistry(Path({str(directory)!r})).running(ActivityKind.IMPORT, {label!r}):
            print('listed', flush=True)
            time.sleep(60)
    ''')
    child = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, text=True)
    # The configuration's own start lines come first
    for _ in range(20):
        if child.stdout.readline().strip() == 'listed':
            return child
    child.kill()
    raise AssertionError('the child never registered')


def _write_entry(directory, activity_id, pid, process_started_at, schema=ACTIVITY_ENTRY_SCHEMA):
    """Write an entry file by hand, the way a writer that died would have left it."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f'{activity_id}.json').write_text(json.dumps({
        'schema_version': schema, 'activity_id': activity_id, 'activity_kind': 'backtest',
        'activity_state': 'running', 'activity_label': 'by hand', 'pid': pid,
        'process_started_at': process_started_at, 'command_line': ['x'],
        'started_at': '2026-10-19T08:00:00+00:00', 'started_monotonic': 1.0,
        'working_tree': '/somewhere', 'console_session': '', 'git_commit': None,
        'origin': None, 'run_id': None, 'sweep_id': None, 'parent_activity_id': None,
    }))


class TestAnEntryIsListedWhileItRuns:

    def test_a_running_activity_is_listed_with_its_lock_held(self, registry_dir):
        with ActivityRegistry().running(ActivityKind.BACKTEST, 'my_set', run_id='r1') as activity_id:
            [entry] = ActivityRegistryReader().read().entries
            assert entry.activity_id == activity_id
            assert entry.activity_kind is ActivityKind.BACKTEST
            assert entry.activity_state is ActivityState.RUNNING
            assert (entry.activity_label, entry.run_id, entry.pid) == ('my_set', 'r1', os.getpid())
            assert entry.started_at.tzinfo is not None

    def test_the_end_of_the_block_removes_both_files(self, registry_dir):
        with ActivityRegistry().running(ActivityKind.IMPORT, 'x'):
            pass
        assert ActivityRegistryReader().read().entries == []
        assert list(registry_dir.iterdir()) == []

    def test_an_exception_in_the_block_still_removes_the_entry(self, registry_dir):
        try:
            with ActivityRegistry().running(ActivityKind.IMPORT, 'x'):
                raise RuntimeError('the work failed')
        except RuntimeError:
            pass
        assert list(registry_dir.iterdir()) == []

    def test_activities_of_two_processes_are_both_listed(self, registry_dir):
        child = _register_in_child(registry_dir, 'other process')
        try:
            with ActivityRegistry().running(ActivityKind.BACKTEST, 'this process'):
                labels = {entry.activity_label for entry in ActivityRegistryReader().read().entries}
            assert labels == {'this process', 'other process'}
        finally:
            child.kill()
            child.wait()

    def test_an_activity_started_inside_another_names_its_parent(self, registry_dir):
        with ActivityRegistry().running(ActivityKind.SWEEP, 'grid', sweep_id='sweep_1') as outer:
            with ActivityRegistry().running(ActivityKind.BACKTEST, 'combination') as inner:
                entries = {e.activity_id: e for e in ActivityRegistryReader().read().entries}
        assert entries[inner].parent_activity_id == outer
        assert entries[outer].parent_activity_id is None
        assert entries[outer].sweep_id == 'sweep_1'


class TestAKilledActivityIsClearedAndOnlyThen:

    def test_kill_9_removes_the_entry_at_the_next_reading(self, registry_dir):
        child = _register_in_child(registry_dir, 'killed')
        assert [e.activity_label for e in ActivityRegistryReader().read().entries] == ['killed']
        child.send_signal(signal.SIGKILL)
        child.wait()
        assert ActivityRegistryReader().read().entries == []
        assert list(registry_dir.iterdir()) == []

    def test_a_free_lock_with_a_living_process_is_kept(self, registry_dir):
        """A mount on which the lock cannot be seen must not empty the list."""
        _write_entry(registry_dir, 'act_alive', os.getpid(), psutil.Process().create_time())
        assert [e.activity_id for e in ActivityRegistryReader().read().entries] == ['act_alive']

    def test_a_free_lock_whose_process_is_gone_is_removed(self, registry_dir):
        _write_entry(registry_dir, 'act_dead', os.getpid(), psutil.Process().create_time() - 5.0)
        assert ActivityRegistryReader().read().entries == []
        assert not (registry_dir / 'act_dead.json').exists()

    def test_an_entry_whose_process_is_a_zombie_is_removed(self, registry_dir):
        """Ended, but not yet collected by its parent: it runs nothing and holds nothing."""
        child = subprocess.Popen([sys.executable, '-c', 'pass'])
        started = psutil.Process(child.pid).create_time()
        while psutil.Process(child.pid).status() != psutil.STATUS_ZOMBIE:
            time.sleep(0.01)
        try:
            _write_entry(registry_dir, 'act_zombie', child.pid, started)
            assert ActivityRegistryReader().read().entries == []
            assert not (registry_dir / 'act_zombie.json').exists()
        finally:
            child.wait()

    def test_a_fresh_lock_without_its_entry_stays(self, registry_dir):
        registry_dir.mkdir()
        (registry_dir / 'act_starting.lock').touch()
        ActivityRegistryReader().read()
        assert (registry_dir / 'act_starting.lock').exists()

    def test_an_old_lock_without_its_entry_is_removed_unless_held(self, registry_dir):
        registry_dir.mkdir()
        old = time.time() - 120
        free, held = registry_dir / 'act_free.lock', registry_dir / 'act_held.lock'
        free.touch()
        with HeldFileLock(held):
            os.utime(free, (old, old))
            os.utime(held, (old, old))
            ActivityRegistryReader().read()
            assert not free.exists()
            assert held.exists()

    def test_a_held_entry_this_code_cannot_read_is_named_unreadable(self, registry_dir):
        _write_entry(registry_dir, 'act_foreign', os.getpid(), 0.0, schema=99)
        with HeldFileLock(registry_dir / 'act_foreign.lock'):
            listing = ActivityRegistryReader().read()
        assert listing.entries == []
        assert listing.unreadable_activity_ids == ['act_foreign']

    def test_a_missing_directory_is_an_empty_list(self, registry_dir):
        assert ActivityRegistryReader().read().entries == []


class TestRegisteringNeverEndsTheActivity:

    def test_an_unwritable_registry_costs_a_warning_and_the_work_runs(self, tmp_path):
        blocked = tmp_path / 'not_a_directory'
        blocked.write_text('a file where the directory should be')
        warnings = _Warnings()
        ran = []
        with ActivityRegistry(blocked).running(ActivityKind.AUTOTRADER_SESSION, 'bot',
                                               logger=warnings) as activity_id:
            ran.append(activity_id)
        assert ran == [None]
        assert len(warnings.messages) == 1
        assert 'Activity not listed' in warnings.messages[0]


class TestTheSnapshotCountsMemoryOnce:

    def test_the_process_holding_a_sweep_counts_once_toward_the_sweep(self, registry_dir):
        with ActivityRegistry().running(ActivityKind.SWEEP, 'grid') as outer:
            with ActivityRegistry().running(ActivityKind.BACKTEST, 'combination') as inner:
                snapshot = take_activity_snapshot(ActivityRegistryReader(),
                                                  MachineResourcesReader())
        assert snapshot.memory[outer].process_count >= 1
        assert snapshot.memory[outer].rss_bytes > 0
        assert (snapshot.memory[inner].process_count, snapshot.memory[inner].rss_bytes) == (0, 0)
        assert snapshot.machine_memory.limit_bytes > 0
        assert snapshot.measured_at.tzinfo is not None
