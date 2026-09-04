"""
FiniexTestingIDE - Session-End Reporting Tests (#492)

The two surfaces an operator actually reads, and the source the block edge's disposition
is built from.

The console guard is the one that would have gone unnoticed: `total_trades == 0` printed
"No trades executed" and RETURNED, skipping balances, costs and the position the unit was
holding. A buy-and-hold run therefore reported nothing at all — and it only became reachable
once the run end stopped force-closing.
"""

from datetime import datetime, timezone

from python.framework.process.process_block_boundary import build_block_boundary_report
from python.framework.reporting.console.portfolio_summary import PortfolioSummary
from python.framework.types.api.report_types import (
    AggregatedPortfolioReport,
    ExecutionStatsReport,
    ExecutionStatsTotals,
    OpenPositionRow,
    PendingOrdersReport,
    PortfolioReport,
    PortfolioUnitRow,
)
from python.framework.types.portfolio_types.portfolio_types import Position
from python.framework.types.trading_env_types.order_types import OrderDirection
from python.framework.utils.console_renderer import ConsoleRenderer

# A fixed instant: the tests assert on rendering, so the entry time must not move.
_ENTRY_TIME = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def _position(position_id: str = 'pos_btcusd_47') -> Position:
    """A minimal open LONG position. Args: position_id: Its id. Returns: the position."""
    return Position(
        position_id=position_id, symbol='BTCUSD', direction=OrderDirection.LONG,
        lots=0.014, original_lots=0.014, entry_price=61430.0, entry_time=_ENTRY_TIME)


def _buy_and_hold_row() -> PortfolioUnitRow:
    """A unit that opened one position and closed no trade. Returns: its portfolio row."""
    return PortfolioUnitRow(
        name='session_end_probe', symbol='BTCUSD', currency='USD',
        total_trades=0, winning_trades=0, losing_trades=0, win_rate=0.0,
        profit_factor=None, total_profit=0.0, total_loss=0.0, net_profit=0.0,
        max_drawdown=0.0, total_fees=1.57,
        spot_mode=True, current_balance=358.80, initial_balance=1000.0,
        base_currency='BTC', quote_currency='USD',
        balances={'USD': 358.80, 'BTC': 0.014}, initial_balances={'USD': 1000.0},
        last_price=62180.0, spot_est_current=1229.32, spot_est_initial=1000.0,
        spot_est_pnl=229.32, spot_est_pnl_pct=22.93,
        open_positions=[OpenPositionRow(
            position_id='pos_btcusd_47', direction='long', lots=0.014,
            entry_price=61430.0, entry_time=_ENTRY_TIME.isoformat(),
            last_price=62180.0, unrealized_pnl=10.50, valued=True)],
        unrealized_pnl=10.50, final_equity=1229.32,
        session_end_policy='cancel/leave')


_RUN_ID = '20260903_150000_abcd1234'


def _render(row: PortfolioUnitRow, capsys) -> str:
    """Render one unit row through the public per-scenario path and return its output.

    Args:
        row: The unit row
        capsys: pytest's capture fixture

    Returns:
        The printed text
    """
    summary = PortfolioSummary(
        report=PortfolioReport(run_id=_RUN_ID, units=[row], aggregates=[]),
        pending_report=PendingOrdersReport(run_id=_RUN_ID, units=[]),
        execution_report=ExecutionStatsReport(
            run_id=_RUN_ID, units=[], totals=ExecutionStatsTotals()),
        aggregated_report=AggregatedPortfolioReport(run_id=_RUN_ID),
    )
    summary.render_per_scenario(ConsoleRenderer())
    return capsys.readouterr().out


class TestTheConsoleDoesNotHideABuyAndHoldRun:
    """The early exit used to swallow everything a holding unit had to say."""

    def test_the_balances_are_rendered(self, capsys):
        output = _render(_buy_and_hold_row(), capsys)

        assert 'No trades executed' not in output, (
            'the unit held a position — the early exit swallowed its whole report')
        assert 'BTC' in output and '358.80' in output

    def test_the_open_position_is_rendered(self, capsys):
        output = _render(_buy_and_hold_row(), capsys)

        assert 'pos_btcusd_47' in output
        assert 'Open at end' in output
        assert '61,430' in output, 'the entry price is missing'
        assert '62,180' in output, 'the mark is missing'

    def test_the_wealth_view_is_labelled_and_separate(self, capsys):
        output = _render(_buy_and_hold_row(), capsys)

        assert 'Final equity' in output
        assert 'unrealised' in output
        assert '(realised)' in output, (
            'net_profit must say which of the two figures it is')

    def test_the_policy_is_stated(self, capsys):
        output = _render(_buy_and_hold_row(), capsys)

        assert 'cancel/leave' in output, (
            'a position left by decision must be distinguishable from a missing one')

    def test_a_truly_empty_unit_still_says_nothing_happened(self, capsys):
        """The early exit is right where there is genuinely nothing — only there."""
        row = _buy_and_hold_row()
        row.open_positions = []
        row.session_end_policy = ''

        output = _render(row, capsys)

        assert 'No trades executed' in output

    def test_an_unvalued_position_is_marked_as_such(self, capsys):
        """No tick, no mark — and the reader is told rather than shown a zero."""
        row = _buy_and_hold_row()
        row.open_positions[0].valued = False
        row.open_positions[0].last_price = 0.0

        output = _render(row, capsys)

        assert 'not valued' in output

    def test_the_equity_is_not_called_marked_to_market_when_nothing_was_marked(self, capsys):
        """
        Two lines that used to contradict each other.

        Without a price the figure is the balance alone and the holding counts as ZERO —
        the very understatement this section removes — while the line beside it correctly
        said the position could not be valued.
        """
        row = _buy_and_hold_row()
        row.open_positions[0].valued = False
        row.open_positions[0].last_price = 0.0
        row.final_equity_valued = False

        output = _render(row, capsys)

        assert 'marked to market' not in output
        assert 'nothing could be valued' in output

    def test_it_is_called_marked_to_market_when_it_is_one(self, capsys):
        output = _render(_buy_and_hold_row(), capsys)

        assert 'marked to market' in output


class TestTheBoundaryReportReadsTheOpenPositions:
    """The source side of the block-edge disposition (#214 x #492)."""

    def test_an_open_position_becomes_the_edge_impact(self):
        position = _position()
        position.unrealized_pnl = -6.25

        report = build_block_boundary_report(
            trade_history=[], pending_stats=None, open_positions=[position])

        assert report.open_at_boundary_trades == 1
        assert report.open_at_boundary_pnl == -6.25

    def test_a_flat_block_reports_no_impact(self):
        report = build_block_boundary_report(
            trade_history=[], pending_stats=None, open_positions=[])

        assert report.open_at_boundary_trades == 0
        assert report.open_at_boundary_pnl == 0.0

    def test_an_unvalued_position_contributes_zero_rather_than_a_guess(self):
        """A position no tick ever priced carries 0.0 — honest, not invented."""
        report = build_block_boundary_report(
            trade_history=[], pending_stats=None, open_positions=[_position()])

        assert report.open_at_boundary_trades == 1
        assert report.open_at_boundary_pnl == 0.0
