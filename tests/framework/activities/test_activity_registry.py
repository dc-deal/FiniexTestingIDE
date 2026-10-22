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
from pathlib import Path

import psutil
import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.framework.activities import activity_registry as activity_registry_module
from python.framework.activities import activity_registry_reader as reader_module
from python.framework.activities.activity_registry import ActivityRegistry
from python.framework.activities.activity_registry_reader import ActivityRegistryReader
from python.framework.activities.activity_snapshot import take_activity_snapshot
from python.framework.types.activity_registry_types import (
    ACTIVITY_ENTRY_SCHEMA,
    ActivityKind,
    ActivityState,
)
from python.framework.types.config_types.app_config_types import ActivitiesConfig
from python.framework.utils.file_lock import FileLock
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


def _write_entry(directory, activity_id, pid, process_started_at, schema=ACTIVITY_ENTRY_SCHEMA,
                 started_at='2026-10-19T08:00:00+00:00', overrides=None):
    """Write an entry file by hand, the way a writer that died would have left it."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f'{activity_id}.json').write_text(json.dumps({
        'schema_version': schema, 'activity_id': activity_id, 'activity_kind': 'backtest',
        'activity_state': 'running', 'activity_label': 'by hand', 'pid': pid,
        'process_started_at': process_started_at, 'command_line': ['x'],
        'started_at': started_at, 'started_monotonic': 1.0,
        'working_tree': '/somewhere', 'console_session': '', 'git_commit': None,
        'origin': None, 'run_id': None, 'sweep_id': None, 'parent_activity_id': None,
    } | (overrides or {})))


class TestAnEntryIsListedWhileItRuns:

    def test_a_running_activity_is_listed_with_its_lock_held(self, registry_dir):
        with ActivityRegistry().running(ActivityKind.BACKTEST, 'my_set', run_id='r1') as activity_id:
            [entry] = ActivityRegistryReader().read().entries
            assert entry.activity_id == activity_id
            assert entry.activity_kind is ActivityKind.BACKTEST
            assert entry.activity_state is ActivityState.RUNNING
            assert (entry.activity_label, entry.run_id, entry.pid) == ('my_set', 'r1', os.getpid())
            assert entry.started_at.tzinfo is not None

    def test_the_list_is_readable_by_its_owner_alone(self, registry_dir):
        """An entry names a command line; another user of the machine reads none of it (#598)."""
        with ActivityRegistry().running(ActivityKind.BACKTEST, 'my_set') as activity_id:
            assert registry_dir.stat().st_mode & 0o777 == 0o700
            for suffix in ('.json', '.lock'):
                assert (registry_dir / f'{activity_id}{suffix}').stat().st_mode & 0o777 == 0o600

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
        with FileLock(held):
            os.utime(free, (old, old))
            os.utime(held, (old, old))
            ActivityRegistryReader().read()
            assert not free.exists()
            assert held.exists()

    def test_a_held_entry_this_code_cannot_read_is_named_unreadable(self, registry_dir):
        _write_entry(registry_dir, 'act_foreign', os.getpid(), 0.0, schema=99)
        with FileLock(registry_dir / 'act_foreign.lock'):
            listing = ActivityRegistryReader().read()
        assert listing.entries == []
        assert listing.unreadable_activity_ids == ['act_foreign']

    def test_a_missing_directory_is_an_empty_list(self, registry_dir):
        assert ActivityRegistryReader().read().entries == []


class TestAReadingTouchesOnlyItsOwnFiles:

    def test_files_the_registry_did_not_name_survive_a_reading(self, registry_dir):
        registry_dir.mkdir()
        foreign = ['notes.txt', 'package.json', 'poetry.lock', 'settings.json']
        (registry_dir / 'package.json').write_text('{}')
        (registry_dir / 'settings.json').write_text('[]')
        (registry_dir / 'poetry.lock').write_text('')
        (registry_dir / 'notes.txt').write_text('')
        an_hour_ago = time.time() - 3600
        os.utime(registry_dir / 'poetry.lock', (an_hour_ago, an_hour_ago))
        with ActivityRegistry().running(ActivityKind.IMPORT, 'beside them') as activity_id:
            listing = ActivityRegistryReader().read()
        assert [entry.activity_id for entry in listing.entries] == [activity_id]
        assert sorted(path.name for path in registry_dir.iterdir()) == foreign

    def test_an_old_temporary_entry_file_is_removed_and_a_fresh_one_stays(self, registry_dir):
        registry_dir.mkdir()
        old = registry_dir / '.act_old.json.a1b2c3.tmp'
        fresh = registry_dir / '.act_fresh.json.d4e5f6.tmp'
        old.write_text('{')
        fresh.write_text('{')
        two_minutes_ago = time.time() - 120
        os.utime(old, (two_minutes_ago, two_minutes_ago))
        ActivityRegistryReader().read()
        assert not old.exists()
        assert fresh.exists()

    def test_a_path_that_is_no_directory_cannot_be_read(self, registry_dir):
        registry_dir.write_text('a file where the directory should be')
        with pytest.raises(OSError):
            ActivityRegistryReader().read()


class TestOneEntryNeverFailsTheReading:

    def test_a_held_entry_that_is_no_object_is_named_and_the_rest_is_listed(self, registry_dir):
        registry_dir.mkdir()
        (registry_dir / 'act_array.json').write_text('[]')
        with FileLock(registry_dir / 'act_array.lock'):
            with ActivityRegistry().running(ActivityKind.IMPORT, 'readable') as activity_id:
                listing = ActivityRegistryReader().read()
        assert [entry.activity_id for entry in listing.entries] == [activity_id]
        assert listing.unreadable_activity_ids == ['act_array']

    def test_a_held_entry_without_an_offset_is_named_rather_than_sorted(self, registry_dir):
        _write_entry(registry_dir, 'act_naive', os.getpid(), psutil.Process().create_time(),
                     started_at='2026-10-19T08:00:00')
        with FileLock(registry_dir / 'act_naive.lock'):
            with ActivityRegistry().running(ActivityKind.IMPORT, 'aware') as activity_id:
                listing = ActivityRegistryReader().read()
        assert [entry.activity_id for entry in listing.entries] == [activity_id]
        assert listing.unreadable_activity_ids == ['act_naive']

    @pytest.mark.parametrize('field', [
        {'pid': -1}, {'pid': 'x'}, {'pid': float('inf')}, {'pid': 2 ** 40},
        {'command_line': [None]}, {'activity_label': 5}, {'process_started_at': 'x'},
        {'started_monotonic': float('nan')}, {'run_id': 7}, {'origin': ['not', 'an', 'object']},
    ])
    def test_a_held_entry_with_a_field_of_another_type_is_named(self, registry_dir, field):
        """A foreign file names its own fields; one of them must not fail the list."""
        _write_entry(registry_dir, 'act_malformed', os.getpid(), psutil.Process().create_time(),
                     overrides=field)
        with FileLock(registry_dir / 'act_malformed.lock'):
            with ActivityRegistry().running(ActivityKind.IMPORT, 'well formed') as activity_id:
                listing = ActivityRegistryReader().read()
        assert [entry.activity_id for entry in listing.entries] == [activity_id]
        assert listing.unreadable_activity_ids == ['act_malformed']

    def test_an_entry_whose_lock_cannot_be_asked_about_is_named_and_kept(self, registry_dir,
                                                                         monkeypatch):
        _write_entry(registry_dir, 'act_theirs', os.getpid(), psutil.Process().create_time() - 5.0)
        asks = reader_module.is_lock_held

        def refused(path):
            if path.name == 'act_theirs.lock':
                raise PermissionError(13, 'Permission denied')
            return asks(path)

        monkeypatch.setattr(reader_module, 'is_lock_held', refused)
        listing = ActivityRegistryReader().read()
        assert listing.entries == []
        assert listing.unreadable_activity_ids == ['act_theirs']
        assert (registry_dir / 'act_theirs.json').exists()

    def test_a_removal_that_fails_is_left_for_a_later_reading(self, registry_dir, monkeypatch):
        _write_entry(registry_dir, 'act_dead', os.getpid(), psutil.Process().create_time() - 5.0)
        unlink = Path.unlink
        refusing = [True]

        def refused(path, missing_ok=False):
            if refusing[0] and path.name == 'act_dead.json':
                raise PermissionError(13, 'Permission denied')
            return unlink(path, missing_ok=missing_ok)

        monkeypatch.setattr(Path, 'unlink', refused)
        listing = ActivityRegistryReader().read()
        assert (listing.entries, listing.unreadable_activity_ids) == ([], [])
        assert (registry_dir / 'act_dead.json').exists()
        refusing[0] = False
        ActivityRegistryReader().read()
        assert not (registry_dir / 'act_dead.json').exists()


class TestRegisteringNeverEndsTheActivity:

    def test_a_directory_that_cannot_be_resolved_costs_a_warning(self, monkeypatch):
        # What the configuration answers where there is no home directory to expand `~` in
        monkeypatch.setattr(AppConfigManager, 'get_activity_registry_path',
                            lambda self: Path('~/.finiex/activities'))
        warnings = _Warnings()
        with ActivityRegistry().running(ActivityKind.AUTOTRADER_SESSION, 'bot',
                                        logger=warnings) as activity_id:
            assert activity_id is None
        assert 'cannot be resolved' in warnings.messages[0]
        with pytest.raises(OSError):
            ActivityRegistryReader().read()
        assert not Path('~').exists()

    def test_a_stop_between_the_lock_and_the_entry_leaves_nothing_behind(self, registry_dir,
                                                                        monkeypatch):
        def stopped(path, text, mode=None):
            raise KeyboardInterrupt

        monkeypatch.setattr(activity_registry_module, 'write_text_atomic', stopped)
        with pytest.raises(KeyboardInterrupt):
            with ActivityRegistry().running(ActivityKind.IMPORT, 'stopped'):
                pass
        assert list(registry_dir.iterdir()) == []

    def test_a_failed_write_whose_lock_cannot_be_released_never_ends_the_activity(
            self, registry_dir, monkeypatch):
        def unwritable(path, text, mode=None):
            raise OSError(28, 'No space left on device')

        def unreleasable(lock):
            raise OSError('stuck')

        monkeypatch.setattr(activity_registry_module, 'write_text_atomic', unwritable)
        monkeypatch.setattr(FileLock, 'release', unreleasable)
        warnings = _Warnings()
        ran = []
        with ActivityRegistry().running(ActivityKind.IMPORT, 'x', logger=warnings) as activity_id:
            ran.append(activity_id)
        assert ran == [None]
        assert 'Activity not listed' in warnings.messages[0]

    def test_a_lock_that_cannot_be_released_costs_a_warning(self, registry_dir, monkeypatch):
        release = FileLock.release

        def release_then_fail(lock):
            release(lock)
            raise OSError('stuck')

        monkeypatch.setattr(FileLock, 'release', release_then_fail)
        warnings = _Warnings()
        with ActivityRegistry().running(ActivityKind.IMPORT, 'x', logger=warnings):
            pass
        assert any('not released' in message for message in warnings.messages)

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


class TestTheConfiguredDirectoryStandsForItself:

    @pytest.mark.parametrize('directory', ['', '   ', 'data/runtime/activities', 'activities'])
    def test_an_empty_or_relative_directory_is_refused(self, directory):
        with pytest.raises(ValueError):
            ActivitiesConfig(directory=directory)

    @pytest.mark.parametrize('directory', ['~/.finiex/activities', '/srv/finiex/activities'])
    def test_an_absolute_or_home_directory_is_taken(self, directory):
        assert ActivitiesConfig(directory=directory).directory == directory


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
