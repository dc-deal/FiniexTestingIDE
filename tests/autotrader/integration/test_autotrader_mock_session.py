"""
FiniexTestingIDE - AutoTrader Mock Session Integration Test
End-to-end test: Config → Pipeline → Tick Loop → Shutdown → Result.

Uses btcusd_mock.json profile with parquet replay data.
Deterministic: same data + same config = same results.
"""

import shutil
from pathlib import Path

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain


MOCK_PROFILE = 'configs/autotrader_profiles/backtesting/btcusd_mock.json'


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

    Runs a full session with btcusd_mock.json (parquet replay, ~30K ticks).
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
        assert (run_dir / 'autotrader_trades.csv').exists()
        assert (run_dir / 'autotrader_orders.csv').exists()

        # Session logs in subdirectory (tick-date based, not wall clock)
        session_logs = list((run_dir / 'session_logs').glob('autotrader_session_*.log'))
        assert len(session_logs) >= 1, 'No session log files created'
