"""
A request that meets a store index another process is writing waits briefly, then says so (#650).

The server's readers run in a pool of worker threads, so a reader that waited the two minutes a CLI
may wait would take a thread every route needs. The server waits a few seconds and answers
`503 store_index_busy` with `Retry-After` — never a bare 500, and never the holder's command line,
which stays in the server's log.
"""

import threading
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from python.api import api_app
from python.api.api_app import create_app
from python.api.api_contract import API_CONTRACT_VERSION, CONTRACT_HEADER
from python.configuration.app_config_manager import AppConfigManager
from python.framework.reporting.store.run_ledger_index import RunLedgerIndex
from python.framework.store.abstract_store_index import INDEX_LOCK_WAIT_S, set_process_lock_wait_s
from python.framework.utils.file_lock import FileLock

_URL = '/api/v1/deployments'


@pytest.fixture
def ledger_lock_path() -> Path:
    """
    The session's ledger with one more fragment, so the next read must take the index's lock.

    The ledger is the whole test session's, so the fragment and the index it leaves go again after
    the test — another suite counting the ledger's rows must not meet them.
    """
    ledger_dir = Path(AppConfigManager().get_run_ledger_path())
    ledger_dir.mkdir(parents=True, exist_ok=True)
    fragment = ledger_dir / 'sweep_store_index_busy_run.parquet'
    pd.DataFrame({'run_id': ['store_index_busy_run'], 'param_hash': ['abc1234'],
                  'run_timestamp': ['2026-11-21T09:00:00+00:00']}).to_parquet(fragment)
    index = RunLedgerIndex(ledger_dir, ['run_id'])
    yield index.get_lock_path()
    fragment.unlink(missing_ok=True)
    index.get_path().unlink(missing_ok=True)


@pytest.fixture
def client(monkeypatch) -> TestClient:
    """The server, with its wait short enough for a test; every later index keeps the usual bound."""
    monkeypatch.setattr(api_app, 'API_INDEX_LOCK_WAIT_S', 0.3)
    yield TestClient(create_app())
    set_process_lock_wait_s(INDEX_LOCK_WAIT_S)


class TestAHeldIndex:

    def test_an_index_held_past_the_wait_answers_busy_with_retry_after(self, client, ledger_lock_path):
        with FileLock(ledger_lock_path) as holder:
            holder.write_note('pid 4711 · python my_private_script.py --profile my_secret.json')
            response = client.get(_URL)
        assert response.status_code == 503
        assert response.json()['error'] == 'store_index_busy'
        assert response.headers['Retry-After'] == '5'
        assert response.headers[CONTRACT_HEADER] == str(API_CONTRACT_VERSION)
        detail = response.json()['detail']
        assert 'run ledger index' in detail
        # The holder's note names a process and its command line: it is logged, never served
        assert 'pid' not in detail and 'my_secret' not in response.text

    def test_an_ordinary_hold_is_waited_for_and_answered(self, monkeypatch, ledger_lock_path):
        monkeypatch.setattr(api_app, 'API_INDEX_LOCK_WAIT_S', 5.0)
        try:
            client = TestClient(create_app())
            holder = FileLock(ledger_lock_path)
            release = threading.Timer(0.3, holder.release)
            release.start()
            try:
                response = client.get(_URL)
            finally:
                release.cancel()
                holder.release()
        finally:
            set_process_lock_wait_s(INDEX_LOCK_WAIT_S)
        assert response.status_code == 200
