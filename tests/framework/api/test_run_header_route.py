"""
A run's header over the API (#582): the whole record with every path relative to its repository,
and who started a run on the run list.
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from python.api.api_app import create_app
from python.framework.reporting.io.run_header_io import RUN_HEADER_ARTIFACT, write_run_header
from python.framework.reporting.store.report_store import ReportStore
from python.framework.reporting.store.served_run_header import served_run_header
from python.framework.reporting.store.run_index import RunIndex
from python.framework.types.api.report_types import RunHeader
from python.framework.types.log_layout_types import RUN_TYPE_SIMULATION
from python.framework.types.run_origin_types import (
    CodeIdentity,
    ComponentIdentity,
    ComponentRole,
    RepositoryState,
    RunChannel,
    RunOrigin,
)

_RUN = '20261009_120000_aaaaaaaa'
_URL = f'/api/v1/reports/runs/{_RUN}/header'


def _header(machine: Path) -> RunHeader:
    """
    A header as a run on another machine records it: absolute paths throughout.

    Args:
        machine: Where that machine's checkout lies

    Returns:
        The header
    """
    algos = machine / 'user_algos'
    decision = str(algos / 'my_bot' / 'my_logic.py')
    return RunHeader(
        run_id=_RUN, start_time=datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc),
        run_type=RUN_TYPE_SIMULATION, run_name='my_set', config_snapshot='my_set.json',
        origin=RunOrigin(channel=RunChannel.CLI, client='console', principal='operator',
                         host='h_test01'),
        code_identity=CodeIdentity(
            framework=RepositoryState(root=str(machine), commit='abc1234',
                                      patch_ref=str(machine / 'run_patches' / 'b53b.patch')),
            repositories=[RepositoryState(root=str(algos), commit='def5678')],
            components=[
                # A decision is recorded under its type — here a path, as a profile may name it.
                ComponentIdentity(role=ComponentRole.DECISION, name=decision, type=decision,
                                  source_path=decision, repository=str(algos)),
                ComponentIdentity(role=ComponentRole.WORKER, name='rsi_fast', type='CORE/rsi',
                                  source_path=str(machine / 'python' / 'rsi_worker.py'),
                                  repository=str(machine)),
                ComponentIdentity(role=ComponentRole.WORKER, name='loose', type='CORE/obv',
                                  source_path='/elsewhere/entirely/obv_worker.py'),
            ]))


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    """The planted run's directory, its header written and indexed the way a real run does it."""
    directory = tmp_path / 'logs' / 'simulation' / 'my_set' / _RUN
    header = _header(tmp_path / 'machine')
    write_run_header(header, directory)
    RunIndex(tmp_path / 'index.parquet').register_run(header, directory)
    return directory


@pytest.fixture
def client(tmp_path: Path, run_dir: Path):
    """The API, reading the tmp tree's index."""
    with patch('python.api.endpoints.reports_router.ReportStore',
               lambda: ReportStore(tmp_path / 'index.parquet')):
        yield TestClient(create_app())


class TestTheHeaderIsServedWhole:

    def test_it_answers_with_the_run_and_who_started_it(self, client):
        body = client.get(_URL).json()
        assert (body['run_id'], body['config_snapshot']) == (_RUN, 'my_set.json')
        assert body['origin'] == {'channel': 'cli', 'client': 'console', 'principal': 'operator',
                                  'host': 'h_test01', 'allow_dirty': False}

    def test_every_path_is_relative_to_its_repository(self, client):
        identity = client.get(_URL).json()['code_identity']
        assert identity['framework']['root'] == '.'
        assert identity['framework']['patch_ref'] == 'run_patches/b53b.patch'
        assert identity['repositories'][0]['root'] == 'user_algos'
        decision, worker, loose = identity['components']
        assert (decision['name'], decision['type'], decision['source_path'],
                decision['repository']) == ('user_algos/my_bot/my_logic.py',
                                            'user_algos/my_bot/my_logic.py',
                                            'my_bot/my_logic.py', 'user_algos')
        assert (worker['type'], worker['source_path'], worker['repository']) == (
            'CORE/rsi', 'python/rsi_worker.py', '.')
        assert loose['source_path'] == 'obv_worker.py', 'a path in no repository is its name'

    def test_no_path_of_the_machine_leaves(self, client, tmp_path):
        text = client.get(_URL).text
        assert str(tmp_path) not in text and '/elsewhere' not in text


class TestAHeaderThatCannotBeServedSaysWhy:

    def test_an_unknown_run_is_not_found(self, client):
        response = client.get('/api/v1/reports/runs/20261009_120000_bbbbbbbb/header')
        assert (response.status_code, response.json()['error']) == (404, 'run_not_found')

    def test_a_missing_header_file_has_its_own_cause(self, client, run_dir):
        (run_dir / RUN_HEADER_ARTIFACT).unlink()
        response = client.get(_URL)
        assert (response.status_code, response.json()['error']) == (404, 'run_header_missing')
        assert 'rebuild' in response.json()['detail']

    def test_a_header_that_no_longer_matches_names_fields_and_never_a_value(self, client, run_dir):
        """Pydantic's own text quotes the input, and a header's input holds paths."""
        (run_dir / RUN_HEADER_ARTIFACT).write_text(
            '{"run_id": "x", "code_identity": {"framework": {"root": "/home/someone/secret"}}}',
            encoding='utf-8')
        response = client.get(_URL)
        assert (response.status_code, response.json()['error']) == (409, 'artifact_unreadable')
        assert '/home/someone' not in response.text and 'start_time' in response.json()['detail']


class TestTheRunListSaysWhoStartedEachRun:

    def test_the_origin_is_flattened_onto_the_row(self, client):
        row = next(run for run in client.get('/api/v1/reports/runs').json()['runs']
                   if run['run_id'] == _RUN)
        assert (row['origin_channel'], row['origin_client'], row['origin_principal'],
                row['origin_host']) == ('cli', 'console', 'operator', 'h_test01')


class TestAPathFromAnotherSystemIsServedTheSameWay:

    def test_a_windows_path_is_made_relative_too(self):
        """A header written on Windows names its paths with a drive and backslashes."""
        root = 'C:\\Users\\someone\\FiniexTestingIDE'
        header = RunHeader(
            run_id=_RUN, start_time=datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc),
            run_type=RUN_TYPE_SIMULATION, run_name='my_set',
            code_identity=CodeIdentity(
                framework=RepositoryState(root=root),
                components=[ComponentIdentity(
                    role=ComponentRole.WORKER, name='rsi_fast', type='CORE/rsi',
                    source_path=root + '\\python\\rsi_worker.py', repository=root)]))

        served = served_run_header(header).code_identity

        assert served.framework.root == '.'
        assert (served.components[0].source_path, served.components[0].repository) == (
            'python/rsi_worker.py', '.')
