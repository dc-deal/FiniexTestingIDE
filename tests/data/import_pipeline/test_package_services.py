"""
Package Service Tests (#120)

Every package service an import can fetch from is declared in its own peer's file — the data
collector's in data_collector_config.json, the RAG engine's in sentiment_config.json — with a switch
that is off until a workspace copy turns it on. One builder decides for both what "on" means, one
registry lists them, and `data_index_cli.py package-sources` shows each one: what it delivers, its
switch, its endpoint and address, the credential as the cascade answered it — never the token.
"""

import importlib
import inspect
import json
import os
import pkgutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel

import python.framework.types.config_types as config_types_package
from python.configuration.data_collector_config_manager import DataCollectorConfigManager
from python.configuration.package_service_registry import (
    PACKAGE_SERVICE_MANAGERS,
    resolve_package_services,
)
from python.configuration.sentiment_config_manager import SentimentConfigManager
from python.data_management.importers.package_service_status import (
    any_list_unread,
    build_package_service_status,
)
from python.framework.reporting.console.package_services_summary import render_package_services
from python.framework.types.config_types.package_service_config_types import (
    ActivePackageSource,
    PackageFetchConfig,
    package_connection,
)
from python.framework.types.config_types.sentiment_config_types import SentimentConfig
from python.framework.types.import_types.import_progress_types import ImportSection
from python.framework.types.import_types.package_types import (
    PackageListing,
    PackageServiceResolution,
    PackageServiceState,
    PackageState,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _sentiment(tmp_path, monkeypatch, override: dict) -> PackageServiceResolution:
    """
    The RAG engine's package service under a workspace copy holding the given override.

    Args:
        tmp_path: Where the copy is written
        monkeypatch: To let the manager read the copy despite the test session's isolation
        override: The copy's content

    Returns:
        The service's resolution
    """
    copy = tmp_path / 'sentiment_config.json'
    copy.write_text(json.dumps(override), encoding='utf-8')
    monkeypatch.setattr(
        'python.configuration.sentiment_config_manager.is_config_isolation_active', lambda: False)
    return SentimentConfigManager(user_config_path=str(copy)).resolve_package_source()


class TestTheRagEnginesService:
    """Fetched from the active producer endpoint — the address and the file's name, never the
    token — and switched in sentiment_config.json's own `packages` block."""

    def test_the_tracked_file_keeps_it_switched_off(self):
        resolution = SentimentConfigManager(
            user_config_path='/nonexistent/override.json').resolve_package_source()
        assert resolution.state is PackageServiceState.SWITCHED_OFF
        assert resolution.source is None
        assert (resolution.package_service, resolution.delivers) == ('rag', ImportSection.SIGNALS)

    def test_switched_on_it_fetches_from_the_active_producer_endpoint(self, tmp_path, monkeypatch):
        resolution = _sentiment(tmp_path, monkeypatch, {
            'packages': {'enabled': True},
            'producer': {'active': 'production', 'endpoints': {'production': {
                'base_url': 'https://rag.example.test/'}}}})

        assert resolution.state is PackageServiceState.ON
        assert resolution.source.base_url == 'https://rag.example.test'
        assert resolution.source.credentials_file == 'peers/rag_credentials.json'
        assert resolution.source.connection.attempt_budget == 3

    def test_switched_on_without_an_address_it_says_so(self, tmp_path, monkeypatch):
        resolution = _sentiment(tmp_path, monkeypatch, {
            'packages': {'enabled': True}, 'producer': {'active': 'production'}})

        assert resolution.state is PackageServiceState.NO_ADDRESS
        assert resolution.source is None
        assert resolution.describe_state() == 'rag: no address'

    def test_the_tracked_block_mirrors_the_model(self):
        tracked = json.loads((_PROJECT_ROOT / 'configs/sentiment_config.json').read_text())
        assert (PackageFetchConfig.model_validate(tracked['packages']).model_dump()
                == SentimentConfig().packages.model_dump())


def _models_with_a_package_block() -> set:
    """
    Every configuration model, found by walking the configuration types, that carries a package
    service block as one of its fields.

    Returns:
        The model classes
    """
    found = set()
    for module_info in pkgutil.iter_modules(config_types_package.__path__):
        module = importlib.import_module(f'{config_types_package.__name__}.{module_info.name}')
        for _, model in inspect.getmembers(module, inspect.isclass):
            if not issubclass(model, BaseModel) or model.__module__ != module.__name__:
                continue
            if any(inspect.isclass(field.annotation) and issubclass(field.annotation,
                                                                     PackageFetchConfig)
                   for field in model.model_fields.values()):
                found.add(model)
    return found


class TestTheRegistry:

    def test_it_lists_every_peer_whose_file_declares_a_package_service(self):
        registered = {type(manager().get_config()) for manager in PACKAGE_SERVICE_MANAGERS}
        found = _models_with_a_package_block()
        assert found, 'the walk must find the package blocks, or this proves nothing'
        assert registered == found

    def test_every_service_resolves_with_a_name_of_its_own(self):
        services = resolve_package_services()
        names = [service.package_service for service in services]
        assert len(names) == len(set(names)) == len(PACKAGE_SERVICE_MANAGERS)

    def test_the_collector_comes_first_and_delivers_ticks(self):
        assert DataCollectorConfigManager().resolve_package_source().delivers is ImportSection.TICKS
        assert resolve_package_services()[0].package_service == 'collector'


def _resolution(state: PackageServiceState, address_set: bool = True,
                credentials_file: str = 'peers/no_such_credentials.json'
                ) -> PackageServiceResolution:
    """A service in the given state, with its source when it is on."""
    source = ActivePackageSource(
        package_service='rag', delivers=ImportSection.SIGNALS, endpoint_name='production',
        base_url='https://rag.example.test', credentials_file=credentials_file,
        connection=package_connection()) if state is PackageServiceState.ON else None
    return PackageServiceResolution(
        package_service='rag', delivers=ImportSection.SIGNALS, state=state,
        endpoint_name='production', address_set=address_set, credentials_file=credentials_file,
        source=source)


class _StubClient:
    """A service whose list holds the given packages, or that does not answer."""

    def __init__(self, listings: Optional[List[PackageListing]]):
        self._listings = listings

    def list_packages(self) -> Optional[List[PackageListing]]:
        return self._listings


def _listing(package_id: str, state: PackageState) -> PackageListing:
    return PackageListing(package_id=package_id, source='crypto_sentiment', size_bytes=1,
                          sha256='ab' * 32, state=state)


class TestTheStatusRows:

    def test_without_a_check_no_list_is_read(self):
        row = build_package_service_status(
            [_resolution(PackageServiceState.ON)], check=False,
            client_factory=lambda source: _StubClient(None))[0]
        assert (row.waiting, row.acknowledged, row.last_listed_package, row.check_note) == (
            None, None, None, '')
        assert row.credential == 'no credentials file found'

    def test_a_check_counts_what_waits_and_what_was_acknowledged(self):
        listings = [_listing('crypto_sentiment-0001-20261116', PackageState.DELIVERED),
                    _listing('crypto_sentiment-0002-20261122', PackageState.READY)]
        row = build_package_service_status(
            [_resolution(PackageServiceState.ON)], check=True,
            client_factory=lambda source: _StubClient(listings))[0]
        assert (row.waiting, row.acknowledged) == (1, 1)
        assert row.last_listed_package == 'crypto_sentiment-0002-20261122'
        assert not any_list_unread([row])

    def test_a_check_reads_no_list_of_a_service_that_is_off_or_silent(self):
        off, silent = build_package_service_status(
            [_resolution(PackageServiceState.SWITCHED_OFF), _resolution(PackageServiceState.ON)],
            check=True, client_factory=lambda source: _StubClient(None))
        assert off.check_note == 'not read — rag: switched off'
        assert silent.check_note == 'not read — the service did not answer'
        assert silent.waiting is None
        # A switched-off service is not a failed check; a silent switched-on one is
        assert not any_list_unread([off]) and any_list_unread([off, silent])

    def test_a_malformed_list_fails_its_own_row_only(self):
        class _Malformed:
            def list_packages(self):
                raise ValueError('state is not a known word')

        listings = [_listing('crypto_sentiment-0001-20261116', PackageState.READY)]
        clients = iter([_Malformed(), _StubClient(listings)])
        broken, fine = build_package_service_status(
            [_resolution(PackageServiceState.ON), _resolution(PackageServiceState.ON)],
            check=True, client_factory=lambda source: next(clients))
        assert broken.check_note == ('not read — its list could not be read: '
                                     'state is not a known word')
        assert fine.waiting == 1

    def test_the_table_shows_each_service_and_never_a_token(self, capsys):
        rows = build_package_service_status(
            [_resolution(PackageServiceState.SWITCHED_OFF, address_set=False)], check=True,
            client_factory=lambda source: _StubClient(None))
        render_package_services(rows, checked=True)
        out = capsys.readouterr().out
        assert 'rag' in out and 'signals' in out and 'switched off' in out
        assert 'not read — rag: switched off' in out
        assert 'api_token' not in out


class TestTheCommand:

    def test_package_sources_lists_the_tracked_services_switched_off(self):
        result = subprocess.run(
            [sys.executable, 'python/cli/data_index_cli.py', 'package-sources'],
            cwd=_PROJECT_ROOT, capture_output=True, text=True, timeout=120,
            env={**os.environ, 'PYTHONPATH': str(_PROJECT_ROOT)})
        assert result.returncode == 0, result.stderr
        assert 'collector' in result.stdout and 'rag' in result.stdout
        assert result.stdout.count('switched off') >= 2
