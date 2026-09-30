"""
Directory routes (#554) — the wiring from the router to the config directory.

What a row SAYS is pinned in `tests/framework/config_directory/`; here only what the routes do
with it: the list passes `refresh` through, the detail answers a known file, and an unknown one is
`config_file_not_found` from the error catalog rather than a bare 404.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from python.api.api_app import create_app
from python.framework.types.api.directory_types import (
    DirectoryDetailResponse,
    DirectoryListResponse,
    DirectoryRow,
)
from python.framework.types.config_directory_types import (
    ConfigKind,
    ConfigOrigin,
    ConfigReadStatus,
)

_ROW = DirectoryRow(kind=ConfigKind.SCENARIO_SET, file='my_set.json', origin=ConfigOrigin.CONFIGS,
                    status=ConfigReadStatus.READABLE, scenarios_declared=3, scenarios_enabled=2,
                    run_count=0)


@pytest.fixture
def directory():
    """A directory double the router is pointed at, and the client that calls it."""
    double = MagicMock()
    with patch('python.api.endpoints.directory_router._directory', return_value=double):
        yield double, TestClient(create_app())


class TestDirectory:

    def test_the_list_serves_every_row_and_declares_its_key(self, directory):
        double, client = directory
        double.list_configs.return_value = DirectoryListResponse(rows=[_ROW], count=1)

        response = client.get('/api/v1/directory')

        assert response.status_code == 200
        body = response.json()
        assert body['key'] == ['file'] and body['count'] == 1
        assert body['rows'][0]['run_count'] == 0, 'a file that never ran is a row, not an absence'
        double.list_configs.assert_called_once_with(refresh=False)

    def test_refresh_walks_now(self, directory):
        double, client = directory
        double.list_configs.return_value = DirectoryListResponse(rows=[], count=0)

        client.get('/api/v1/directory', params={'refresh': 'true'})

        double.list_configs.assert_called_once_with(refresh=True)

    def test_the_detail_of_a_known_file(self, directory):
        double, client = directory
        double.detail.return_value = DirectoryDetailResponse(row=_ROW, runs=['r2', 'r1'])

        response = client.get('/api/v1/directory/my_set.json')

        assert response.status_code == 200
        assert response.json()['runs'] == ['r2', 'r1']
        double.detail.assert_called_once_with('my_set.json')

    def test_an_unknown_file_names_its_cause(self, directory):
        double, client = directory
        double.detail.return_value = None

        response = client.get('/api/v1/directory/nope.json')

        assert response.status_code == 404
        assert response.json()['error'] == 'config_file_not_found'
