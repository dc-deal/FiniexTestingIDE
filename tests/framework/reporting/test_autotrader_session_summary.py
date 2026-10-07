"""
AutoTrader session summary tests (#403 Phase 2).

`AutotraderSessionSummary` is the AutoTrader closing block of the unified end-of-run console:
session stats + warnings/errors (from the session buffers, §35) + output locations. Built against a real
AutoTraderResult (not a stand-in); rendered through the real ConsoleRenderer with stdout captured.
"""

import io
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from python.framework.reporting.console.autotrader_session_summary import AutotraderSessionSummary
from python.framework.types.api.report_types import (
    ReconcileDivergenceRow,
    VenueAccountReport,
    VenueAccountRow,
    VenueSnapshotRow,
)
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.live_types.broker_truth_types import BrokerTruthPart
from python.framework.types.live_types.reconciliation_types import ReconcileState
from python.framework.types.log_level import LogLevel
from python.framework.types.log_record_types import LogRecord
from python.framework.types.portfolio_types.portfolio_aggregation_types import PortfolioStats
from python.framework.types.portfolio_types.portfolio_types import Position
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.order_types import OrderDirection
from python.framework.utils.console_renderer import ConsoleRenderer


def _render(result: AutoTraderResult, run_dir=None, trade_report=None,
            venue_account_report: Optional[VenueAccountReport] = None) -> str:
    """Render the closing block and return the captured stdout (ANSI kept)."""
    old = sys.stdout
    sys.stdout = buf = io.StringIO()
    try:
        AutotraderSessionSummary(
            result, trade_report, run_dir,
            venue_account_report=venue_account_report).render(ConsoleRenderer())
    finally:
        sys.stdout = old
    return buf.getvalue()


def _open_position() -> Position:
    """One open LONG position. Returns: the position."""
    return Position(
        position_id='pos_btcusd_47', symbol='BTCUSD', direction=OrderDirection.LONG,
        lots=0.014, original_lots=0.014, entry_price=61430.0,
        entry_time=datetime(2026, 9, 4, 7, 0, tzinfo=timezone.utc))


def _stats_holding_one_position() -> PortfolioStats:
    """Portfolio stats carrying a mark on one open position. Returns: the stats."""
    return PortfolioStats(
        broker_type=BrokerType.KRAKEN_SPOT, total_trades=0, total_long_trades=0,
        total_short_trades=0, winning_trades=0, losing_trades=0, total_profit=0.0,
        total_loss=0.0, account_max_drawdown=0.0, max_equity=1000.0, account_max_drawdown_pct=0.0,
        win_rate=0.0,
        profit_factor=None, total_spread_cost=0.0, total_commission=0.0, total_swap=0.0,
        maker_fee=0.0, taker_fee=0.0, total_fees=1.57, fees_charged=1.57, currency='USD',
        broker_name='Kraken', current_conversion_rate=1.0, current_balance=358.80,
        initial_balance=1000.0, unrealized_pnl=10.50, spot_mode=True)


class TestAutotraderSessionSummary:
    """The live closing block renders the session outcome."""

    def test_an_open_position_appears_in_the_headline(self):
        """
        A session may END holding something (#492), and the headline must say so.

        The headline is what an operator reads first. Reporting only the realised balance
        there describes a flat account that is not flat — and the position sits further
        down in the portfolio section, past everything else.
        """
        result = AutoTraderResult(
            shutdown_mode='normal',
            portfolio_stats=_stats_holding_one_position(),
            open_positions=[_open_position()],
            session_end_policy='cancel/leave')

        out = _render(result)

        assert 'Still open:     1 position(s)' in out
        assert 'unrealized' in out
        assert 'realised' in out, 'the balance line must say which of the two it is'
        assert 'cancel/leave' in out, 'a position left by policy must be distinguishable'

    def test_a_flat_session_says_nothing_about_open_positions(self):
        result = AutoTraderResult(
            shutdown_mode='normal', portfolio_stats=_stats_holding_one_position())

        out = _render(result)

        assert 'Still open' not in out

    def test_renders_session_stats(self):
        result = AutoTraderResult(
            session_duration_s=4.5, ticks_processed=100, shutdown_mode='normal')
        out = _render(result, run_dir=Path('logs/autotrader/x/run'))
        assert 'AutoTrader Session Summary' in out
        assert 'Duration:' in out
        assert 'Shutdown:       normal' in out
        assert 'Log directory:  logs/autotrader/x/run' in out

    def test_emergency_cause_rendered(self):
        result = AutoTraderResult(shutdown_mode='emergency', emergency_reason='broker down')
        out = _render(result)
        assert 'EMERGENCY CAUSE: broker down' in out

    def test_operator_stop_is_named_as_one(self):
        """Ctrl+C ends as 'emergency' too — the line says which of the two it was."""
        out = _render(AutoTraderResult(shutdown_mode='emergency', operator_interrupted=True))
        assert 'Shutdown:       emergency (operator stop)' in out
        assert 'EMERGENCY CAUSE' not in out

        crashed = _render(AutoTraderResult(
            shutdown_mode='emergency', emergency_reason='broker down'))
        assert 'Shutdown:       emergency\n' in crashed

    def test_warnings_not_in_closing_block(self):
        # Warnings/errors moved to the shared WarningsSummary section (#403 Phase 2 follow-up);
        # the closing block no longer lists them (only the prominent emergency cause stays).
        result = AutoTraderResult(session_logger_buffer=[
            LogRecord(level=LogLevel.WARNING, timestamp=datetime.now(timezone.utc),
                      scope='s', message='1 position open')])
        out = _render(result)
        assert 'Warnings:' not in out
        assert '1 position open' not in out


def _venue_account(**row: object) -> VenueAccountReport:
    """
    A one-session venue-account report.

    Args:
        row: The row's fields beside its name

    Returns:
        The report
    """
    return VenueAccountReport(run_id='20261007_120000_ab12cd34',
                              units=[VenueAccountRow(name='btc_session', **row)])


class TestTheVenueBlock:
    """
    What the venue held, printed from the model (#362) — a part the venue did not answer for says
    so, and never reads as nothing.
    """

    def test_start_and_end_read_as_counts_and_balances(self):
        out = _render(AutoTraderResult(), venue_account_report=_venue_account(
            at_start=VenueSnapshotRow(seq=1, venue_order_count=0,
                                      venue_balances={'ZUSD': 812.4, 'XETH': 0.0031}),
            at_end=VenueSnapshotRow(seq=9, venue_order_count=1,
                                    unread_parts=[BrokerTruthPart.VENUE_BALANCES])))

        assert '🏦 Venue (broker truth)' in out
        assert '  At start:       0 open orders · ZUSD 812.4 · XETH 0.0031\n' in out
        assert '  At end:         1 open order · balances unread\n' in out, 'unread, not empty'

    def test_a_margin_account_counts_its_positions(self):
        out = _render(AutoTraderResult(), venue_account_report=_venue_account(
            at_start=VenueSnapshotRow(seq=1, venue_order_count=0, venue_position_count=2,
                                      venue_balances={'USD': 10000.0})))

        assert '  At start:       0 open orders · 2 positions · USD 10000\n' in out

    def test_an_empty_account_and_a_missing_read_are_told_apart(self):
        out = _render(AutoTraderResult(), venue_account_report=_venue_account(
            at_start=VenueSnapshotRow(seq=1, venue_order_count=0, venue_balances={})))

        assert '  At start:       0 open orders · no balances\n' in out
        assert '  At end:         not recorded\n' in out
        assert '  Reconciliation: no divergence recorded\n' in out

    def test_the_last_divergence_is_named_and_where_the_last_line_left_the_books(self):
        out = _render(AutoTraderResult(), venue_account_report=_venue_account(
            reconcile_lines=2, divergent_lines=1, last_reconcile_state=ReconcileState.CLEAN,
            last_divergence=ReconcileDivergenceRow(ghost_orders=['OQ3V2K-ABCDE-FGHIJK'],
                                                   orphan_positions=1)))

        assert '  Reconciliation: 2 lines written, 1 divergent — the last clean\n' in out
        assert 'last divergence: ghost orders: OQ3V2K-ABCDE-FGHIJK; orphan positions: 1' in out

    def test_a_session_that_asked_no_venue_has_no_block(self):
        assert '🏦' not in _render(AutoTraderResult())
