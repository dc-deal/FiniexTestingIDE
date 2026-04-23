"""
FiniexTestingIDE - API Endpoint Tests

Tests for all HTTP API endpoints: health, brokers, symbols, coverage, bars.
Uses FastAPI TestClient with mocked BarsIndexManager and MarketConfigManager
so no actual parquet data or index files are required.

Happy path + one error case per endpoint as specified in #298.
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from python.api.api_app import APP_VERSION, create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def client():
    return TestClient(create_app())


def _mock_index(broker_types=None, symbols=None, stats=None, bar_file=None):
    """Build a preconfigured BarsIndexManager mock."""
    m = MagicMock()
    m.list_broker_types.return_value = broker_types or ['kraken_spot', 'mt5']
    m.list_symbols.return_value = symbols or ['BTCUSD', 'ETHUSD']
    m.get_symbol_stats.return_value = stats or {
        'M30': {
            'start_time': '2026-01-01T00:00:00+00:00',
            'end_time': '2026-01-31T00:00:00+00:00',
            'bar_count': 1440,
            'file_size_mb': 0.2,
        },
        'H1': {
            'start_time': '2026-01-01T00:00:00+00:00',
            'end_time': '2026-01-31T00:00:00+00:00',
            'bar_count': 720,
            'file_size_mb': 0.1,
        },
    }
    m.get_bar_file.return_value = bar_file or Path('/fake/bars.parquet')
    return m


def _mock_market_config(market_type_value='crypto'):
    m = MagicMock()
    market_type = MagicMock()
    market_type.value = market_type_value
    m.get_market_type.return_value = market_type
    return m


def _sample_bars_df() -> pd.DataFrame:
    """Minimal bar DataFrame matching the real parquet schema."""
    ts = [
        datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc),
    ]
    return pd.DataFrame({
        'timestamp': pd.to_datetime(ts, utc=True),
        'symbol': ['BTCUSD', 'BTCUSD'],
        'timeframe': ['M30', 'M30'],
        'open': [40000.0, 40100.0],
        'high': [40200.0, 40300.0],
        'low': [39900.0, 39950.0],
        'close': [40100.0, 40200.0],
        'volume': [1.5, 2.0],
        'tick_count': [100, 120],
    })


# ---------------------------------------------------------------------------
# Timeframes
# ---------------------------------------------------------------------------

class TestTimeframes:

    def test_list_timeframes(self, client):
        r = client.get('/api/v1/timeframes')
        assert r.status_code == 200
        data = r.json()
        assert 'timeframes' in data
        tfs = data['timeframes']
        assert isinstance(tfs, list)
        assert len(tfs) > 0
        assert 'M1' in tfs
        assert 'D1' in tfs

    def test_timeframes_are_sorted_ascending(self, client):
        r = client.get('/api/v1/timeframes')
        tfs = r.json()['timeframes']
        from python.framework.utils.timeframe_config_utils import TimeframeConfig
        assert tfs == TimeframeConfig.sorted()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:

    def test_health_ok(self, client):
        r = client.get('/api/v1/health')
        assert r.status_code == 200
        assert r.json() == {'status': 'ok', 'version': APP_VERSION}


# ---------------------------------------------------------------------------
# Brokers
# ---------------------------------------------------------------------------

class TestBrokers:

    def test_list_brokers(self, client):
        with patch('python.api.api_app.BarsIndexManager', return_value=_mock_index()):
            r = client.get('/api/v1/brokers')
        assert r.status_code == 200
        assert set(r.json()['brokers']) == {'kraken_spot', 'mt5'}


# ---------------------------------------------------------------------------
# Symbols
# ---------------------------------------------------------------------------

class TestSymbols:

    def test_list_symbols(self, client):
        with (
            patch('python.api.endpoints.broker_router.BarsIndexManager', return_value=_mock_index()),
            patch('python.api.endpoints.broker_router.MarketConfigManager', return_value=_mock_market_config()),
        ):
            r = client.get('/api/v1/brokers/kraken_spot/symbols')
        assert r.status_code == 200
        data = r.json()
        assert all(s['market_type'] == 'crypto' for s in data['symbols'])
        assert {s['symbol'] for s in data['symbols']} == {'BTCUSD', 'ETHUSD'}

    def test_unknown_broker_returns_404(self, client):
        with patch('python.api.endpoints.broker_router.BarsIndexManager', return_value=_mock_index()):
            r = client.get('/api/v1/brokers/nonexistent/symbols')
        assert r.status_code == 404
        assert r.json()['error'] == 'not_found'


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------

class TestCoverage:

    def test_coverage_ok(self, client):
        with patch('python.api.endpoints.bars_router.BarsIndexManager', return_value=_mock_index()):
            r = client.get('/api/v1/brokers/kraken_spot/symbols/BTCUSD/coverage')
        assert r.status_code == 200
        data = r.json()
        assert 'start' in data
        assert 'end' in data
        assert set(data['timeframes']) == {'M30', 'H1'}

    def test_unknown_symbol_returns_404(self, client):
        index = _mock_index()
        index.list_symbols.return_value = []
        with patch('python.api.endpoints.bars_router.BarsIndexManager', return_value=index):
            r = client.get('/api/v1/brokers/kraken_spot/symbols/UNKNOWN/coverage')
        assert r.status_code == 404
        assert r.json()['error'] == 'not_found'


# ---------------------------------------------------------------------------
# Bars
# ---------------------------------------------------------------------------

class TestBars:

    def test_bars_ok(self, client):
        with (
            patch('python.api.endpoints.bars_router.BarsIndexManager', return_value=_mock_index()),
            patch('python.api.endpoints.bars_router.pd.read_parquet', return_value=_sample_bars_df()),
        ):
            r = client.get(
                '/api/v1/brokers/kraken_spot/symbols/BTCUSD/bars',
                params={
                    'timeframe': 'M30',
                    'from': '2026-01-01T00:00:00Z',
                    'to': '2026-02-01T00:00:00Z',
                },
            )
        assert r.status_code == 200
        bars = r.json()
        assert len(bars) == 2
        assert all(k in bars[0] for k in ('t', 'o', 'h', 'l', 'c', 'v'))
        assert bars[0]['o'] == 40000.0

    def test_invalid_timeframe_returns_400(self, client):
        r = client.get(
            '/api/v1/brokers/kraken_spot/symbols/BTCUSD/bars',
            params={
                'timeframe': 'X99',
                'from': '2026-01-01T00:00:00Z',
                'to': '2026-02-01T00:00:00Z',
            },
        )
        assert r.status_code == 400
        assert r.json()['error'] == 'invalid_timeframe'

    def test_from_after_to_returns_400(self, client):
        with patch('python.api.endpoints.bars_router.BarsIndexManager', return_value=_mock_index()):
            r = client.get(
                '/api/v1/brokers/kraken_spot/symbols/BTCUSD/bars',
                params={
                    'timeframe': 'M30',
                    'from': '2026-02-01T00:00:00Z',
                    'to': '2026-01-01T00:00:00Z',
                },
            )
        assert r.status_code == 400
        assert r.json()['error'] == 'invalid_range'

    def test_unknown_broker_returns_404(self, client):
        with patch('python.api.endpoints.bars_router.BarsIndexManager', return_value=_mock_index()):
            r = client.get(
                '/api/v1/brokers/nonexistent/symbols/BTCUSD/bars',
                params={
                    'timeframe': 'M30',
                    'from': '2026-01-01T00:00:00Z',
                    'to': '2026-02-01T00:00:00Z',
                },
            )
        assert r.status_code == 404
        assert r.json()['error'] == 'not_found'
