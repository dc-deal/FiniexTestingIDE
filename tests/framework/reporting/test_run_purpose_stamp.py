"""
What a run is FOR, on its header from its first second (#576).

Both pipelines stamp the purpose their configuration declares, and the contract their reports
will be written under, into the header they write at the start. And the test session refuses a
run a test starts from a configuration that is not a fixture — the guard every other test in
this suite runs under.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from python.api.api_contract import API_CONTRACT_VERSION
from python.configuration.app_config_manager import AppConfigManager
from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_startup import create_autotrader_loggers
from python.framework.reporting.io.run_header_io import RUN_HEADER_ARTIFACT, read_run_header
from python.framework.reporting.store.run_index import RunIndex
from python.framework.types.api.report_types import RunHeader, RunReporting
from python.framework.types.config_types.host_identity_config_types import TEST_HOST_ID
from python.framework.types.run_origin_types import (
    CONSOLE_CLIENT,
    OPERATOR_PRINCIPAL,
    RunChannel,
    RunOrigin,
)
from python.framework.types.run_purpose_types import RunPurpose
from python.scenario.scenario_config_loader import ScenarioConfigLoader
from python.scenario.scenario_set import ScenarioSet

_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE_SET = _ROOT / 'tests' / 'fixtures' / 'scenario_sets' / 'cascade' / 'no_overrides.json'
_FIXTURE_PROFILE = _ROOT / 'configs' / 'autotrader_profiles' / 'mock' / 'mock_session_test.json'
_ORIGIN = RunOrigin(channel=RunChannel.CLI, client=CONSOLE_CLIENT, principal=OPERATOR_PRINCIPAL,
                    host=TEST_HOST_ID)


def _set_header(config_path: Path) -> RunHeader:
    """
    The header a scenario set writes when its run is set up — before a tick is read.

    Args:
        config_path: The scenario set to start from

    Returns:
        The header as written into the run's directory
    """
    scenario_set = ScenarioSet(ScenarioConfigLoader().load_config(str(config_path)),
                               AppConfigManager(), reporting=RunReporting.NONE)
    return read_run_header(Path(scenario_set.logger.get_log_dir()) / RUN_HEADER_ARTIFACT)


def _session_header(config_path: Path) -> RunHeader:
    """
    The header an AutoTrader session writes at its start.

    Args:
        config_path: The profile to start from

    Returns:
        The header as written into the session's directory
    """
    bundle = create_autotrader_loggers(load_autotrader_config(str(config_path)),
                                       datetime.now(timezone.utc), origin=_ORIGIN,
                                       code_identity=None)
    return read_run_header(bundle.run_dir / RUN_HEADER_ARTIFACT)


def _without_purpose(source: Path, tmp_path: Path) -> Path:
    """
    A copy of a configuration that declares nothing — and is therefore regular.

    Args:
        source: The configuration to copy
        tmp_path: Where the copy goes

    Returns:
        The copy's path
    """
    data = json.loads(source.read_text(encoding='utf-8'))
    data.pop('run_purpose')
    path = tmp_path / f'regular_{source.name}'
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')
    return path


class TestBothPipelinesStampThePurpose:

    def test_a_backtest_of_a_fixture_set_says_so(self):
        header = _set_header(_FIXTURE_SET)
        assert (header.run_purpose, header.report_contract) == (
            RunPurpose.FIXTURE, API_CONTRACT_VERSION)

    def test_a_session_of_a_fixture_profile_says_so(self):
        header = _session_header(_FIXTURE_PROFILE)
        assert (header.run_purpose, header.report_contract) == (
            RunPurpose.FIXTURE, API_CONTRACT_VERSION)

    def test_a_set_that_declares_nothing_is_stamped_regular(self, tmp_path, any_run_purpose):
        assert _set_header(_without_purpose(_FIXTURE_SET, tmp_path)).run_purpose \
            is RunPurpose.REGULAR

    def test_a_profile_that_declares_nothing_is_stamped_regular(self, tmp_path, any_run_purpose):
        assert _session_header(_without_purpose(_FIXTURE_PROFILE, tmp_path)).run_purpose \
            is RunPurpose.REGULAR


class TestTheSessionRefusesARunThatIsNotAFixture:
    """The guard in the root conftest — every run a test starts passes through it."""

    def test_a_regular_set_started_by_a_test_fails_the_test(self, tmp_path):
        with pytest.raises(pytest.fail.Exception, match='run_purpose "regular"'):
            _set_header(_without_purpose(_FIXTURE_SET, tmp_path))

    def test_a_certificate_header_is_refused_at_the_index(self, tmp_path):
        header = RunHeader(run_id='20261008_120000_aaaaaaaa', start_time=datetime.now(timezone.utc),
                           run_type='autotrader', run_name='field_study',
                           config_snapshot='kraken_spot_ethusd_field_study.json',
                           run_purpose=RunPurpose.CERTIFICATE)
        with pytest.raises(pytest.fail.Exception, match='kraken_spot_ethusd_field_study.json'):
            RunIndex(tmp_path / 'index.parquet').register_run(header, tmp_path / 'run')
