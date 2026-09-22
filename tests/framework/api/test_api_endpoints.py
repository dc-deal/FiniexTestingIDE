"""
FiniexTestingIDE - API Endpoint Tests

Tests for all HTTP API endpoints: health, brokers, symbols, coverage, gaps, bars,
and the ATR indicator series.
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

from python.api.api_app import create_app
from python.configuration.app_config_manager import AppConfigManager
from python.data_management.index.bars_index_manager import BarsIndexManager
from python.framework.types.api.report_types import RunInfo, RunResultRow

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
    # The nested index the router reads the stamped price basis from. A MagicMock would
    # answer every lookup with another mock, which is not a header value — and would hide
    # that the basis now comes from the FILE rather than from a constant.
    m.index = {
        'kraken_spot': {
            'BTCUSD': {'M30': {'price_basis': 'order_driven'},
                       'H1': {'price_basis': 'order_driven'}},
            'ETHUSD': {'M30': {'price_basis': 'order_driven'},
                       'H1': {'price_basis': 'order_driven'}},
        },
        'mt5': {
            'BTCUSD': {'M30': {'price_basis': 'quote_driven'},
                       'H1': {'price_basis': 'quote_driven'}},
            'ETHUSD': {'M30': {'price_basis': 'quote_driven'},
                       'H1': {'price_basis': 'quote_driven'}},
        },
    }
    # The REAL accessor over that data, not a canned return value: the lookup and its
    # 'unknown' fallback are the behaviour under test, and a mocked answer would pass just as
    # happily if the router stopped consulting the index at all.
    m.get_price_basis.side_effect = (
        lambda broker, symbol, timeframe: BarsIndexManager.get_price_basis(
            m, broker, symbol, timeframe))
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

    def test_list_timeframes_structure(self, client):
        r = client.get('/api/v1/timeframes')
        assert r.status_code == 200
        data = r.json()
        assert 'timeframes' in data
        tfs = data['timeframes']
        assert len(tfs) > 0
        assert all('name' in tf and 'minutes' in tf for tf in tfs)

    def test_list_timeframes_contains_known_entries(self, client):
        r = client.get('/api/v1/timeframes')
        tfs = r.json()['timeframes']
        by_name = {tf['name']: tf['minutes'] for tf in tfs}
        assert by_name.get('M1') == 1
        assert by_name.get('H1') == 60
        assert by_name.get('D1') == 1440

    def test_timeframes_sorted_ascending_by_minutes(self, client):
        r = client.get('/api/v1/timeframes')
        tfs = r.json()['timeframes']
        minutes = [tf['minutes'] for tf in tfs]
        assert minutes == sorted(minutes)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:

    def test_health_ok(self, client):
        r = client.get('/api/v1/health')
        assert r.status_code == 200
        assert r.json() == {'status': 'ok', 'version': AppConfigManager().get_version()}


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

    def test_bars_carry_the_tick_count(self, client):
        """Forex feeds report volume 0.0, so the tick count is their only activity measure."""
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
        assert [bar['tc'] for bar in r.json()] == [100, 120]

    def test_a_cut_response_says_that_it_was_cut(self, client):
        """The defect this replaces: head() returned the first rows and nothing said so."""
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
                    'limit': 1,
                },
            )
        assert r.status_code == 200
        assert len(r.json()) == 1
        assert r.headers['X-Bar-Truncated'] == 'true'
        assert r.headers['X-Bar-Count'] == '1'
        assert r.headers['X-Bar-Limit'] == '1'
        # The total is what makes the rest reachable — head() is exactly what discards it.
        assert r.headers['X-Bar-Total'] == '2'

    def test_a_complete_response_says_it_was_not_cut(self, client):
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
        assert r.headers['X-Bar-Truncated'] == 'false'
        assert r.headers['X-Bar-Count'] == r.headers['X-Bar-Total'] == '2'

    def test_every_response_states_its_own_semantics(self, client):
        """Open-vs-close, timezone and mid-vs-traded each cost a reader a wrong answer."""
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
        assert r.headers['X-Bar-Time-Basis'] == 'open'
        assert r.headers['X-Bar-Timezone'] == 'UTC'
        # Read from the FILE's stamp, not from a constant and not from config: during a
        # re-render half the archive still carries the previous basis, and a header taken
        # from configuration would be wrong for exactly those files.
        assert r.headers['X-Bar-Price-Basis'] == 'order_driven'

    def test_a_limit_above_the_cap_is_refused_rather_than_clamped(self, client):
        r = client.get(
            '/api/v1/brokers/kraken_spot/symbols/BTCUSD/bars',
            params={
                'timeframe': 'M30',
                'from': '2026-01-01T00:00:00Z',
                'to': '2026-02-01T00:00:00Z',
                'limit': 10_001,
            },
        )
        assert r.status_code == 400
        assert r.json()['error'] == 'invalid_limit'

    def test_a_limit_below_one_is_refused(self, client):
        r = client.get(
            '/api/v1/brokers/kraken_spot/symbols/BTCUSD/bars',
            params={
                'timeframe': 'M30',
                'from': '2026-01-01T00:00:00Z',
                'to': '2026-02-01T00:00:00Z',
                'limit': 0,
            },
        )
        assert r.status_code == 400
        assert r.json()['error'] == 'invalid_limit'

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


# ---------------------------------------------------------------------------
# Report runs (run index)
# ---------------------------------------------------------------------------

class TestReportRuns:

    def test_list_runs(self, client):
        store = MagicMock()
        store.list_runs.return_value = [
            RunInfo(run_id='20260615_130000', group='autotrader', name='my_profile'),
            RunInfo(run_id='20260615_120000', group='scenario_sets', name='my_set'),
        ]
        with patch('python.api.endpoints.reports_router.ReportStore', return_value=store):
            r = client.get('/api/v1/reports/runs')
        assert r.status_code == 200
        data = r.json()
        assert data['count'] == 2
        assert [run['run_id'] for run in data['runs']] == [
            '20260615_130000', '20260615_120000']
        assert data['runs'][0]['group'] == 'autotrader'
        assert data['runs'][0]['name'] == 'my_profile'

    def test_no_persisted_run_is_not_an_error(self, client):
        """An empty store is a legitimate empty index, never a 404."""
        store = MagicMock()
        store.list_runs.return_value = []
        with patch('python.api.endpoints.reports_router.ReportStore', return_value=store):
            r = client.get('/api/v1/reports/runs')
        assert r.status_code == 200
        assert r.json() == {'runs': [], 'count': 0}


class TestSweeps:
    """
    A sweep is a family of runs, so it gets its own routes: the run index lists standalone runs,
    these list sweeps and rank their combinations. Served from the ledger, where a sweep's
    identity lives — the logs tree only knows directory names.
    """

    @staticmethod
    def _rows():
        return [
            RunResultRow(run_id='r2', param_hash='h2', run_timestamp='20260615_120100',
                         currency='EUR', sweep_id='sweep_1', sweep_objective='net_pnl',
                         sweep_maximize=True, net_pnl=10.0,
                         scenario_set_name='set__sweep_1_c001', status='ok'),
            RunResultRow(run_id='r1', param_hash='h1', run_timestamp='20260615_120000',
                         currency='EUR', sweep_id='sweep_1', sweep_objective='net_pnl',
                         sweep_maximize=True, net_pnl=30.0,
                         scenario_set_name='set__sweep_1_c000', status='ok'),
        ]

    def test_lists_recorded_sweeps(self, client):
        ledger = MagicMock()
        ledger.read_rows.return_value = self._rows()
        with patch('python.api.endpoints.sweeps_router._ledger', return_value=ledger):
            r = client.get('/api/v1/sweeps')
        assert r.status_code == 200
        data = r.json()
        assert data['count'] == 1
        assert data['sweeps'][0]['sweep_id'] == 'sweep_1'
        assert data['sweeps'][0]['run_count'] == 2

    def test_no_sweep_is_not_an_error(self, client):
        ledger = MagicMock()
        ledger.read_rows.return_value = []
        with patch('python.api.endpoints.sweeps_router._ledger', return_value=ledger):
            r = client.get('/api/v1/sweeps')
        assert r.status_code == 200
        assert r.json() == {'sweeps': [], 'count': 0}

    def test_combinations_are_ranked_by_the_sweeps_own_objective(self, client):
        """Ranked, not alphabetical — the question a sweep answers is which combination won."""
        ledger = MagicMock()
        ledger.read_rows.return_value = self._rows()
        with patch('python.api.endpoints.sweeps_router._ledger', return_value=ledger):
            r = client.get('/api/v1/sweeps/sweep_1')
        assert r.status_code == 200
        data = r.json()
        assert data['objective'] == 'net_pnl' and data['maximize'] is True
        assert [c['run_id'] for c in data['combinations']] == ['r1', 'r2']   # 30.0 before 10.0

    def test_each_combination_carries_its_run_id(self, client):
        """The hinge into the report routes — without it a sweep view is a dead end."""
        ledger = MagicMock()
        ledger.read_rows.return_value = self._rows()
        with patch('python.api.endpoints.sweeps_router._ledger', return_value=ledger):
            data = client.get('/api/v1/sweeps/sweep_1').json()
        assert all(c['run_id'] for c in data['combinations'])

    def test_unknown_sweep_is_a_404(self, client):
        ledger = MagicMock()
        ledger.read_rows.return_value = []
        with patch('python.api.endpoints.sweeps_router._ledger', return_value=ledger):
            r = client.get('/api/v1/sweeps/nope')
        assert r.status_code == 404


class TestDeployments:
    """
    A deployment is the life of ONE bot across its restarts, and it is not a run: no header, no
    directory, no artifacts. Its rows live in the ledger and nowhere else — which is why these
    routes read the ledger and only the ledger (one route, one store).

    Before #539 the ledger's only reader filtered on `sweep_id`, which a live session does not
    have, so every one of these rows was written and unreachable (§44).
    """

    @staticmethod
    def _rows():
        # Two sessions of one deployment, and the second raised a safety threshold: same
        # param_hash (it decides the same), different profile_hash (it does something else).
        return [
            RunResultRow(run_id='s1', param_hash='p1', profile_hash='o1',
                         run_timestamp='2026-09-01T06:00:00+00:00',
                         recorded_at_utc='2026-09-01T18:00:00+00:00',
                         currency='USD', deployment_id='deploy_1', bot_id='bot-a',
                         scenario_set_name='dotusd_live', net_pnl=10.0,
                         account_max_drawdown=5.0, account_max_drawdown_pct=1.0, status='ok'),
            RunResultRow(run_id='s2', param_hash='p1', profile_hash='o2',
                         run_timestamp='2026-09-02T06:00:00+00:00',
                         recorded_at_utc='2026-09-02T18:00:00+00:00',
                         currency='USD', deployment_id='deploy_1', bot_id='bot-a',
                         scenario_set_name='dotusd_live', net_pnl=-4.0,
                         account_max_drawdown=9.0, account_max_drawdown_pct=1.8, status='ok'),
        ]

    @staticmethod
    def _no_unfinished():
        """An index that knows no run — so nothing of this deployment is missing from the ledger."""
        index = MagicMock()
        index.list_runs.return_value = []
        return patch('python.api.endpoints.deployments_router.RunIndex', return_value=index)

    def test_lists_recorded_deployments(self, client):
        ledger = MagicMock()
        ledger.read_rows.return_value = self._rows()
        with patch('python.api.endpoints.deployments_router._ledger', return_value=ledger):
            r = client.get('/api/v1/deployments')
        assert r.status_code == 200
        data = r.json()
        assert data['count'] == 1
        assert data['deployments'][0]['deployment_id'] == 'deploy_1'
        assert data['deployments'][0]['sessions'] == 2
        assert data['deployments'][0]['bot'] == 'dotusd_live'

    def test_a_deployments_pnl_sums_and_its_drawdown_does_not(self, client):
        """
        The one arithmetic a deployment view must not get wrong (§48). Each live row carries
        the RUNNING decline against the inherited peak, so the reduction is max() — a sum
        would report 14.0 here, counting one decline once per session that was still inside it.
        """
        ledger = MagicMock()
        ledger.read_rows.return_value = self._rows()
        with patch('python.api.endpoints.deployments_router._ledger', return_value=ledger):
            summary = client.get('/api/v1/deployments').json()['deployments'][0]
        assert summary['net_pnl'] == 6.0            # 10.0 + (-4.0)
        assert summary['max_drawdown'] == 9.0       # max(5.0, 9.0), never 14.0

    def test_no_deployment_is_not_an_error(self, client):
        ledger = MagicMock()
        ledger.read_rows.return_value = []
        with patch('python.api.endpoints.deployments_router._ledger', return_value=ledger):
            r = client.get('/api/v1/deployments')
        assert r.status_code == 200
        assert r.json() == {'deployments': [], 'count': 0}

    def test_sessions_read_forwards(self, client):
        """A life reads oldest first — the opposite order to the console, deliberately."""
        ledger = MagicMock()
        ledger.read_rows.return_value = self._rows()
        with patch('python.api.endpoints.deployments_router._ledger', return_value=ledger), \
                self._no_unfinished():
            data = client.get('/api/v1/deployments/deploy_1').json()
        assert [s['run_id'] for s in data['sessions']] == ['s1', 's2']
        assert data['count'] == 2

    def test_a_configuration_change_is_reported_before_the_table(self, client):
        """
        The advisory answers whether the rows may be read as ONE series, and it has to reach
        the reader before the numbers do — so it rides on the response, not inside a row.
        """
        ledger = MagicMock()
        ledger.read_rows.return_value = self._rows()
        with patch('python.api.endpoints.deployments_router._ledger', return_value=ledger), \
                self._no_unfinished():
            data = client.get('/api/v1/deployments/deploy_1').json()
        assert data['advisory']['operation_stands'] == 2
        assert data['advisory']['strategy_stands'] == 1     # it kept deciding the same way
        assert data['sessions'][1]['operation_changed'] is True
        assert data['sessions'][1]['strategy_changed'] is False

    def test_a_session_that_never_reached_its_close_is_counted(self, client):
        """
        The sessions come from the LEDGER, whose row is written last — so a killed run is
        absent from them by construction, and a bare count would be a number the reader has
        no reason to doubt (§44).
        """
        ledger = MagicMock()
        ledger.read_rows.return_value = self._rows()
        index = MagicMock()
        index.list_runs.return_value = []
        with patch('python.api.endpoints.deployments_router._ledger', return_value=ledger), \
                patch('python.api.endpoints.deployments_router.RunIndex', return_value=index), \
                patch('python.api.endpoints.deployments_router.unfinished_by_group',
                      return_value={'live': [MagicMock(), MagicMock()]}):
            data = client.get('/api/v1/deployments/deploy_1').json()
        assert data['count'] == 2 and data['unfinished'] == 2

    def test_unknown_deployment_is_a_404_and_not_an_empty_history(self, client):
        """An empty list would read as a deployment that ran and did nothing."""
        ledger = MagicMock()
        ledger.read_rows.return_value = []
        with patch('python.api.endpoints.deployments_router._ledger', return_value=ledger):
            r = client.get('/api/v1/deployments/nope')
        assert r.status_code == 404
        assert r.json()['error'] == 'deployment_not_found'

    @staticmethod
    def _period_rows():
        """
        Two sessions of one deployment, three booked periods, and one row that books none.

        The last one is what every ledger row written before the booking journal looks like:
        an aggregate per currency with no period at all.
        """
        def row(run_id, no, opened, closed, pnl, trades, reason='anchor'):
            return RunResultRow(
                run_id=run_id, param_hash='p1', run_timestamp=opened, currency='USD',
                deployment_id='deploy_1', scenario_set_name='dotusd_live', status='ok',
                unit_name='dotusd_live', segment_no=no, segment_opened_at=opened,
                segment_closed_at=closed, segment_close_reason=reason,
                segment_trade_count=trades, net_pnl=pnl,
                segment_min_equity=90.0, segment_max_equity=110.0,
                segment_max_drawdown=-5.0, final_equity=100.0 + pnl)

        return [
            row('s1', 1, '2026-09-01T00:00:00+00:00', '2026-09-02T00:00:00+00:00', 12.0, 3),
            row('s1', 2, '2026-09-02T00:00:00+00:00', '2026-09-02T18:00:00+00:00', -4.0, 2,
                reason='session_end'),
            # A second session, and its counter starts at 1 AGAIN — which it does wherever a
            # session wrote no carry-over floor. Without `run_id` these two would be one row.
            row('s2', 1, '2026-09-03T00:00:00+00:00', '2026-09-03T18:00:00+00:00', 7.0, 1,
                reason='session_end'),
            RunResultRow(run_id='s0', param_hash='p1', currency='USD',
                         run_timestamp='2026-08-01T00:00:00+00:00',
                         deployment_id='deploy_1', scenario_set_name='dotusd_live',
                         status='ok', net_pnl=99.0),
        ]

    def test_the_periods_of_every_session_come_back_in_one_call(self, client):
        """The thirty-day picture without walking the sessions and asking each run in turn."""
        ledger = MagicMock()
        ledger.read_rows.return_value = self._period_rows()
        with patch('python.api.endpoints.deployments_router._ledger', return_value=ledger):
            r = client.get('/api/v1/deployments/deploy_1/booking-periods')
        assert r.status_code == 200
        data = r.json()
        assert data['count'] == 3
        assert data['currencies'] == ['USD']
        assert [p['net_pnl'] for p in data['periods']] == [12.0, -4.0, 7.0]

    def test_every_period_names_the_session_that_booked_it(self, client):
        """
        `segment_no` is a per-BOT counter and restarts wherever a session wrote no carry-over
        floor, so two periods of one deployment can both be #1. `run_id` is then the only
        thing that tells them apart — and it is the hinge into that run's report routes.
        """
        ledger = MagicMock()
        ledger.read_rows.return_value = self._period_rows()
        with patch('python.api.endpoints.deployments_router._ledger', return_value=ledger):
            periods = client.get('/api/v1/deployments/deploy_1/booking-periods').json()['periods']
        assert [p['run_id'] for p in periods] == ['s1', 's1', 's2']
        assert [p['segment_no'] for p in periods] == [1, 2, 1]

    def test_a_row_that_books_no_period_is_skipped_and_counted(self, client):
        """
        Every row written before the booking journal is one of those. Dropping it silently
        would make an incomplete history indistinguishable from a quiet one, so the session
        it belongs to is counted instead.
        """
        ledger = MagicMock()
        ledger.read_rows.return_value = self._period_rows()
        with patch('python.api.endpoints.deployments_router._ledger', return_value=ledger):
            data = client.get('/api/v1/deployments/deploy_1/booking-periods').json()
        assert data['sessions'] == 2                    # s1 and s2 booked
        assert data['sessions_without_periods'] == 1    # s0 did not
        assert all(p['opened_at'] for p in data['periods'])

    def test_the_periods_carry_their_own_band_not_the_cumulative_one(self, client):
        """
        On this table the question is what EACH period did — the running figure would repeat
        the same number down the column.
        """
        ledger = MagicMock()
        ledger.read_rows.return_value = self._period_rows()
        with patch('python.api.endpoints.deployments_router._ledger', return_value=ledger):
            first = client.get('/api/v1/deployments/deploy_1/booking-periods').json()['periods'][0]
        assert first['max_drawdown'] == -5.0    # segment_max_drawdown, not account_max_drawdown
        assert (first['min_equity'], first['max_equity']) == (90.0, 110.0)

    def test_there_is_no_reconciliation_and_that_is_deliberate(self, client):
        """
        Across many runs no single run summary exists to sum against, so a check here could
        only compare the rows with themselves — a control total that holds by construction
        and can never fail (§48). Pinned as an ABSENCE so it cannot be added by reflex.
        """
        ledger = MagicMock()
        ledger.read_rows.return_value = self._period_rows()
        with patch('python.api.endpoints.deployments_router._ledger', return_value=ledger):
            data = client.get('/api/v1/deployments/deploy_1/booking-periods').json()
        assert 'reconciles' not in data
        assert 'total_net_pnl' not in data and 'run_net_pnl' not in data

    def test_periods_of_an_unknown_deployment_are_a_404(self, client):
        ledger = MagicMock()
        ledger.read_rows.return_value = []
        with patch('python.api.endpoints.deployments_router._ledger', return_value=ledger):
            r = client.get('/api/v1/deployments/nope/booking-periods')
        assert r.status_code == 404
        assert r.json()['error'] == 'deployment_not_found'

    def test_the_detail_route_filters_in_the_store(self, client):
        """
        Not after reading: `deployment_id` is the live counterpart of the sweep filter, and
        pulling the whole ledger to drop most of it is what the filter exists to avoid.
        """
        ledger = MagicMock()
        ledger.read_rows.return_value = self._rows()
        with patch('python.api.endpoints.deployments_router._ledger', return_value=ledger), \
                self._no_unfinished():
            client.get('/api/v1/deployments/deploy_1')
        ledger.read_rows.assert_called_once_with(deployment_id='deploy_1')


# ---------------------------------------------------------------------------
# Gaps
# ---------------------------------------------------------------------------

def _mock_coverage_report():
    """A coverage report with one expected closure and one real outage."""
    from python.framework.types.coverage_report_types import Gap, GapCategory

    report = MagicMock()
    report.start_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    report.end_time = datetime(2026, 1, 31, tzinfo=timezone.utc)
    report.gap_counts = {'weekend': 4, 'large': 1, 'seamless': 0}
    report.gaps = [
        Gap(gap_seconds=172800.0, category=GapCategory.WEEKEND, reason='market closed',
            gap_start=datetime(2026, 1, 3, tzinfo=timezone.utc),
            gap_end=datetime(2026, 1, 5, tzinfo=timezone.utc)),
        Gap(gap_seconds=7200.0, category=GapCategory.LARGE, reason='no ticks received',
            gap_start=datetime(2026, 1, 8, 10, tzinfo=timezone.utc),
            gap_end=datetime(2026, 1, 8, 12, tzinfo=timezone.utc)),
    ]
    return report


class TestGaps:
    """A venue outage and a quiet weekend are different facts; the category is what says so."""

    def test_gaps_ok(self, client):
        cache = MagicMock()
        cache.get_report.return_value = _mock_coverage_report()
        with (
            patch('python.api.endpoints.bars_router.BarsIndexManager', return_value=_mock_index()),
            patch('python.api.endpoints.bars_router.DataCoverageReportCache', return_value=cache),
        ):
            r = client.get('/api/v1/brokers/kraken_spot/symbols/BTCUSD/gaps')

        assert r.status_code == 200
        body = r.json()
        assert body['symbol'] == 'BTCUSD'
        assert len(body['gaps']) == 2
        assert {g['category'] for g in body['gaps']} == {'weekend', 'large'}

    def test_empty_categories_are_not_reported(self, client):
        """A zero count is noise — the reader wants what DID happen."""
        cache = MagicMock()
        cache.get_report.return_value = _mock_coverage_report()
        with (
            patch('python.api.endpoints.bars_router.BarsIndexManager', return_value=_mock_index()),
            patch('python.api.endpoints.bars_router.DataCoverageReportCache', return_value=cache),
        ):
            r = client.get('/api/v1/brokers/kraken_spot/symbols/BTCUSD/gaps')

        assert 'seamless' not in r.json()['gap_counts']

    def test_missing_report_is_a_404(self, client):
        cache = MagicMock()
        cache.get_report.return_value = None
        with (
            patch('python.api.endpoints.bars_router.BarsIndexManager', return_value=_mock_index()),
            patch('python.api.endpoints.bars_router.DataCoverageReportCache', return_value=cache),
        ):
            r = client.get('/api/v1/brokers/kraken_spot/symbols/BTCUSD/gaps')

        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Indicators — ATR
# ---------------------------------------------------------------------------

def _long_bars_df(rows: int = 200) -> pd.DataFrame:
    """Enough bars that a 14-period ATR is past its warmup well before the requested range."""
    stamps = pd.date_range('2026-01-01', periods=rows, freq='30min', tz='UTC')
    closes = [40000.0 + i * 5 for i in range(rows)]
    return pd.DataFrame({
        'timestamp': stamps,
        'open': closes,
        'high': [c + 50 for c in closes],
        'low': [c - 50 for c in closes],
        'close': closes,
        'volume': [1.0] * rows,
        'tick_count': [10] * rows,
    })


class TestAtrIndicator:

    def _call(self, client, **params):
        query = {
            'timeframe': 'M30',
            'from': '2026-01-03T00:00:00Z',
            'to': '2026-01-04T00:00:00Z',
        }
        query.update(params)
        with (
            patch('python.api.endpoints.bars_router.BarsIndexManager', return_value=_mock_index()),
            patch('python.api.endpoints.bars_router.pd.read_parquet', return_value=_long_bars_df()),
        ):
            return client.get(
                '/api/v1/brokers/kraken_spot/symbols/BTCUSD/indicators/atr', params=query)

    def test_atr_ok(self, client):
        r = self._call(client)

        assert r.status_code == 200
        points = r.json()
        assert points, 'the range must produce values'
        assert all(set(p) == {'t', 'v'} for p in points)
        # Every bar spans exactly 100 with no gaps, so any average of the true range is 100.
        assert all(p['v'] == pytest.approx(100.0, abs=1.0) for p in points)

    def test_the_response_declares_its_smoothing(self, client):
        """
        "ATR" means Wilder's outside this project, and a caller cannot tell from the rows.
        The default is the standard and the header says so.
        """
        r = self._call(client)

        assert r.headers['X-Indicator-Smoothing'] == 'rma'
        assert r.headers['X-Indicator-Period'] == '14'
        assert r.headers['X-Indicator-Timeframe'] == 'M30'
        assert r.headers['X-Bar-Time-Basis'] == 'open'

    def test_a_named_variant_is_reachable_and_declared(self, client):
        r = self._call(client, smoothing='ema')

        assert r.status_code == 200
        assert r.headers['X-Indicator-Smoothing'] == 'ema'

    def test_an_unknown_smoothing_is_refused(self, client):
        r = self._call(client, smoothing='wilder')

        assert r.status_code == 422

    def test_an_out_of_range_period_is_refused_not_clamped(self, client):
        r = self._call(client, period=9999)

        assert r.status_code == 400
        assert r.json()['error'] == 'invalid_period'

    def test_an_empty_range_is_a_404(self, client):
        r = self._call(client, **{'from': '2030-01-01T00:00:00Z', 'to': '2030-01-02T00:00:00Z'})

        assert r.status_code == 404
