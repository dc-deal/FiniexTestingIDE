"""
FiniexTestingIDE - AutoTrader Mock Session Integration Test
End-to-end test: Config → Pipeline → Tick Loop → Shutdown → Result.

Uses mock_session_test.json profile with parquet replay data.
Deterministic: same data + same config = same results.
"""

import json
import shutil
from pathlib import Path

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.reporting.io.broker_report_io import read_broker_report
from python.framework.reporting.store.report_store import IO_SUBDIR


MOCK_PROFILE = 'configs/autotrader_profiles/backtesting/mock_session_test.json'


@pytest.fixture(scope='module')
def mock_session():
    """
    Run one full mock session shared across all tests in this module.
    Avoids running 29780 ticks twice.
    """
    config = load_autotrader_config(MOCK_PROFILE)
    trader = AutotraderMain(config)
    result = trader.run()
    yield result, trader._run_dir
    if trader._run_dir and trader._run_dir.exists():
        shutil.rmtree(trader._run_dir)


class TestAutotraderMockSession:
    """
    End-to-end integration test for AutoTrader mock pipeline.

    Runs a full session with mock_session_test.json (parquet replay, ~30K ticks).
    Validates that the complete pipeline produces correct, deterministic results.
    """

    def test_full_mock_session(self, mock_session):
        """
        Run complete mock session and validate result.

        Covers: config loading, pipeline creation, tick processing,
        decision logic, order execution, shutdown, and reporting.
        """
        result, _ = mock_session

        # === Session completed normally ===
        assert result.shutdown_mode == 'normal', (
            f"Expected normal shutdown, got '{result.shutdown_mode}'"
        )

        # === All ticks processed ===
        assert result.ticks_processed == 29780, (
            f"Expected 29780 ticks, got {result.ticks_processed}"
        )

        # === No clipping in replay mode ===
        assert result.ticks_clipped == 0, (
            f"Expected 0 clipped ticks in replay mode, got {result.ticks_clipped}"
        )

        # === Clean session — no unexpected warnings or errors ===
        # Spot mode may leave positions open until scenario_end (no SHORT reversal)
        unexpected_warnings = [
            w for w in result.warning_messages
            if 'positions remain open' not in w
        ]
        assert len(unexpected_warnings) == 0, (
            f"Unexpected warnings: {unexpected_warnings[:5]}"
        )
        assert len(result.error_messages) == 0, (
            f"Unexpected errors: {result.error_messages[:5]}"
        )

        # === Decision logic produced trades ===
        assert len(result.trade_history) > 0, 'No trades executed'
        assert len(result.order_history) > 0, 'No orders recorded'

        # === Portfolio stats collected ===
        assert result.portfolio_stats is not None, 'Missing portfolio stats'
        assert result.execution_stats is not None, 'Missing execution stats'

        # === Clipping monitor reported ===
        assert result.clipping_summary.total_ticks == 29780

    def test_log_files_created(self, mock_session):
        """Verify that all expected log files are created."""
        _, run_dir = mock_session

        assert run_dir.exists(), f"Run directory not created: {run_dir}"
        assert (run_dir / 'autotrader_global.log').exists()
        assert (run_dir / 'autotrader_summary.log').exists()
        assert (run_dir / 'session_logs').is_dir()
        assert (run_dir / 'events.csv').exists()

        # Session logs in subdirectory (tick-date based, not wall clock)
        session_logs = list((run_dir / 'session_logs').glob('autotrader_session_*.log'))
        assert len(session_logs) >= 1, 'No session log files created'

    def test_broker_report_written(self, mock_session):
        """Broker report is persisted (unified model) + rendered in the summary (#391 live)."""
        _, run_dir = mock_session

        broker_artifact = run_dir / IO_SUBDIR / 'broker.json'
        assert broker_artifact.exists(), 'broker.json not written for live session'

        report = read_broker_report(broker_artifact)
        assert len(report.units) == 1
        assert report.units[0].symbols[0].symbol == 'BTCUSD'

        # The broker configuration section appears in the post-session summary (#403 Phase 2:
        # the live console renders the shared broker table, like sim).
        summary = (run_dir / 'autotrader_summary.log').read_text()
        assert 'BROKER CONFIGURATION' in summary
        assert 'Company: Kraken' in summary


class TestProfileLoader:
    """Profile → AutoTraderConfig parse guards (no session run)."""

    def test_tick_source_fields_fully_parsed(self, tmp_path):
        """
        Every tick_source field a profile may set must reach the config —
        a key the allowlist accepts but the loader drops is a silent misconfig.
        """
        profile = json.loads(Path(MOCK_PROFILE).read_text())
        profile['tick_source'] = {
            'type': 'mock',
            'parquet_path': 'data/some.parquet',
            'max_ticks': 123,
            'tick_delay_ms': 7,
            'ws_url': 'wss://example/v2',
            'reconnect_initial_delay_s': 2.5,
            'reconnect_max_delay_s': 90.0,
            'connection_check_interval_s': 15.0,
            'connection_dead_s': 45.0,
            'freeze_after_ticks': 500,
            'freeze_duration_s': 1.5,
        }
        profile_path = tmp_path / 'tick_source_profile.json'
        profile_path.write_text(json.dumps(profile))

        tick_source = load_autotrader_config(str(profile_path)).tick_source
        for key, expected in profile['tick_source'].items():
            assert getattr(tick_source, key) == expected, (
                f"tick_source.{key} not parsed: expected {expected!r}, "
                f"got {getattr(tick_source, key)!r}"
            )

    def test_staleness_contract_fields_parsed(self, tmp_path):
        """
        #436 contract knobs reach the config: the execution threshold
        (per-profile override over the app_config default) and the
        order_guard stale-entry block flag.
        """
        profile = json.loads(Path(MOCK_PROFILE).read_text())
        profile['execution'] = {'market_data_stale_after_s': 42.0}
        profile['order_guard'] = {'block_stale_market_data': False}
        profile_path = tmp_path / 'staleness_profile.json'
        profile_path.write_text(json.dumps(profile))

        config = load_autotrader_config(str(profile_path))
        assert config.execution.market_data_stale_after_s == 42.0
        assert config.order_guard.block_stale_market_data is False

        # JIC defaults (app_config mirror) when the profile stays silent
        default_config = load_autotrader_config(MOCK_PROFILE)
        assert default_config.execution.market_data_stale_after_s == 300.0
        assert default_config.order_guard.block_stale_market_data is True
