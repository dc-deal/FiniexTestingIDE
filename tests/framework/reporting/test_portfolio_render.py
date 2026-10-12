"""
Portfolio Console Tests — where a session's fill fees came from, and every sign (#508).

`PortfolioSummary` and the executive summary print money purely from the model. Two things are
pinned here. The line saying where a unit's fill fees came from — the venue's own figure or our
fee model's estimate — is an AutoTrader session's: every fee of a backtest is the estimate, so a
backtest prints none, while its model still carries the split. And a figure that can go below zero
keeps its minus, in front of the currency symbol, where Unicode CLDR and JavaScript's
Intl.NumberFormat put it — `-$0.49`, `-€44,56`. The magnitude formatter those figures went through
drops the sign, which printed a loss like a gain. Every money test runs in both account models.
"""

import io
import re
from contextlib import redirect_stdout
from typing import Any, Dict, List

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.framework.reporting.builders.aggregated_portfolio_report_builder import (
    build_aggregated_portfolio_report,
)
from python.framework.reporting.console.portfolio_summary import PortfolioSummary
from python.framework.reporting.console.sim_executive_summary import SimExecutiveSummary
from python.framework.types.api.report_types import (
    AggregatedPortfolioReport,
    ExecutionStatsReport,
    ExecutionStatsTotals,
    FeeSourceTotals,
    OpenPositionRow,
    PendingOrdersReport,
    PortfolioReport,
    PortfolioUnitRow,
    ProfilingReport,
    RunMetaReport,
    RunSummary,
    ScenarioDetailsReport,
    WarningsErrorsReport,
)
from python.framework.types.trading_env_types.broker_types import FeeSource
from python.framework.types.trading_env_types.currency_codes import (
    format_currency_signed,
    format_currency_simple,
)
from python.framework.utils.console_renderer import ConsoleRenderer

# Every report artifact names its run (#475); the value is opaque to these tests.
_RUN_ID = '20261011_120000_a1b2c3d4'
_ANSI = re.compile(r'\x1b\[[0-9;]*m')
# What an AutoTrader session's unit carries and a backtest's scenario never does
_SESSION_POLICY = 'cancel/leave'
_ACCOUNT_MODELS = pytest.mark.parametrize('spot_mode', [False, True], ids=['margin', 'spot'])


def _unit(spot_mode: bool, currency: str = 'USD', **fields: Any) -> PortfolioUnitRow:
    """
    A unit that closed one losing trade and holds nothing, in the given account model.

    Args:
        spot_mode: Spot account (True) or margin account (False)
        currency: The account currency — the spot quote currency as well
        **fields: Row fields to set on top

    Returns:
        The unit's portfolio row
    """
    row: Dict[str, Any] = dict(
        name='ethusd_session', symbol=f'ETH{currency}', currency=currency, total_trades=1,
        winning_trades=0, losing_trades=1, win_rate=0.0, profit_factor=0.0, total_profit=0.0,
        total_loss=0.49, net_profit=-0.49, account_max_drawdown=0.49, total_fees=0.49,
        spot_mode=spot_mode, current_balance=99.51, initial_balance=100.0)
    if spot_mode:
        row.update(
            base_currency='ETH', quote_currency=currency,
            balances={currency: 99.51, 'ETH': 0.0}, initial_balances={currency: 100.0},
            last_price=2500.0, spot_est_current=99.51, spot_est_initial=100.0,
            spot_est_pnl=-0.49, spot_est_pnl_pct=-0.49)
    row.update(fields)
    return PortfolioUnitRow(**row)


def _split(**sources: tuple) -> Dict[FeeSource, FeeSourceTotals]:
    """
    A fee-source split, in the order the model folds it — venue, then estimate.

    Args:
        **sources: `venue` / `estimate` → (fill count, fill fees)

    Returns:
        The split
    """
    return {source: FeeSourceTotals(fill_count=sources[source.value][0],
                                    fill_fees=sources[source.value][1])
            for source in FeeSource if source.value in sources}


def _render_unit(unit: PortfolioUnitRow) -> str:
    """
    Render one unit through the public per-scenario path.

    Args:
        unit: The unit row

    Returns:
        The printed text, colours stripped
    """
    summary = PortfolioSummary(
        report=PortfolioReport(run_id=_RUN_ID, units=[unit], aggregates=[]),
        pending_report=PendingOrdersReport(run_id=_RUN_ID, units=[]),
        execution_report=ExecutionStatsReport(
            run_id=_RUN_ID, units=[], totals=ExecutionStatsTotals()),
        aggregated_report=AggregatedPortfolioReport(run_id=_RUN_ID))
    buf = io.StringIO()
    with redirect_stdout(buf):
        summary.render_per_scenario(ConsoleRenderer())
    return _ANSI.sub('', buf.getvalue())


def _lines(output: str) -> List[str]:
    """
    The printed lines, stripped of their indent.

    Args:
        output: Rendered text

    Returns:
        One entry per line
    """
    return [line.strip() for line in output.splitlines()]


def _line(output: str, prefix: str) -> str:
    """
    The one printed line that starts with the prefix.

    Args:
        output: Rendered text
        prefix: What the line starts with, after its indent

    Returns:
        That line
    """
    found = [line for line in _lines(output) if line.startswith(prefix)]
    assert len(found) == 1, f'expected one line starting {prefix!r}, got {found}'
    return found[0]


def _aggregated(units: List[PortfolioUnitRow]) -> AggregatedPortfolioReport:
    """
    The aggregated report the batch builds from the unit rows.

    Args:
        units: The unit rows

    Returns:
        The aggregated per-currency report
    """
    return build_aggregated_portfolio_report(
        _RUN_ID, PortfolioReport(run_id=_RUN_ID, units=units, aggregates=[]),
        ExecutionStatsReport(run_id=_RUN_ID, units=[], totals=ExecutionStatsTotals()),
        PendingOrdersReport(run_id=_RUN_ID, units=[]))


def _render_executive(units: List[PortfolioUnitRow]) -> str:
    """
    Render the executive summary through its public method.

    Args:
        units: The unit rows its portfolio blocks are rolled up from

    Returns:
        The printed text, colours stripped
    """
    executive = SimExecutiveSummary(
        app_config=AppConfigManager(), run_summary=RunSummary(run_id=_RUN_ID, currencies=[]),
        run_meta_report=RunMetaReport(run_id=_RUN_ID),
        profiling_report=ProfilingReport(run_id=_RUN_ID, units=[]),
        scenario_details_report=ScenarioDetailsReport(run_id=_RUN_ID, units=[]),
        warnings_errors_report=WarningsErrorsReport(run_id=_RUN_ID),
        aggregated_report=_aggregated(units))
    buf = io.StringIO()
    with redirect_stdout(buf):
        executive.render(ConsoleRenderer())
    return _ANSI.sub('', buf.getvalue())


class TestTheFeeSourceLine:
    """Where a session's fill fees came from, one line under its costs (#508)."""

    @_ACCOUNT_MODELS
    def test_a_session_whose_venue_reported_every_fee(self, spot_mode):
        output = _render_unit(_unit(
            spot_mode, session_end_policy=_SESSION_POLICY,
            fee_source_totals=_split(venue=(13, 0.4944))))

        lines = _lines(output)
        line = _line(output, 'Fee source:')
        assert line == 'Fee source: venue 13 fills 0.4944 USD'
        assert lines[lines.index(line) - 1].startswith('Cost:'), 'it stands under the costs'

    @_ACCOUNT_MODELS
    def test_one_estimated_fill_is_named_beside_the_venues(self, spot_mode):
        output = _render_unit(_unit(
            spot_mode, session_end_policy=_SESSION_POLICY,
            fee_source_totals=_split(venue=(12, 0.4532), estimate=(1, 0.0412))))

        assert _line(output, 'Fee source:') == (
            'Fee source: venue 12 fills 0.4532 USD | estimate 1 fill 0.0412 USD')

    @_ACCOUNT_MODELS
    def test_a_session_booked_from_the_estimate_says_so(self, spot_mode):
        """A dry run: the venue reports its fee, its answers come from our own simulator."""
        output = _render_unit(_unit(
            spot_mode, session_end_policy=_SESSION_POLICY,
            fee_source_totals=_split(estimate=(13, 0.4944))))

        assert _line(output, 'Fee source:') == 'Fee source: estimate 13 fills 0.4944 USD'

    @_ACCOUNT_MODELS
    def test_a_backtest_prints_none_and_still_carries_the_split(self, spot_mode):
        unit = _unit(spot_mode, fee_source_totals=_split(estimate=(13, 0.4944)))

        assert 'Fee source' not in _render_unit(unit)
        assert unit.fee_source_totals[FeeSource.ESTIMATE].fill_count == 13

    @_ACCOUNT_MODELS
    def test_a_session_charged_nothing_per_fill_prints_none(self, spot_mode):
        """A spread broker: its fills carry the estimate at zero — there is no fee to place."""
        unit = _unit(spot_mode, session_end_policy=_SESSION_POLICY,
                     fee_source_totals=_split(estimate=(13, 0.0)))

        assert 'Fee source' not in _render_unit(unit)

    @_ACCOUNT_MODELS
    def test_a_venue_figure_of_zero_is_still_the_venues(self, spot_mode):
        unit = _unit(spot_mode, session_end_policy=_SESSION_POLICY,
                     fee_source_totals=_split(venue=(2, 0.0)))

        assert _line(_render_unit(unit), 'Fee source:') == 'Fee source: venue 2 fills 0.0000 USD'

    @_ACCOUNT_MODELS
    def test_a_session_that_executed_nothing_prints_none(self, spot_mode):
        unit = _unit(spot_mode, session_end_policy=_SESSION_POLICY)

        assert 'Fee source' not in _render_unit(unit)


class TestTheSignedFormatter:
    """The minus goes in front of the symbol; the magnitude formatter stays what callers rely on."""

    @pytest.mark.parametrize('amount, currency, printed', [
        (-0.49, 'USD', '-$0.49'),
        (-44.56, 'EUR', '-€44,56'),
        (-1234.5, 'CHF', "-CHF 1'234.50"),
        (-1.5, 'XYZ', '-XYZ 1.50'),
        (44.56, 'EUR', '€44,56'),
        (0.0, 'USD', '$0.00'),
    ])
    def test_the_minus_stands_before_the_symbol(self, amount, currency, printed):
        assert format_currency_signed(amount, currency) == printed

    def test_a_plus_sign_on_request(self):
        assert format_currency_signed(0.49, 'USD', plus_sign=True) == '+$0.49'
        assert format_currency_signed(0.0, 'USD', plus_sign=True) == '+$0.00'
        assert format_currency_signed(-0.49, 'USD', plus_sign=True) == '-$0.49'

    def test_the_magnitude_formatter_still_drops_the_sign(self):
        """The console's P&L formatter puts the sign in front itself — a signed magnitude would
        print it twice."""
        assert format_currency_simple(-0.49, 'USD') == '$0.49'
        assert _ANSI.sub('', ConsoleRenderer().pnl(-0.04, 'USD')) == '-$0.04'


class TestASignedFigureKeepsItsSign:
    """A figure that can go below zero keeps its minus on every surface the console prints."""

    def test_a_spot_loss_is_printed_as_one(self):
        line = _line(_render_unit(_unit(True)), 'Est:')

        assert line == 'Est: -$0.49 (-0.49%) @ ETH $2,500.00'

    def test_a_spot_gain_keeps_its_plus(self):
        unit = _unit(True, spot_est_pnl=0.49, spot_est_pnl_pct=0.49)

        assert _line(_render_unit(unit), 'Est:') == 'Est: +$0.49 (+0.49%) @ ETH $2,500.00'

    def test_usable_funds_below_zero_print_their_minus(self):
        """Adoption can inherit a reserve the balance has moved past — the case worth seeing."""
        unit = _unit(True, committed_funds={'USD': 299.51}, usable_funds={'USD': -200.0})

        assert _line(_render_unit(unit), 'Usable:').startswith('Usable: -$200.00 |')

    def test_a_margin_balance_below_zero_prints_its_minus(self):
        unit = _unit(False, current_balance=-12.5)

        assert _line(_render_unit(unit), 'Balance:') == 'Balance: -$12.50 (init $100.00)'

    @_ACCOUNT_MODELS
    def test_a_swap_credit_and_a_price_improvement_are_not_zero(self, spot_mode):
        """Both are signed costs: below zero they are a gain, which the cost line used to zero."""
        unit = _unit(spot_mode, total_swap=-3.80, total_spread_cost=-1.20)

        line = _line(_render_unit(unit), 'Cost:')

        assert 'swap +$3.80' in line and 'spread +$1.20' in line

    @_ACCOUNT_MODELS
    def test_a_swap_debit_and_a_spread_cost_stay_costs(self, spot_mode):
        unit = _unit(spot_mode, total_swap=3.80, total_spread_cost=1.20, taker_fee=0.04)

        line = _line(_render_unit(unit), 'Cost:')

        assert 'swap -$3.80' in line and 'spread -$1.20' in line and 'taker -$0.04' in line

    @_ACCOUNT_MODELS
    def test_the_aggregated_cost_breakdown_keeps_a_credit(self, spot_mode):
        units = [_unit(spot_mode, name='s1', total_swap=-3.80),
                 _unit(spot_mode, name='s2', total_swap=0.0)]
        summary = PortfolioSummary(
            PortfolioReport(run_id=_RUN_ID, units=units, aggregates=[]),
            PendingOrdersReport(run_id=_RUN_ID, units=[]),
            ExecutionStatsReport(run_id=_RUN_ID, units=[], totals=ExecutionStatsTotals()),
            _aggregated(units))
        buf = io.StringIO()
        with redirect_stdout(buf):
            summary.render_aggregated(ConsoleRenderer())

        output = _ANSI.sub('', buf.getvalue())
        assert 'Swap: +$3.80' in _line(output, 'Spread:')
        # The total is signed like its parts: here the credit outweighs the costs
        assert 'Total Fees: +$3.80' in _line(output, 'Maker:')

    def test_the_executive_summary_prints_an_unrealized_loss_as_one(self):
        position = OpenPositionRow(
            position_id='pos_etheur_7', direction='long', lots=0.5, entry_price=2600.0,
            entry_time='2026-10-11T08:00:00+00:00', last_price=2510.88,
            unrealized_pnl=-44.56, valued=True)
        unit = _unit(False, currency='EUR', open_positions=[position], unrealized_pnl=-44.56,
                     final_equity=55.44)

        output = _render_executive([unit])

        assert _line(output, 'Open at end:') == (
            'Open at end:        1 position(s) (-€44,56 unrealized)')
        assert _line(output, 'Final Equity:') == 'Final Equity:       €55,44'

    @_ACCOUNT_MODELS
    def test_the_executive_summary_prints_its_costs_in_the_pnl_sign(self, spot_mode):
        """A charge reads negative and a credit positive — as on every cost line, and as a broker
        statement prints them."""
        credit = _render_executive(
            [_unit(spot_mode, currency='EUR', total_swap=-3.80, total_spread_cost=-1.20)])
        charge = _render_executive(
            [_unit(spot_mode, currency='EUR', total_swap=2.00, total_spread_cost=1.50,
                   taker_fee=0.40)])

        assert _line(credit, 'Swap:') == 'Swap:               +€3,80'
        assert _line(credit, 'Spread Cost:').startswith('Spread Cost:        +€1,20')
        assert _line(charge, 'Swap:') == 'Swap:               -€2,00'
        assert _line(charge, 'Spread Cost:').startswith('Spread Cost:        -€1,50')
        assert _line(charge, 'Taker Fee:') == 'Taker Fee:          -€0,40'
        assert _line(charge, 'Maker Fee:') == 'Maker Fee:          €0,00'
