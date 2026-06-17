"""
Portfolio Report Builder Tests (#391).

One builder feeds the array model (units + per-currency aggregates), the roll-up derived from
the unit rows via the shared aggregator. Tested against a **real** BatchExecutionSummary /
ProcessResult / ProcessTickLoopResult / SingleScenario (not stand-ins), extracted into
RunUnits — so it exercises the actual attribute access of the persist path (the symbol comes
from the index-synced SingleScenario, since ProcessResult carries none). The same real batch
also covers the RunUnit record extraction. The live builder uses a real AutoTraderResult.
"""

from datetime import datetime, timezone

from python.framework.reporting.run_reports.portfolio_report_builder import build_portfolio_report
from python.framework.reporting.run_reports.run_unit import (
    run_units_from_batch, run_units_from_session)
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.portfolio_types.portfolio_aggregation_types import PortfolioStats
from python.framework.types.process_data_types import ProcessResult, ProcessTickLoopResult
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.order_types import (
    OrderAction, OrderDirection, OrderResult, OrderStatus)


_DT = datetime(2025, 10, 13, tzinfo=timezone.utc)


def _stats(
    currency: str = 'USD',
    total_trades: int = 10,
    winning_trades: int = 6,
    losing_trades: int = 4,
    total_profit: float = 100.0,
    total_loss: float = 40.0,
    win_rate: float = 0.6,
    profit_factor: float = 2.5,
    total_fees: float = 5.0,
    max_drawdown: float = 12.0,
) -> PortfolioStats:
    """A real PortfolioStats fixture with sensible headline numbers."""
    return PortfolioStats(
        broker_type=BrokerType.KRAKEN_SPOT,
        total_trades=total_trades, total_long_trades=total_trades, total_short_trades=0,
        winning_trades=winning_trades, losing_trades=losing_trades,
        total_profit=total_profit, total_loss=total_loss,
        max_drawdown=max_drawdown, max_equity=1100.0,
        win_rate=win_rate, profit_factor=profit_factor,
        total_spread_cost=2.0, total_commission=2.0, total_swap=1.0, total_fees=total_fees,
        currency=currency, broker_name='kraken', current_conversion_rate=1.0,
        current_balance=1060.0, initial_balance=1000.0, symbol='BTCUSD',
    )


def _order(order_id: str, symbol: str) -> OrderResult:
    return OrderResult(
        order_id=order_id, status=OrderStatus.EXECUTED, symbol=symbol,
        direction=OrderDirection.LONG, action=OrderAction.OPEN,
        executed_lots=0.1, executed_price=1.1, requested_lots=0.1)


def _process_result(name, idx, stats, orders) -> ProcessResult:
    return ProcessResult(
        success=True, scenario_name=name, scenario_index=idx,
        tick_loop_results=ProcessTickLoopResult(portfolio_stats=stats, order_history=orders))


def _scenario(name, idx, symbol) -> SingleScenario:
    return SingleScenario(
        name=name, scenario_index=idx, symbol=symbol,
        data_broker_type='mt5', start_date=_DT)


def _batch(extra_results=None) -> BatchExecutionSummary:
    """A real two-scenario batch (USD), with order history per scenario."""
    results = [
        _process_result('s1', 0, _stats(total_trades=10), [_order('o1', 'EURUSD')]),
        _process_result('s2', 1, _stats(total_trades=4),
                        [_order('o2', 'GBPUSD'), _order('o3', 'GBPUSD')]),
    ]
    if extra_results:
        results.extend(extra_results)
    scenarios = [_scenario('s1', 0, 'EURUSD'), _scenario('s2', 1, 'GBPUSD')]
    return BatchExecutionSummary(
        batch_execution_time=0.0, batch_warmup_time=0.0, batch_tickrun_time=0.0,
        process_result_list=results, single_scenario_list=scenarios)


class TestBatch:
    """sim: N scenario units (symbol from SingleScenario) + per-currency roll-up from the rows."""

    def test_units_use_scenario_symbol(self):
        report = build_portfolio_report(run_units_from_batch(_batch()))

        assert [u.name for u in report.units] == ['s1', 's2']
        # symbol is NOT on ProcessResult — must resolve via the index-synced scenario
        assert [u.symbol for u in report.units] == ['EURUSD', 'GBPUSD']
        assert len(report.aggregates) == 1
        assert report.aggregates[0].currency == 'USD'
        assert report.aggregates[0].unit_count == 2
        assert report.aggregates[0].total_trades == 14          # 10 + 4 (summed from rows)
        assert report.aggregates[0].net_profit == 120.0          # (100-40) + (100-40)

    def test_unit_headline_mapped(self):
        report = build_portfolio_report(run_units_from_batch(_batch()))
        row = report.units[0]
        assert row.win_rate == 0.6
        assert row.profit_factor == 2.5
        assert row.net_profit == 60.0                  # total_profit - total_loss

    def test_skips_scenarios_without_stats(self):
        bad = ProcessResult(
            success=False, scenario_name='bad', scenario_index=2, tick_loop_results=None)
        report = build_portfolio_report(run_units_from_batch(_batch(extra_results=[bad])))
        assert [u.name for u in report.units] == ['s1', 's2']


class TestBatchExtraction:
    """The shared real batch also covers the RunUnit record extraction."""

    def test_order_records_flatten(self):
        units = run_units_from_batch(_batch())
        records = [o for u in units for o in u.order_history]
        assert [r.order_id for r in records] == ['o1', 'o2', 'o3']

    def test_trade_records_flatten_empty_when_no_trades(self):
        # tick_loop_results carry no trade_history in this fixture → empty, no crash
        units = run_units_from_batch(_batch())
        assert [t for u in units for t in u.trade_history] == []


class TestSession:
    """live: 1 unit = its own currency aggregate."""

    def test_single_unit_and_aggregate(self):
        result = AutoTraderResult(portfolio_stats=_stats(currency='USD'))
        report = build_portfolio_report(run_units_from_session(result, 'my_profile', 'BTCUSD'))
        assert len(report.units) == 1
        assert report.units[0].name == 'my_profile'
        assert report.units[0].symbol == 'BTCUSD'
        assert len(report.aggregates) == 1
        assert report.aggregates[0].unit_count == 1
        assert report.aggregates[0].net_profit == 60.0

    def test_empty_when_no_stats(self):
        report = build_portfolio_report(
            run_units_from_session(AutoTraderResult(portfolio_stats=None), 'p', 'BTCUSD'))
        assert report.units == []
        assert report.aggregates == []
