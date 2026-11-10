"""
Tick Package Fetch Tests (#120)

The import fetches the data collector's packages before it scans the inbox, proves each zip by
its hash, holds its files to the manifest, imports it like any zip, and acknowledges each package
whose zip reached the finished directory — with the hash of the zip it holds. Run against a stub
package service on a local port.
"""

import hashlib
import io
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import pytest

from python.configuration.data_collector_config_manager import DataCollectorConfigManager
from python.data_management.importers.import_coordinator import ImportCoordinator
from python.data_management.importers.tick_package_client import TickPackageClient
from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.types.config_types.data_collector_config_types import (
    ActivePackageSource,
    DataCollectorConfig,
)
from python.framework.types.connection_types import GiveUpAction
from python.framework.types.import_types.import_progress_types import (
    ImportOutcome,
    ImportPaths,
    ImportStepId,
    ImportStepStatus,
)
from tests.data.import_pipeline.conftest import build_minimal_tick_json, write_json_fixture

TOKEN = 's3cret'


def _export_name(symbol: str, start_time: str) -> str:
    """The collector's file name for an export starting at start_time."""
    stamp = start_time.replace('.', '').replace(':', '').replace(' ', '_')
    return f'{symbol}_{stamp}_ticks.json'


def _export_bytes(symbol: str, start_time: str) -> bytes:
    """A minimal valid export, as bytes."""
    return json.dumps(build_minimal_tick_json(symbol=symbol, start_time=start_time)).encode()


def _zip_bytes(members: Dict[str, bytes]) -> bytes:
    """A Deflate zip of name → bytes members."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _sha(data: bytes) -> str:
    """SHA-256 of bytes."""
    return hashlib.sha256(data).hexdigest()


class _PackageService:
    """A stub of the collector's package service: the four routes the import uses."""

    def __init__(self) -> None:
        self.packages: Dict[str, Dict[str, Any]] = {}
        self.requests: List[Tuple[str, str, Dict[str, str]]] = []
        self.cut_zip_once: Set[str] = set()
        self.receipt_status: Optional[int] = None
        self.drop_receipt_answer = False
        self._server: Optional[ThreadingHTTPServer] = None

    def add(self, package_id: str, members: Dict[str, bytes], state: str = 'ready') -> bytes:
        """Register a package; returns its zip."""
        data = _zip_bytes(members)
        zip_name = f'{package_id}.zip'
        self.packages[package_id] = {
            'zip': data,
            'listing': {'package_id': package_id, 'collector': package_id.split('-')[0],
                        'size_bytes': len(data), 'sha256': _sha(data), 'state': state,
                        'received_at': None},
            'manifest': {'schema': 1, 'package_id': package_id,
                         'zip': {'name': zip_name, 'size_bytes': len(data),
                                 'sha256': _sha(data), 'compression': 'deflate'},
                         'members': [{'name': name, 'sha256': _sha(body)}
                                     for name, body in members.items()],
                         'excluded': [], 'held_for_next': [], 'open_files': []},
            'receipt': None,
        }
        return data

    def get_base_url(self) -> str:
        """Where the stub listens."""
        return f'http://127.0.0.1:{self._server.server_address[1]}'

    def start(self) -> None:
        """Serve on a free local port."""
        self._server = ThreadingHTTPServer(('127.0.0.1', 0), self._handler())
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        """Stop serving."""
        self._server.shutdown()
        self._server.server_close()

    def count(self, method: str, path_end: str) -> int:
        """How many requests of a method ended with a path."""
        return sum(1 for m, path, _ in self.requests if m == method and path.endswith(path_end))

    def _handler(self):
        service = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                return

            def _json(self, status: int, body: Any) -> None:
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _authorized(self) -> bool:
                service.requests.append((self.command, self.path, dict(self.headers)))
                if self.headers.get('Authorization') != f'Bearer {TOKEN}':
                    self._json(401, {'detail': 'unknown token'})
                    return False
                return True

            def do_GET(self) -> None:
                if not self._authorized():
                    return
                parts = self.path.strip('/').split('/')
                if parts == ['v1', 'packages']:
                    self._json(200, {'packages': [p['listing'] for p in service.packages.values()]})
                    return
                package = service.packages.get(parts[2]) if len(parts) >= 3 else None
                if package is None:
                    self._json(404, {'detail': 'no such package'})
                    return
                if len(parts) == 3:
                    self._json(200, {**package['manifest'], 'receipt': package['receipt']})
                    return
                data = package['zip']
                start = 0
                if self.headers.get('Range'):
                    start = int(self.headers['Range'].split('=')[1].split('-')[0])
                body = data[start:]
                self.send_response(206 if start else 200)
                self.send_header('Content-Type', 'application/zip')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                if parts[2] in service.cut_zip_once:
                    service.cut_zip_once.discard(parts[2])
                    self.wfile.write(body[:len(body) // 2])
                    self.wfile.flush()
                    self.close_connection = True
                    return
                self.wfile.write(body)

            def do_POST(self) -> None:
                if not self._authorized():
                    return
                parts = self.path.strip('/').split('/')
                package = service.packages[parts[2]]
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if service.receipt_status is not None:
                    self._json(service.receipt_status, {'detail': 'forced'})
                    return
                if body.get('zip_sha256') != _sha(package['zip']):
                    self._json(409, {'detail': 'not this package'})
                    return
                first = package['receipt'] is None
                if first:
                    package['receipt'] = {'zip_sha256': body['zip_sha256'], 'note': body.get('note')}
                    package['listing']['state'] = 'delivered'
                if service.drop_receipt_answer:
                    self.close_connection = True
                    return
                self._json(201 if first else 200, package['receipt'])

        return Handler


@pytest.fixture
def service():
    """A running stub package service."""
    stub = _PackageService()
    stub.start()
    yield stub
    stub.stop()


def _paths(root: Path) -> ImportPaths:
    """An import tree under a temporary root."""
    return ImportPaths(
        tick_source=root / 'raw',
        tick_target=root / 'processed',
        tick_finished=root / 'finished',
        tick_unpacking=root / 'unpacking',
        tick_refused=root / 'refused',
        signal_source=root / 'raw' / 'signals',
        signal_target=root / 'processed' / 'signals',
        signal_finished=root / 'finished' / 'signals',
        log_dir=root / 'logs' / 'imports',
        timings_file=root / 'runtime' / 'import_timings.json',
    )


def _client(service: _PackageService, token: str = TOKEN) -> TickPackageClient:
    """A client of the stub that never waits between attempts, reading small chunks so a
    break-off in the middle of a zip leaves something to resume from."""
    source = ActivePackageSource(
        name='test', base_url=service.get_base_url(), credentials_file='unused',
        connection=ConnectionPolicy(initial_delay_s=0.0, max_delay_s=0.0, jitter=False,
                                    attempt_budget=3, request_timeout_s=5.0,
                                    on_give_up=GiveUpAction.DEGRADE))
    return TickPackageClient(source, read_token=lambda: token, wait=lambda delay: None,
                             chunk_bytes=256)


def _run(paths: ImportPaths, client: Optional[TickPackageClient]) -> ImportCoordinator:
    """One import over the tree with the given package client, without the screen."""
    coordinator = ImportCoordinator(paths=paths, show_display=False, package_client=client)
    coordinator.run()
    return coordinator


def _outcome(coordinator: ImportCoordinator) -> Optional[ImportOutcome]:
    """How the import ended."""
    return coordinator._tracker.snapshot().outcome


def _status(coordinator: ImportCoordinator, step_id: ImportStepId) -> Optional[ImportStepStatus]:
    """The final status of one step."""
    for step in coordinator._tracker.snapshot().steps:
        if step.step_id == step_id:
            return step.status
    return None


def _log(coordinator: ImportCoordinator) -> str:
    """The import's own log file."""
    return coordinator.get_log_path().read_text(encoding='utf-8')


def _tick_files(paths: ImportPaths) -> List[Path]:
    """The tick parquets the import wrote."""
    return sorted(paths.tick_target.glob('*/ticks/*/*.parquet'))


def _two_exports() -> Dict[str, bytes]:
    """Two valid exports of different symbols."""
    return {_export_name('AAAUSD', '2026.01.15 10:00:00'): _export_bytes('AAAUSD', '2026.01.15 10:00:00'),
            _export_name('BBBUSD', '2026.01.15 10:00:00'): _export_bytes('BBBUSD', '2026.01.15 10:00:00')}


class TestAPackageIsFetchedImportedAndAcknowledged:
    """The whole exchange, from the list to the receipt."""

    def test_the_whole_exchange(self, tmp_path, service):
        paths = _paths(tmp_path)
        data = service.add('kraken-0001-20261115', _two_exports())

        coordinator = _run(paths, _client(service))

        assert _outcome(coordinator) == ImportOutcome.COMPLETED
        assert len(_tick_files(paths)) == 2
        assert (paths.tick_finished / 'kraken-0001-20261115.zip').read_bytes() == data
        assert (paths.tick_finished / 'kraken-0001-20261115.manifest.json').exists()
        assert [path.name for path in paths.tick_source.iterdir()] == []
        receipt = service.packages['kraken-0001-20261115']['receipt']
        assert receipt['zip_sha256'] == _sha(data)
        assert '2 imported' in receipt['note']
        assert _status(coordinator, ImportStepId.PACKAGES_RECEIPT) == ImportStepStatus.DONE

    def test_a_download_that_breaks_off_is_resumed(self, tmp_path, service):
        paths = _paths(tmp_path)
        service.add('kraken-0001-20261115', _two_exports())
        service.cut_zip_once.add('kraken-0001-20261115')

        coordinator = _run(paths, _client(service))

        assert _outcome(coordinator) == ImportOutcome.COMPLETED
        zip_requests = [headers for method, path, headers in service.requests
                        if method == 'GET' and path.endswith('/zip')]
        assert len(zip_requests) == 2 and zip_requests[1].get('Range', '').startswith('bytes=')
        assert service.packages['kraken-0001-20261115']['receipt'] is not None

    def test_a_delivered_package_is_left_alone(self, tmp_path, service):
        paths = _paths(tmp_path)
        service.add('kraken-0001-20261115', _two_exports(), state='delivered')

        coordinator = _run(paths, _client(service))

        assert service.count('GET', '/zip') == 0
        assert _status(coordinator, ImportStepId.PACKAGES_RECEIPT) == ImportStepStatus.SKIPPED


    def test_a_source_the_import_never_saw_is_fetched_like_the_others(self, tmp_path, service):
        # A further collector instance, such as an experimental line, comes as a source of its own;
        # the import needs nothing for it beyond what the list names
        paths = _paths(tmp_path)
        start = '2026.01.15 10:00:00'
        packages = {f'{source}-0001-20261115': {_export_name(symbol, start): _export_bytes(symbol, start)}
                    for source, symbol in (('kraken', 'AAAUSD'), ('mt5', 'BBBUSD'),
                                           ('krakendev', 'CCCUSD'))}
        for package_id, members in packages.items():
            service.add(package_id, members)

        coordinator = _run(paths, _client(service))

        assert _outcome(coordinator) == ImportOutcome.COMPLETED
        assert len(_tick_files(paths)) == 3
        for package_id in packages:
            assert (paths.tick_finished / f'{package_id}.zip').exists()
            assert service.packages[package_id]['receipt'] is not None


class TestWhatArrivesIsProven:
    """Nothing is imported or acknowledged that is not the package."""

    def test_a_zip_that_does_not_match_its_hash(self, tmp_path, service):
        paths = _paths(tmp_path)
        service.add('kraken-0001-20261115', _two_exports())
        package = service.packages['kraken-0001-20261115']
        package['listing']['sha256'] = package['manifest']['zip']['sha256'] = '0' * 64

        coordinator = _run(paths, _client(service))

        assert _outcome(coordinator) == ImportOutcome.COMPLETED_WITH_ERRORS
        assert not _tick_files(paths)
        assert not list(paths.tick_source.glob('*.zip*'))
        assert package['receipt'] is None
        assert 'does not match' in _log(coordinator)

    def test_a_zip_whose_files_differ_from_its_manifest(self, tmp_path, service):
        paths = _paths(tmp_path)
        service.add('kraken-0001-20261115', _two_exports())
        service.packages['kraken-0001-20261115']['manifest']['members'].append(
            {'name': _export_name('CCCUSD', '2026.01.15 10:00:00'), 'sha256': '0' * 64})

        coordinator = _run(paths, _client(service))

        assert _outcome(coordinator) == ImportOutcome.COMPLETED_WITH_ERRORS
        assert (paths.tick_refused / 'kraken-0001-20261115.zip').exists()
        assert (paths.tick_refused / 'kraken-0001-20261115.manifest.json').exists()
        assert not _tick_files(paths)
        assert service.packages['kraken-0001-20261115']['receipt'] is None

    def test_a_file_whose_hash_differs_from_its_manifest(self, tmp_path, service):
        paths = _paths(tmp_path)
        service.add('kraken-0001-20261115', _two_exports())
        service.packages['kraken-0001-20261115']['manifest']['members'][0]['sha256'] = '0' * 64

        coordinator = _run(paths, _client(service))

        assert _status(coordinator, ImportStepId.TICKS_UNPACK) == ImportStepStatus.FAILED
        assert (paths.tick_refused / 'kraken-0001-20261115.zip').exists()
        assert not _tick_files(paths)
        assert service.packages['kraken-0001-20261115']['receipt'] is None


class TestWhatLiesHereAlready:
    """The finished and the refused directory decide before anything is downloaded."""

    def test_a_package_imported_before_is_only_acknowledged(self, tmp_path, service):
        paths = _paths(tmp_path)
        data = service.add('kraken-0001-20261115', _two_exports())
        paths.tick_finished.mkdir(parents=True)
        (paths.tick_finished / 'kraken-0001-20261115.zip').write_bytes(data)

        _run(paths, _client(service))

        assert service.count('GET', '/zip') == 0
        receipt = service.packages['kraken-0001-20261115']['receipt']
        assert receipt['zip_sha256'] == _sha(data)
        assert receipt['note'] == 'imported by an earlier import'

    def test_a_package_refused_before_is_neither_fetched_nor_acknowledged(self, tmp_path, service):
        paths = _paths(tmp_path)
        data = service.add('kraken-0001-20261115', _two_exports())
        paths.tick_refused.mkdir(parents=True)
        (paths.tick_refused / 'kraken-0001-20261115.zip').write_bytes(data)

        coordinator = _run(paths, _client(service))

        assert service.count('GET', '/zip') == 0
        assert service.packages['kraken-0001-20261115']['receipt'] is None
        assert 'NOT FETCHED' in _log(coordinator)


class TestTheReceipt:
    """A write, sent once: a lost answer is settled by reading the package back."""

    def test_a_hash_the_service_does_not_accept(self, tmp_path, service):
        paths = _paths(tmp_path)
        service.add('kraken-0001-20261115', _two_exports())
        service.receipt_status = 409

        coordinator = _run(paths, _client(service))

        assert _outcome(coordinator) == ImportOutcome.COMPLETED_WITH_ERRORS
        assert _status(coordinator, ImportStepId.PACKAGES_RECEIPT) == ImportStepStatus.FAILED
        assert 'RECEIPT HASH_MISMATCH' in _log(coordinator)

    def test_a_lost_answer_is_settled_by_asking(self, tmp_path, service):
        paths = _paths(tmp_path)
        service.add('kraken-0001-20261115', _two_exports())
        service.drop_receipt_answer = True

        coordinator = _run(paths, _client(service))

        assert _outcome(coordinator) == ImportOutcome.COMPLETED
        assert service.count('POST', '/receipt') == 1
        assert 'Acknowledged kraken-0001-20261115' in _log(coordinator)


class TestAServiceThatFails:
    """The service failing stops the fetch, never the import."""

    def test_a_refused_token(self, tmp_path, service):
        paths = _paths(tmp_path)
        service.add('kraken-0001-20261115', _two_exports())
        write_json_fixture(paths.tick_source, _export_name('CCCUSD', '2026.01.15 10:00:00'),
                           build_minimal_tick_json(symbol='CCCUSD'))

        coordinator = _run(paths, _client(service, token='wrong'))

        assert _outcome(coordinator) == ImportOutcome.COMPLETED_WITH_ERRORS
        assert _status(coordinator, ImportStepId.PACKAGES_FETCH) == ImportStepStatus.FAILED
        # A refusal is TERMINAL: asked once, not retried
        assert service.count('GET', '/v1/packages') == 1
        assert len(_tick_files(paths)) == 1

    def test_no_fetch_says_why(self, tmp_path):
        paths = _paths(tmp_path)
        coordinator = ImportCoordinator(paths=paths, show_display=False,
                                        no_fetch_reason='skipped by --no-fetch')
        coordinator.run()

        fetch = next(step for step in coordinator._tracker.snapshot().steps
                     if step.step_id == ImportStepId.PACKAGES_FETCH)
        assert fetch.status == ImportStepStatus.SKIPPED
        assert fetch.detail == 'skipped by --no-fetch'


class TestTheConfiguration:
    """The tracked file mirrors the model, and an empty address fetches nothing."""

    def test_the_tracked_file_mirrors_the_model(self):
        manager = DataCollectorConfigManager(user_config_path='/nonexistent/override.json')
        assert manager.get_config().model_dump() == DataCollectorConfig().model_dump()
        assert manager.resolve_package_source() is None

    def test_an_address_in_the_workspace_copy_makes_a_source(self, tmp_path, monkeypatch):
        override = tmp_path / 'data_collector_config.json'
        override.write_text(json.dumps({'packages': {'endpoints': {'production': {
            'base_url': 'https://collector.example.test/'}}}}), encoding='utf-8')
        monkeypatch.setattr(
            'python.configuration.data_collector_config_manager.is_config_isolation_active',
            lambda: False)

        source = DataCollectorConfigManager(user_config_path=str(override)).resolve_package_source()

        assert source.base_url == 'https://collector.example.test'
        assert source.credentials_file == 'peers/collector_credentials.json'
