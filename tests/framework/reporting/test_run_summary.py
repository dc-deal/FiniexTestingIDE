"""
Run-Summary Builder Tests (#390 prework).

Composes the cross-section KPI model from the section reports (portfolio aggregates +
trade analytics + execution totals) — no re-derivation. Per-currency join + global counts.
"""

from python.framework.reporting.io.artifact_specs import (
    RUN_SUMMARY_ARTIFACT,
)
from python.framework.reporting.io.report_artifact_io import read_artifact, write_artifact
from python.framework.reporting.builders.report_aggregators import (
    aggregate_portfolio_by_currency,
)
from python.framework.reporting.builders.run_summary_builder import build_run_summary
from python.framework.types.api.report_types import (
    AbsentUnitRow,
    ExecutionStatsReport,
    ExecutionStatsTotals,
    PortfolioAggregateRow,
    PortfolioReport,
    PortfolioUnitRow,
    RunSummary,
    TradeAnalytics,
    TradeHistoryReport,
    UnitRoster,
)

# Every report artifact names its run (#475); the value is opaque to these tests.
_RUN_ID = '20260830_120000_a1b2c3d4'


def _agg(currency='USD', net=60.0) -> PortfolioAggregateRow:
    return PortfolioAggregateRow(
        currency=currency, unit_count=1, total_trades=10, winning_trades=6, losing_trades=4,
        win_rate=0.6, profit_factor=2.5, total_profit=100.0, total_loss=40.0,
        net_profit=net, account_max_drawdown=12.0, total_fees=5.0)


def _unit(currency='USD') -> PortfolioUnitRow:
    return PortfolioUnitRow(
        name='s1', symbol='EURUSD', currency=currency, total_trades=10, winning_trades=6,
        losing_trades=4, win_rate=0.6, profit_factor=2.5, total_profit=100.0, total_loss=40.0,
        net_profit=60.0, account_max_drawdown=12.0, total_fees=5.0)


def _analytics(currency='USD', expectancy=0.5) -> TradeAnalytics:
    return TradeAnalytics(
        currency=currency, trade_count=4, expectancy=expectancy, avg_win_r=2.0,
        avg_loss_r=-1.0, r_trade_count=4, avg_mae_winners=0.0, avg_mae_losers=0.0,
        avg_mfe_losers=0.0)


def _exec(sent=5, ex=4, rej=1, sltp=2) -> ExecutionStatsReport:
    return ExecutionStatsReport(run_id=_RUN_ID, units=[], totals=ExecutionStatsTotals(
        orders_sent=sent, orders_executed=ex, orders_rejected=rej, sl_tp_triggered=sltp))


class TestBuild:
    def test_composes_per_currency(self):
        portfolio = PortfolioReport(run_id=_RUN_ID, units=[_unit()], aggregates=[_agg()])
        trade = TradeHistoryReport(run_id=_RUN_ID, trades=[], count=0, symbols=[], analytics=[_analytics()])
        rs = build_run_summary(_RUN_ID, portfolio, trade, _exec())
        assert len(rs.currencies) == 1
        c = rs.currencies[0]
        assert c.currency == 'USD'
        assert (c.net_pnl, c.profit_factor, c.win_rate) == (60.0, 2.5, 0.6)
        assert (c.account_max_drawdown, c.total_fees) == (12.0, 5.0)
        assert (c.expectancy, c.avg_win_r, c.r_trade_count) == (0.5, 2.0, 4)
        assert (rs.orders_sent, rs.orders_executed, rs.sl_tp_triggered) == (5, 4, 2)
        assert rs.unit_count == 1

    def test_currency_without_analytics_defaults_r(self):
        # portfolio currency present, no matching trade analytics → R fields default 0
        portfolio = PortfolioReport(run_id=_RUN_ID, units=[_unit('JPY')], aggregates=[_agg(currency='JPY')])
        trade = TradeHistoryReport(run_id=_RUN_ID, trades=[], count=0, symbols=[], analytics=[])
        c = build_run_summary(_RUN_ID, portfolio, trade, _exec()).currencies[0]
        assert c.currency == 'JPY' and c.net_pnl == 60.0
        assert c.expectancy == 0.0 and c.r_trade_count == 0

    def test_multi_currency(self):
        portfolio = PortfolioReport(run_id=_RUN_ID, 
            units=[_unit('USD'), _unit('JPY')],
            aggregates=[_agg('USD', net=60.0), _agg('JPY', net=100.0)])
        trade = TradeHistoryReport(run_id=_RUN_ID, 
            trades=[], count=0, symbols=[],
            analytics=[_analytics('USD'), _analytics('JPY', expectancy=1.0)])
        rs = build_run_summary(_RUN_ID, portfolio, trade, _exec())
        by = {c.currency: c for c in rs.currencies}
        assert by['USD'].net_pnl == 60.0 and by['JPY'].net_pnl == 100.0
        assert by['JPY'].expectancy == 1.0
        assert rs.unit_count == 2


class TestTheRosterSaysWhatIsMissing:
    """
    The figures above are summed over the units that produced something. The roster says how
    many were DECLARED and what became of the rest, so a run of ten with two rejected no longer
    reads as a run of eight (contract 6).
    """

    @staticmethod
    def _reports():
        portfolio = PortfolioReport(run_id=_RUN_ID, units=[_unit()], aggregates=[_agg()])
        trade = TradeHistoryReport(run_id=_RUN_ID, trades=[], count=0, symbols=[],
                                   analytics=[_analytics()])
        return portfolio, trade

    def test_the_roster_reaches_the_summary(self):
        portfolio, trade = self._reports()
        roster = UnitRoster(declared=4, disabled=2, absent=[
            AbsentUnitRow(name='w_01', reason='Warmup for M30 has 1/20 bars')])

        rs = build_run_summary(_RUN_ID, portfolio, trade, _exec(), roster=roster)

        assert (rs.units_declared, rs.units_disabled, rs.unit_count) == (4, 2, 1)
        assert [(row.name, row.reason) for row in rs.units_absent] == [
            ('w_01', 'Warmup for M30 has 1/20 bars')]
        assert rs.units_declared == rs.units_disabled + len(rs.units_absent) + rs.unit_count

    def test_without_a_roster_every_summed_unit_is_declared(self):
        """A summary over units a caller already chose — one robustness window — is complete."""
        portfolio, trade = self._reports()

        rs = build_run_summary(_RUN_ID, portfolio, trade, _exec())

        assert (rs.units_declared, rs.units_disabled, rs.units_absent) == (1, 0, [])

    def test_a_run_recorded_before_the_roster_states_nothing_rather_than_zero(self):
        """
        An artifact written before contract 6 carries no roster. Read back, it says NOTHING —
        null — rather than 0, which the invariant would then disprove on every old run.
        """
        old = RunSummary.model_validate({'run_id': _RUN_ID, 'currencies': [], 'unit_count': 8})
        assert (old.units_declared, old.units_disabled, old.units_absent) == (None, None, None)


class TestUndefinedProfitFactor:
    """A run without a losing trade has no profit factor — and must still round-trip.

    Regression: the value was minted as float('inf'), which Pydantic persists as JSON null,
    and the reader declared a plain float. The API could not serve a run that only won.
    """

    def test_builder_carries_none_through(self):
        agg = _agg()
        agg.profit_factor = None
        portfolio = PortfolioReport(run_id=_RUN_ID, units=[_unit()], aggregates=[agg])
        trade = TradeHistoryReport(run_id=_RUN_ID, trades=[], count=0, symbols=[], analytics=[])
        assert build_run_summary(_RUN_ID, portfolio, trade, _exec()).currencies[0].profit_factor is None

    def test_survives_the_json_round_trip(self, tmp_path):
        agg = _agg()
        agg.profit_factor = None
        portfolio = PortfolioReport(run_id=_RUN_ID, units=[_unit()], aggregates=[agg])
        trade = TradeHistoryReport(run_id=_RUN_ID, trades=[], count=0, symbols=[], analytics=[])
        summary = build_run_summary(_RUN_ID, portfolio, trade, _exec())
        read_back = read_artifact(write_artifact(summary, tmp_path, RUN_SUMMARY_ARTIFACT), RUN_SUMMARY_ARTIFACT)
        assert read_back.currencies[0].profit_factor is None

    def test_aggregator_mints_none_not_infinity(self):
        """The producer side: no gross loss means undefined, never an unpersistable inf."""
        row = _unit()
        row.total_loss = 0.0
        row.losing_trades = 0
        assert aggregate_portfolio_by_currency([row])[0].profit_factor is None
