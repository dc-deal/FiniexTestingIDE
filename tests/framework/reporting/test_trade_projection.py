"""
Trade-History Full-Projection Tests (#393).

The B projection lets the console render the audit table purely from the model: each
row carries its unit (`scenario_name`), the audit columns, and the #330 per-fill
executions. Tested with real TradeRecord / BrokerTrade / ProcessResult fixtures (no
run required), built into RunUnits — the units must tag the unit name correctly.
"""

from datetime import datetime, timezone

from python.framework.reporting.builders.run_unit import (
    run_units_from_batch, run_units_from_session)
from python.framework.reporting.builders.trade_history_report_builder import build_trade_history_report
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.portfolio_types.portfolio_trade_record_types import (
    CloseReason, CloseType, EntryType, TradeRecord)
from python.framework.types.process_data_types import ProcessResult, ProcessTickLoopResult
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.trading_env_types.broker_trade_types import BrokerTrade
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderSide


_T0 = datetime(2025, 10, 13, 8, 0, 0, tzinfo=timezone.utc)


def _bt(trade_id: str, side: OrderSide, is_maker: bool = False) -> BrokerTrade:
    return BrokerTrade(
        trade_id=trade_id, parent_broker_ref='ref', order_id='o1',
        volume=0.1, price=1.1000, fee=1.4, fee_currency='USD',
        timestamp=_T0, side=side, is_maker=is_maker)


def _trade(position_id: str = 'p1', with_executions: bool = True) -> TradeRecord:
    return TradeRecord(
        position_id=position_id, symbol='EURUSD', direction=OrderDirection.LONG, lots=0.1,
        close_type=CloseType.FULL,
        entry_price=1.1000, entry_time=_T0,
        entry_tick_value=1.0, entry_bid=1.0999, entry_ask=1.1001,
        exit_price=1.1020, exit_time=_T0, exit_tick_value=1.0,
        entry_tick_index=42, exit_tick_index=99,
        digits=5, contract_size=100000,
        spread_cost=0.5, commission_cost=0.3, swap_cost=0.0, total_fees=0.8,
        gross_pnl=10.8, net_pnl=10.0,
        stop_loss=1.0980, take_profit=1.1040,
        close_reason=CloseReason.TP_TRIGGERED, entry_type=EntryType.LIMIT,
        entry_side=OrderSide.BUY, exit_side=OrderSide.SELL,
        entry_trades=[_bt('e1', OrderSide.BUY)] if with_executions else [],
        exit_trades=[_bt('x1', OrderSide.SELL)] if with_executions else [],
    )


def _session_report(result: AutoTraderResult, name: str):
    """Build the trade report from a single live-session unit."""
    return build_trade_history_report(run_units_from_session(result, name, ''))


class TestProjection:
    """The B audit columns + #330 executions map onto the row."""

    def test_audit_columns(self):
        row = _session_report(AutoTraderResult(trade_history=[_trade()]), 's').trades[0]
        assert row.entry_type == 'limit'
        assert row.stop_loss == 1.0980 and row.take_profit == 1.1040
        assert row.entry_side == 'buy' and row.exit_side == 'sell'
        assert row.entry_tick_index == 42 and row.exit_tick_index == 99

    def test_executions_mapped(self):
        row = _session_report(AutoTraderResult(trade_history=[_trade()]), 's').trades[0]
        assert len(row.entry_executions) == 1 and len(row.exit_executions) == 1
        ex = row.entry_executions[0]
        assert ex.trade_id == 'e1' and ex.side == 'buy'
        assert ex.liquidity == 'taker'              # is_maker=False
        assert ex.volume == 0.1 and ex.price == 1.1000

    def test_no_executions(self):
        row = _session_report(
            AutoTraderResult(trade_history=[_trade(with_executions=False)]), 's').trades[0]
        assert row.entry_executions == [] and row.exit_executions == []


class TestSourceVariants:
    """The unit name (scenario_name) is tagged per source."""

    def test_from_batch_tags_scenario(self):
        batch = BatchExecutionSummary(
            batch_execution_time=0.0, batch_warmup_time=0.0, batch_tickrun_time=0.0,
            process_result_list=[
                ProcessResult(
                    success=True, scenario_name='AUDUSD_cont_00', scenario_index=0,
                    tick_loop_results=ProcessTickLoopResult(trade_history=[_trade('p1')])),
                ProcessResult(
                    success=True, scenario_name='EURUSD_cont_01', scenario_index=1,
                    tick_loop_results=ProcessTickLoopResult(trade_history=[_trade('p2')])),
            ],
            single_scenario_list=[
                SingleScenario(
                    name='AUDUSD_cont_00', scenario_index=0, symbol='AUDUSD',
                    data_broker_type='mt5', start_date=_T0),
                SingleScenario(
                    name='EURUSD_cont_01', scenario_index=1, symbol='EURUSD',
                    data_broker_type='mt5', start_date=_T0),
            ])
        report = build_trade_history_report(run_units_from_batch(batch))
        assert [r.scenario_name for r in report.trades] == ['AUDUSD_cont_00', 'EURUSD_cont_01']

    def test_from_session_tags_name(self):
        report = _session_report(AutoTraderResult(trade_history=[_trade()]), 'my_profile')
        assert report.trades[0].scenario_name == 'my_profile'
