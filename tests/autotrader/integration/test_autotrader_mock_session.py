"""
FiniexTestingIDE - AutoTrader Mock Session Integration Test
End-to-end test: Config → Pipeline → Tick Loop → Shutdown → Result.

Uses mock_session_test.json profile with parquet replay data.
Deterministic: same data + same config = same results.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.reporting.io.artifact_specs import (
    BROKER_ARTIFACT,
)
from python.framework.reporting.io.report_artifact_io import read_artifact
from python.framework.reporting.store.report_store import IO_SUBDIR
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.log_level import LogLevel
from python.framework.types.log_record_types import LogRecord
from python.framework.types.run_outcome_types import RunOutcome
from tests.shared.fixture_helpers import logged_messages, remove_run_dir

MOCK_PROFILE = 'configs/autotrader_profiles/backtesting/mock_session_test.json'


@pytest.fixture(scope='module')
def mock_session():
    """
    Run one full mock session shared across all tests in this module.
    Avoids running 29782 ticks twice.
    """
    config = load_autotrader_config(MOCK_PROFILE)
    trader = AutotraderMain(config)
    result = trader.run()
    yield result, trader._run_dir
    remove_run_dir(trader._run_dir)


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

        # A normal shutdown must report success to whoever started the process (#372)
        assert result.get_exit_code() == 0, (
            f'Normal shutdown must exit 0, got {result.get_exit_code()}'
        )

        # === All ticks processed ===
        assert result.ticks_processed == 29782, (
            f'Expected 29782 ticks, got {result.ticks_processed}'
        )

        # === No clipping in replay mode ===
        assert result.ticks_clipped == 0, (
            f'Expected 0 clipped ticks in replay mode, got {result.ticks_clipped}'
        )

        # === Clean session — no unexpected warnings or errors ===
        # A position left open at the end is a POLICY outcome now, not a warning (#492):
        # the old 'positions remain open — direct-closing' line is gone with the
        # force-close it announced, so nothing has to be filtered out of this list.
        unexpected_warnings = list(logged_messages(result, LogLevel.WARNING))
        assert len(unexpected_warnings) == 0, (
            f'Unexpected warnings: {unexpected_warnings[:5]}'
        )
        assert len(logged_messages(result, LogLevel.ERROR)) == 0, (
            f'Unexpected errors: {logged_messages(result, LogLevel.ERROR)[:5]}'
        )

        # === Decision logic acted ===
        # Closed trades OR a position still open — this profile's algo does not exit
        # within its tick budget, so until #492 the only "trade" it ever produced was the
        # end-of-session force-close.
        assert len(result.trade_history) + len(result.open_positions) > 0, (
            'The session neither closed nor opened a position')
        assert len(result.order_history) > 0, 'No orders recorded'

        # === Portfolio stats collected ===
        assert result.portfolio_stats is not None, 'Missing portfolio stats'
        assert result.execution_stats is not None, 'Missing execution stats'

        # === Clipping monitor reported ===
        assert result.clipping_summary.total_ticks == 29782

    def test_log_files_created(self, mock_session):
        """Verify that all expected log files are created."""
        _, run_dir = mock_session

        assert run_dir.exists(), f'Run directory not created: {run_dir}'
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

        report = read_artifact(broker_artifact, BROKER_ARTIFACT)
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
                f'tick_source.{key} not parsed: expected {expected!r}, '
                f'got {getattr(tick_source, key)!r}'
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


class TestSessionExitCode:
    """The run outcome reaches the process exit code (#372)."""

    def test_framework_emergency_exits_two(self):
        """An emergency the operator did not initiate is a failed run."""
        result = AutoTraderResult(shutdown_mode='emergency',
                                  emergency_reason='tick loop crashed')
        assert result.get_outcome() == RunOutcome.FAILED
        assert result.get_exit_code() == 2

    def test_normal_shutdown_exits_zero(self):
        """A normal shutdown reports success."""
        assert AutoTraderResult(shutdown_mode='normal').get_exit_code() == 0

    def test_operator_interrupt_exits_zero(self):
        """
        A deliberate Ctrl+C is not a failed run.

        The SIGINT handler sets shutdown_mode='emergency' itself, so operator_interrupted
        is the only thing separating a deliberate stop from a crash.
        """
        result = AutoTraderResult(shutdown_mode='emergency', operator_interrupted=True)
        assert result.get_outcome() == RunOutcome.SUCCESS
        assert result.get_exit_code() == 0

    def test_safety_escalation_without_a_reason_still_fails(self):
        """
        A #348 EMERGENCY session-end escalation raises an emergency with no reason.

        Inferring 'the operator did it' from a missing reason would let the safety layer
        fire and still report success — the exact blindness this issue removes.
        """
        result = AutoTraderResult(shutdown_mode='emergency')
        assert result.get_outcome() == RunOutcome.FAILED
        assert result.get_exit_code() == 2

    def test_logged_errors_regrade_a_normal_run(self):
        """
        The §35 asymmetry, closed: a session that logged errors is not a clean run.

        This replaces the pinned assertion that held the old behaviour in place.
        """
        def _rec(level, message):
            return LogRecord(level=level, timestamp=datetime.now(timezone.utc),
                             scope='s', message=message)

        result = AutoTraderResult(shutdown_mode='normal',
                                  session_logger_buffer=[_rec(LogLevel.ERROR,
                                                              'something went wrong')])
        assert result.get_outcome() == RunOutcome.FINISHED_WITH_ERRORS
        assert result.get_exit_code() == 3

        # The other half of the contract: the grading reads the ERROR level, not the mere
        # presence of records. A chatty but clean session stays SUCCESS.
        clean = AutoTraderResult(shutdown_mode='normal',
                                 session_logger_buffer=[_rec(LogLevel.WARNING, 'noisy'),
                                                        _rec(LogLevel.INFO, 'chatter')])
        assert clean.get_outcome() == RunOutcome.SUCCESS
        assert clean.get_exit_code() == 0
