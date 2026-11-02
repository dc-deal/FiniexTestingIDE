"""
FiniexTestingIDE - A Live Adapter Run That Declared Its Intent (#640)

The live adapter suite is a release gate and stays out of the daily runner, so what it does when it
has nothing to run is held here. Measured on a host whose key the suite could not see: exit 0, every
real phase skipped, no report, and the check after it passed on an older certificate.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

import tests.live_adapters.conftest as live_adapter_gate


def _config(place_real_orders: bool = False, release_version: str = 'dev') -> SimpleNamespace:
    """
    A pytest config answering the two options a run declares its intent with.

    Args:
        place_real_orders: The `--place-real-orders` flag
        release_version: The `--release-version` option

    Returns:
        The stand-in
    """
    options = {'place_real_orders': place_real_orders, 'release_version': release_version}
    return SimpleNamespace(getoption=lambda name, default=None: options.get(name, default))


@pytest.fixture
def missing_key(tmp_path, monkeypatch) -> Path:
    """The configured key points at a file that does not exist."""
    path = tmp_path / 'kraken_credentials.json'
    monkeypatch.setattr(live_adapter_gate, 'venue_credentials_path', lambda broker_type: path)
    return path


class TestAMissingKey:

    def test_a_bare_run_skips(self, missing_key):
        with pytest.raises(pytest.skip.Exception):
            live_adapter_gate.require_venue_key(SimpleNamespace(config=_config()), 'kraken_spot')

    def test_a_run_for_real_orders_fails(self, missing_key):
        request = SimpleNamespace(config=_config(place_real_orders=True))
        with pytest.raises(pytest.fail.Exception, match='no key'):
            live_adapter_gate.require_venue_key(request, 'kraken_spot')

    def test_a_run_for_a_release_fails(self, missing_key):
        request = SimpleNamespace(config=_config(release_version='1.5.0'))
        with pytest.raises(pytest.fail.Exception, match='no key'):
            live_adapter_gate.require_venue_key(request, 'kraken_spot')

    def test_a_key_that_is_there_is_handed_back(self, missing_key):
        missing_key.write_text('{}', encoding='utf-8')
        request = SimpleNamespace(config=_config(place_real_orders=True))
        assert live_adapter_gate.require_venue_key(request, 'kraken_spot') == missing_key


class TestARunThatPlacedNothing:

    @staticmethod
    def _session(place_real_orders: bool) -> SimpleNamespace:
        """
        A pytest session that ended well as far as pytest knows.

        Args:
            place_real_orders: The `--place-real-orders` flag

        Returns:
            The stand-in
        """
        return SimpleNamespace(config=_config(place_real_orders=place_real_orders),
                               exitstatus=pytest.ExitCode.OK)

    def test_it_fails_where_real_orders_were_asked_for(self, capsys):
        session = self._session(place_real_orders=True)
        results = SimpleNamespace(observed_phases=[{'phase': 'validate_only', 'dry_run': True}])
        live_adapter_gate.refuse_a_run_that_placed_nothing(session, results)
        assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
        assert 'no real-order phase ran' in capsys.readouterr().out

    def test_no_phase_at_all_fails_too(self):
        session = self._session(place_real_orders=True)
        live_adapter_gate.refuse_a_run_that_placed_nothing(
            session, SimpleNamespace(observed_phases=[]))
        assert session.exitstatus == pytest.ExitCode.TESTS_FAILED

    def test_a_real_order_phase_leaves_the_status_alone(self):
        session = self._session(place_real_orders=True)
        results = SimpleNamespace(observed_phases=[{'phase': 'real_orders', 'dry_run': False}])
        live_adapter_gate.refuse_a_run_that_placed_nothing(session, results)
        assert session.exitstatus == pytest.ExitCode.OK

    def test_a_run_that_asked_for_none_is_left_alone(self):
        session = self._session(place_real_orders=False)
        live_adapter_gate.refuse_a_run_that_placed_nothing(
            session, SimpleNamespace(observed_phases=[]))
        assert session.exitstatus == pytest.ExitCode.OK
