"""
Run-provenance (session) tests (#403 · 5.a).

`build_run_provenance_from_session` is the live counterpart to the sim `build_run_provenance`:
it lets a live session append to the same Run Results Ledger. The key property is sim/live
parity — the profile's strategy_config has the same shape as a sim scenario's, so the
param_hash is computed identically and the live row is directly comparable to the backtest.
Built against the REAL AutoTraderConfig / WarningsErrorsReport, never stand-ins.
"""

from datetime import datetime, timezone
from pathlib import Path

from python.framework.reporting.builders.warnings_errors_report_builder import (
    build_warnings_errors_report_from_session)
from python.framework.reporting.store.run_provenance_builder import (
    build_run_provenance_from_session)
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.utils.config_fingerprint_utils import generate_config_fingerprint


_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)
_RUN_DIR = Path('logs/autotrader/my_profile/20260101_000000')


def _config() -> AutoTraderConfig:
    return AutoTraderConfig(
        name='my_profile', symbol='BTCUSD', broker_type='kraken_spot',
        strategy_config={'decision_logic_type': 'CORE/aggressive_trend',
                         'worker_instances': {}})


class TestSessionProvenance:
    """The live session maps onto the same RunProvenance the ledger ranks over."""

    def test_maps_config_to_provenance(self):
        p = build_run_provenance_from_session(_config(), _RUN_DIR, _TS, None)
        assert p.run_id == '20260101_000000'
        assert p.run_timestamp == _TS
        assert p.scenario_set_name == 'my_profile'
        assert p.symbols == ['BTCUSD']
        assert p.data_broker_type == 'kraken_spot'
        # Live is never swept.
        assert p.sweep_id is None and p.sweep_params is None
        assert p.sweep_objective is None and p.sweep_maximize is None

    def test_param_hash_parity_with_backtest(self):
        # The whole point of 5.a: the live param_hash is the SAME fingerprint the sim run
        # produces for the same strategy_config — so live + backtest rows compare directly.
        cfg = _config()
        p = build_run_provenance_from_session(cfg, _RUN_DIR, _TS, None)
        assert p.param_hash == generate_config_fingerprint(cfg.strategy_config)

    def test_status_ok_without_report(self):
        p = build_run_provenance_from_session(_config(), _RUN_DIR, _TS, None)
        assert p.status == 'ok'
        assert p.error is None

    def test_status_error_on_emergency(self):
        # An emergency session (every unit failed) → a status='error' ledger row, never absent.
        result = AutoTraderResult(emergency_reason='boom', shutdown_mode='emergency')
        report = build_warnings_errors_report_from_session(result, 'my_profile', 'BTCUSD')
        p = build_run_provenance_from_session(_config(), _RUN_DIR, _TS, report)
        assert p.status == 'error'
        assert 'boom' in p.error
