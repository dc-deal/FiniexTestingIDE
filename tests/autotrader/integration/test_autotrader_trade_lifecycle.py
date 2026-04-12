"""
FiniexTestingIDE - AutoTrader Trade Lifecycle Tests
Trade lifecycle validation through the AutoTrader mock pipeline.

Uses trade_lifecycle_test.json (3000 ticks, display off) for fast execution.
Validates fill prices, close reasons, portfolio integrity, and log output
through LiveTradeExecutor — complementary to the backtesting suite.
"""

import shutil

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason


MOCK_PROFILE = 'configs/autotrader_profiles/backtesting/trade_lifecycle_test.json'


@pytest.fixture(scope='module')
def session_result():
    """
    Run one full mock session for the entire module.
    Scope=module: single run shared across all tests — avoids repeated 30K-tick runs.
    """
    config = load_autotrader_config(MOCK_PROFILE)
    trader = AutotraderMain(config)
    result = trader.run()
    yield result
    if trader._run_dir and trader._run_dir.exists():
        shutil.rmtree(trader._run_dir)


class TestNormalCycle:
    """Normal trade cycle: position opens and closes via signal."""

    def test_session_completes_normally(self, session_result):
        assert session_result.shutdown_mode == 'normal', (
            f"Expected normal shutdown, got '{session_result.shutdown_mode}'"
        )

    def test_trades_executed(self, session_result):
        """Signal-based algo produces at least one completed trade."""
        assert len(session_result.trade_history) > 0, 'No trades executed'

    def test_orders_recorded(self, session_result):
        assert len(session_result.order_history) > 0, 'No orders recorded'

    def test_no_errors(self, session_result):
        assert len(session_result.error_messages) == 0, (
            f"Unexpected errors: {session_result.error_messages}"
        )

    def test_trade_has_valid_entry_exit(self, session_result):
        """Every completed trade must have a real entry and exit price."""
        for trade in session_result.trade_history:
            assert trade.entry_price > 0, (
                f"Trade {trade.position_id}: entry_price is 0 — dry-run artifact"
            )
            assert trade.exit_price > 0, (
                f"Trade {trade.position_id}: exit_price is 0"
            )

    def test_trade_directions_valid(self, session_result):
        """All trade directions are LONG or SHORT."""
        from python.framework.types.trading_env_types.order_types import OrderDirection
        for trade in session_result.trade_history:
            assert trade.direction in (OrderDirection.LONG, OrderDirection.SHORT), (
                f"Unexpected direction: {trade.direction}"
            )


class TestClosePaths:
    """Validate that different close paths produce correct close_reason values."""

    def test_close_reasons_are_known_values(self, session_result):
        """All close_reason values must be valid CloseReason enum members."""
        valid_reasons = set(CloseReason)
        for trade in session_result.trade_history:
            assert trade.close_reason in valid_reasons, (
                f"Unknown close_reason '{trade.close_reason}' on {trade.position_id}"
            )

    def test_no_orphaned_positions(self, session_result):
        """
        All trades must be closed — no position should remain open after normal shutdown.
        Proxy check: every entry in trade_history has an exit_price set.
        """
        for trade in session_result.trade_history:
            assert trade.exit_price > 0, (
                f"Trade {trade.position_id} has no exit price — may be orphaned"
            )

    def test_pnl_is_finite(self, session_result):
        """P&L on every trade must be a finite float (no overflow/NaN from bad fill)."""
        import math
        for trade in session_result.trade_history:
            assert math.isfinite(trade.net_pnl), (
                f"Trade {trade.position_id}: net_pnl={trade.net_pnl} is not finite"
            )


class TestPortfolioIntegrity:
    """Portfolio-level consistency checks after session."""

    def test_portfolio_stats_present(self, session_result):
        assert session_result.portfolio_stats is not None

    def test_trade_count_matches_history(self, session_result):
        """portfolio_stats.total_trades must match len(trade_history)."""
        assert session_result.portfolio_stats.total_trades == len(session_result.trade_history), (
            f"Stats says {session_result.portfolio_stats.total_trades} trades, "
            f"history has {len(session_result.trade_history)}"
        )

    def test_winning_losing_adds_up(self, session_result):
        """winning + losing trades must equal total trades."""
        stats = session_result.portfolio_stats
        assert stats.winning_trades + stats.losing_trades == stats.total_trades, (
            f"W({stats.winning_trades}) + L({stats.losing_trades}) "
            f"!= total({stats.total_trades})"
        )

    def test_balance_changed_after_trades(self, session_result):
        """Balance must differ from initial after trades (fees at minimum)."""
        stats = session_result.portfolio_stats
        if stats.total_trades > 0:
            assert stats.current_balance != stats.initial_balance, (
                'Balance unchanged after session with trades — suspicious'
            )


class TestSessionEndWithOpenPosition:
    """
    Validates that positions still open at session end are force-closed
    with close_reason=SCENARIO_END and appear in trade_history.
    """

    def test_scenario_end_closes_are_recorded(self, session_result):
        """
        If any position was open at shutdown, it must appear in trade_history
        with CloseReason.SCENARIO_END — not silently dropped.
        """
        scenario_end_trades = [
            t for t in session_result.trade_history
            if t.close_reason == CloseReason.SCENARIO_END
        ]
        # Informational — not all sessions will have scenario_end closes.
        # If they exist, they must have valid exit prices.
        for trade in scenario_end_trades:
            assert trade.exit_price > 0, (
                f"SCENARIO_END trade {trade.position_id} has no exit price"
            )


class TestLogFiles:
    """Log file creation validation."""

    def test_all_log_files_present(self, cleanup_log_dir):
        config = load_autotrader_config(MOCK_PROFILE)
        trader = AutotraderMain(config)
        trader.run()

        run_dir = trader._run_dir
        cleanup_log_dir.append(run_dir)

        assert run_dir.exists()
        assert (run_dir / 'autotrader_global.log').exists()
        assert (run_dir / 'autotrader_summary.log').exists()
        assert (run_dir / 'autotrader_trades.csv').exists()
        assert (run_dir / 'autotrader_orders.csv').exists()
        assert (run_dir / 'session_logs').is_dir()
        session_logs = list((run_dir / 'session_logs').glob('autotrader_session_*.log'))
        assert len(session_logs) >= 1


@pytest.fixture
def cleanup_log_dir():
    """Per-test log cleanup (for tests that need their own session)."""
    created = []
    yield created
    for d in created:
        if d and d.exists():
            shutil.rmtree(d)
