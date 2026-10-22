"""
`GET /api/v1/activities` (#603): what runs on the machine, as the API serves it — every field, no
path of the machine, the server's own row, the machine's memory read beside the rows, and a list
that cannot be read refused rather than served empty.
"""

import json
import os
import sys
from pathlib import Path

import psutil
import pytest
from fastapi.testclient import TestClient

from python.api.api_app import create_app
from python.configuration.app_config_manager import AppConfigManager
from python.framework.activities.activity_registry import ActivityRegistry
from python.framework.activities.activity_registry_reader import ActivityRegistryReader
from python.framework.types.activity_registry_types import ACTIVITY_ENTRY_SCHEMA, ActivityKind
from python.framework.types.api.api_types import (
    ActivityListResponse,
    ActivityRow,
    MachineMemoryResponse,
)
from python.framework.utils.file_lock import FileLock
from python.framework.utils.machine_resources_reader import MachineResourcesReader


@pytest.fixture
def registry_dir(tmp_path, monkeypatch):
    """A registry of the test's own, set before the app builds its reader."""
    directory = tmp_path / 'activities'
    monkeypatch.setattr(AppConfigManager, 'get_activity_registry_path', lambda self: directory)
    return directory


def _get():
    return TestClient(create_app()).get('/api/v1/activities')


def _hand_written_entry(directory, activity_id, working_tree='/somewhere', schema=None):
    """An entry of THIS process written by hand — the way another code tree would write one."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f'{activity_id}.json').write_text(json.dumps({
        'schema_version': ACTIVITY_ENTRY_SCHEMA if schema is None else schema,
        'activity_id': activity_id, 'activity_kind': 'autotrader_session',
        'activity_state': 'running', 'activity_label': 'my_bot', 'pid': os.getpid(),
        'process_started_at': psutil.Process().create_time(), 'command_line': ['x'],
        'started_at': '2026-10-19T08:00:00+00:00', 'started_monotonic': 0.0,
        'working_tree': working_tree, 'console_session': '', 'git_commit': None,
        'origin': None, 'run_id': None, 'sweep_id': None, 'parent_activity_id': None,
    }))
    return FileLock(directory / f'{activity_id}.lock')


class TestTheListIsWhatRunsNow:

    def test_a_running_activity_is_served_with_every_field(self, registry_dir):
        with ActivityRegistry().running(ActivityKind.BACKTEST, 'my_set', run_id='r1'):
            body = _get().json()
        assert set(body) == set(ActivityListResponse.model_fields)
        assert body['key'] == ['activity_id']
        [row] = body['activities']
        assert set(row) == set(ActivityRow.model_fields)
        assert (row['activity_kind'], row['activity_label'], row['run_id']) == (
            'backtest', 'my_set', 'r1')
        assert row['activity_state'] == 'running'
        assert row['process_count'] >= 1 and row['rss_bytes'] > 0

    def test_an_ended_activity_is_not_served(self, registry_dir):
        with ActivityRegistry().running(ActivityKind.IMPORT, 'x'):
            pass
        assert _get().json()['activities'] == []

    def test_a_missing_registry_is_an_empty_list(self, registry_dir):
        response = _get()
        assert response.status_code == 200
        assert response.json()['activities'] == []

    def test_an_unreadable_registry_is_refused_and_names_no_path(self, registry_dir, monkeypatch):
        """The suite runs as root, so the fault is injected rather than made with chmod."""
        def refuse(self):
            raise PermissionError(13, 'Permission denied', '/srv/finiex/home/activities')

        monkeypatch.setattr(ActivityRegistryReader, 'read', refuse)
        response = _get()
        assert response.status_code == 500
        assert response.json()['error'] == 'activity_registry_unreadable'
        assert 'Permission denied' in response.json()['detail']
        assert '/srv' not in response.json()['detail']

    def test_one_entry_that_is_no_object_does_not_fail_the_list(self, registry_dir):
        registry_dir.mkdir()
        (registry_dir / 'act_array.json').write_text('[]')
        with FileLock(registry_dir / 'act_array.lock'):
            response = _get()
        assert response.status_code == 200
        assert response.json()['unreadable_activity_ids'] == ['act_array']

    def test_a_directory_that_cannot_be_resolved_is_refused_as_unreadable(self, monkeypatch):
        # What the configuration answers where there is no home directory to expand `~` in
        monkeypatch.setattr(AppConfigManager, 'get_activity_registry_path',
                            lambda self: Path('~/.finiex/activities'))
        response = _get()
        assert response.status_code == 500
        assert response.json()['error'] == 'activity_registry_unreadable'

    def test_a_held_entry_this_server_cannot_read_is_named(self, registry_dir):
        lock = _hand_written_entry(registry_dir, 'act_foreign', schema=99)
        try:
            body = _get().json()
        finally:
            lock.release()
        assert body['activities'] == []
        assert body['unreadable_activity_ids'] == ['act_foreign']


class TestNoPathOfTheMachineIsServed:

    def test_the_command_line_is_relative_and_the_own_tree_is_a_dot(self, registry_dir,
                                                                     monkeypatch):
        root = os.getcwd()
        monkeypatch.setattr(sys, 'argv', [f'{root}/python/cli/strategy_runner_cli.py', 'run',
                                          '--set', f'{root}/configs/my_set.json'])
        with ActivityRegistry().running(ActivityKind.BACKTEST, 'my_set'):
            [row] = _get().json()['activities']
        assert row['working_tree'] == '.'
        assert row['command_line'] == ('python/cli/strategy_runner_cli.py run --set '
                                       'configs/my_set.json')

    def test_a_value_written_with_its_name_is_relative_too(self, registry_dir, monkeypatch):
        root = os.getcwd()
        monkeypatch.setattr(sys, 'argv', [f'{root}/python/cli/autotrader_cli.py', 'run',
                                          f'--config={root}/configs/my_bot.json',
                                          '--secrets=/home/someone/private/keys.json'])
        with ActivityRegistry().running(ActivityKind.AUTOTRADER_SESSION, 'my_bot'):
            [row] = _get().json()['activities']
        assert row['command_line'] == ('python/cli/autotrader_cli.py run '
                                       '--config=configs/my_bot.json --secrets=keys.json')

    def test_a_console_session_named_by_a_path_leaves_by_its_last_part(self, registry_dir,
                                                                       monkeypatch):
        monkeypatch.setattr(AppConfigManager, 'get_activity_session_env_var',
                            lambda self: 'TMUX')
        monkeypatch.setenv('TMUX', '/tmp/tmux-0/default,1234,0')
        with ActivityRegistry().running(ActivityKind.IMPORT, 'x'):
            [row] = _get().json()['activities']
        assert row['console_session'] == 'default,1234,0'

    def test_another_code_tree_is_named_by_its_directory(self, registry_dir):
        lock = _hand_written_entry(registry_dir, 'act_release',
                                   working_tree='/srv/finiex/releases/v1.5.1')
        try:
            [row] = _get().json()['activities']
        finally:
            lock.release()
        assert row['working_tree'] == 'v1.5.1'


class TestTheServerFindsItself:

    def test_a_server_that_is_not_listed_is_null(self, registry_dir):
        assert _get().json()['server_activity_id'] is None

    def test_the_server_names_its_own_row(self, registry_dir):
        with ActivityRegistry().running(ActivityKind.API_SERVER, 'port 8000') as activity_id:
            body = _get().json()
        assert body['server_activity_id'] == activity_id


class TestOneInstant:

    def test_the_machine_memory_is_read_without_moving_the_processor_window(self, registry_dir,
                                                                            monkeypatch):
        """`/machine-resources` measures its busy share since the previous full reading."""
        full_readings = []
        real_read = MachineResourcesReader.read

        def counted(self):
            full_readings.append(1)
            return real_read(self)

        monkeypatch.setattr(MachineResourcesReader, 'read', counted)
        body = _get().json()
        assert full_readings == []
        assert MachineMemoryResponse(**body['machine_memory']).limit_bytes > 0
        assert body['measured_at'].endswith('+00:00')
        assert body['pressure_level'] in ('ok', 'warn', 'critical')
