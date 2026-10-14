"""
Polled Route Access Filter — a route made to be polled stays out of the access log (#595).

The viewer asks `/machine-resources` every few seconds, and the server's access log wrote a line
per reading until the requests that carry meaning could no longer be found between them. A
successful answer on a polled route is dropped; a failed one stays, because the log is read when
something went wrong.
"""

import logging

import pytest

from python.api.api_app import POLLED_PATHS, create_app
from python.api.polled_route_access_filter import ACCESS_LOGGER_NAME, PolledRouteAccessFilter

_POLLED = '/api/v1/machine-resources'


def _access_record(path: str, status: int) -> logging.LogRecord:
    """An access record exactly as uvicorn emits one."""
    return logging.LogRecord(
        ACCESS_LOGGER_NAME, logging.INFO, __file__, 0, '%s - "%s %s HTTP/%s" %d',
        ('172.18.0.3:39228', 'GET', path, '1.1', status), None)


class TestOnlyASuccessfulReadingIsDropped:

    @pytest.fixture
    def access_filter(self) -> PolledRouteAccessFilter:
        return PolledRouteAccessFilter(frozenset({_POLLED}))

    @pytest.mark.parametrize('status', [200, 304])
    def test_a_successful_reading_of_a_polled_route_is_dropped(self, access_filter, status):
        assert access_filter.filter(_access_record(_POLLED, status)) is False

    @pytest.mark.parametrize('status', [401, 403, 500])
    def test_a_failed_reading_of_a_polled_route_stays(self, access_filter, status):
        assert access_filter.filter(_access_record(_POLLED, status)) is True

    def test_another_route_stays(self, access_filter):
        assert access_filter.filter(_access_record('/api/v1/caller', 200)) is True

    def test_a_query_does_not_hide_the_path(self, access_filter):
        assert access_filter.filter(_access_record(f'{_POLLED}?pretty=1', 200)) is False

    def test_a_record_not_shaped_like_an_access_line_is_left_alone(self, access_filter):
        record = logging.LogRecord(ACCESS_LOGGER_NAME, logging.INFO, __file__, 0,
                                   'Started server process', None, None)
        assert access_filter.filter(record) is True


class TestTheDeclarationHoldsToTheApp:

    def test_every_polled_path_is_a_mounted_route(self):
        """A renamed route would otherwise leave a declaration that filters nothing."""
        mounted = set(create_app().openapi()['paths'])
        assert POLLED_PATHS and POLLED_PATHS <= mounted

    def test_the_factory_installs_one_filter_however_often_it_runs(self):
        create_app()
        create_app()
        installed = [f for f in logging.getLogger(ACCESS_LOGGER_NAME).filters
                     if isinstance(f, PolledRouteAccessFilter)]
        assert len(installed) == 1
