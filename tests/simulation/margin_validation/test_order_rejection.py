"""
FiniexTestingIDE - Order Rejection & Edge Case Tests
Validates that invalid operations are rejected cleanly.

Tests:
- Lot size validation (below min, above max)
- Close non-existent position (no crash)
- Rejection tracking in execution statistics
- Rejected orders absent from trade history
"""

from typing import Any, Dict, List

from python.framework.reporting.builders.order_history_report_builder import (
    build_order_history_report,
)
from python.framework.reporting.builders.run_unit import RunUnit
from python.framework.types.process_data_types import ProcessTickLoopResult
from python.framework.types.probe_metadata_types import ProbeMetadata
from python.framework.types.portfolio_types.portfolio_trade_record_types import TradeRecord
from python.framework.types.trading_env_types.order_types import OrderStatus, RejectionReason
from python.framework.types.trading_env_types.trading_env_stats_types import ExecutionStats


class TestLotSizeValidation:
    """Validates that invalid lot sizes are rejected immediately."""

    def test_lot_validation_rejections_counted(
        self,
        execution_stats: ExecutionStats,
        edge_case_orders: list
    ):
        """Lot validation refusals are counted as denials — refused before anything was sent."""
        lot_edge_cases = sum(
            1 for e in edge_case_orders
            if e['type'] in ('invalid_lot_below_min', 'invalid_lot_above_max', 'invalid_lot_step')
        )
        assert execution_stats.orders_denied >= lot_edge_cases, (
            f'Expected at least {lot_edge_cases} denials from lot validation, '
            f'orders_denied={execution_stats.orders_denied}'
        )

    def test_invalid_lots_not_in_trade_history(
        self,
        trade_history: List[TradeRecord],
        edge_case_orders: list
    ):
        """Trades with invalid lot sizes should not appear in trade history."""
        invalid_lots = {
            e['lot_size'] for e in edge_case_orders
            if e['type'] in ('invalid_lot_below_min', 'invalid_lot_above_max')
        }
        for trade in trade_history:
            assert trade.lots not in invalid_lots, (
                f'Trade with lots={trade.lots} should not be in history '
                f'(matches invalid lot size)'
            )

    def test_invalid_lots_not_in_expected_trades(
        self,
        probe_metadata: ProbeMetadata,
        edge_case_orders: list
    ):
        """Expected trades should not contain rejected lot validation orders."""
        invalid_lots = {
            e['lot_size'] for e in edge_case_orders
            if e['type'] in ('invalid_lot_below_min', 'invalid_lot_above_max')
        }
        for trade in probe_metadata.expected_trades:
            assert trade.get('lot_size') not in invalid_lots, (
                f"Expected trade with lots={trade.get('lot_size')} "
                f"should not exist (invalid lot)"
            )

    def test_lot_step_misalignment_rejected(
        self,
        execution_stats: ExecutionStats,
        edge_case_orders: list
    ):
        """Lot not aligned with volume_step should be rejected."""
        step_edge_cases = [
            e for e in edge_case_orders
            if e['type'] == 'invalid_lot_step'
        ]
        assert len(step_edge_cases) > 0, (
            'Config must include at least one invalid_lot_step edge case'
        )
        # Step misalignment refusals are denials — the executor refuses before sending
        assert execution_stats.orders_denied >= len(step_edge_cases), (
            f'Expected at least {len(step_edge_cases)} denials from lot step '
            f'misalignment, orders_denied={execution_stats.orders_denied}'
        )


class TestPositionCloseErrors:
    """Validates that closing non-existent positions doesn't crash."""

    def test_scenario_completes_despite_close_error(
        self,
        probe_metadata: ProbeMetadata,
        scenario_config: Dict[str, Any]
    ):
        """Scenario should complete all ticks despite close errors."""
        expected_ticks = scenario_config['scenarios'][0]['max_ticks']
        assert probe_metadata.tick_count == expected_ticks, (
            f'Expected {expected_ticks} ticks, got {probe_metadata.tick_count}. '
            f'Scenario may have crashed on close error.'
        )

    def test_successful_trades_unaffected_by_close_error(
        self,
        trade_history: List[TradeRecord],
        expected_successful_trades: int
    ):
        """Successful trades should be unaffected by close errors."""
        assert len(trade_history) == expected_successful_trades, (
            f'Expected {expected_successful_trades} trades despite close errors, '
            f'got {len(trade_history)}'
        )


class TestRejectionTracking:
    """Validates that all rejection types are correctly tracked."""

    def test_orders_submitted_includes_rejected(
        self,
        execution_stats: ExecutionStats
    ):
        """A rejection at the fill was submitted first, so submissions outnumber executions."""
        assert execution_stats.orders_submitted > execution_stats.orders_executed, (
            f'orders_submitted ({execution_stats.orders_submitted}) should be > '
            f'orders_executed ({execution_stats.orders_executed}) '
            f'when rejections occur'
        )

    def test_rejected_orders_not_in_trade_history(
        self,
        trade_history: List[TradeRecord],
        tick_loop_results: ProcessTickLoopResult,
    ):
        """Rejected orders should not appear in trade history — no trade is a rejected order's."""
        rejected_ids = {o.order_id for o in tick_loop_results.order_history
                        if o.status is OrderStatus.REJECTED}
        assert rejected_ids, 'the scenario must produce rejections for this test to mean anything'
        traded = [t.position_id for t in trade_history if t.position_id in rejected_ids]
        assert not traded, f'trades recorded for rejected orders: {traded}'

    def test_all_rejections_accounted_for(
        self,
        execution_stats: ExecutionStats,
        expected_rejections: int
    ):
        """Total rejections should match expected count."""
        assert execution_stats.orders_rejected == expected_rejections, (
            f'Expected {expected_rejections} total rejections, '
            f'got {execution_stats.orders_rejected}'
        )


class TestRejectionRecords:
    """A rejection states what was refused and when — over the real executor path."""

    def test_every_rejection_states_its_side_symbol_size_and_time(
        self,
        tick_loop_results: ProcessTickLoopResult,
    ):
        """No rejection leaves its side, symbol, direction, lots or time empty."""
        rejections = [o for o in tick_loop_results.order_history if o.is_refused]
        assert rejections, 'the scenario must produce rejections for this test to mean anything'
        for rejection in rejections:
            assert rejection.action is not None, rejection.order_id
            assert rejection.execution_time is not None, rejection.order_id
            if rejection.rejection_reason is RejectionReason.POSITION_NOT_FOUND:
                # The close of a position this bot does not hold names only the id it was asked
                # for: no symbol, no direction and no size exist to state. It used to leave no row
                # at all, which is why this exemption is new.
                continue
            assert rejection.symbol, rejection.order_id
            assert rejection.direction is not None, rejection.order_id
            assert rejection.requested_lots is not None, rejection.order_id

    def test_the_symbol_filter_keeps_the_rejections(
        self,
        tick_loop_results: ProcessTickLoopResult,
    ):
        """
        Filtering the order history by its symbol drops no rejected row.

        The defect this pins: every rejection carried an empty symbol, so a symbol filter
        removed all of them while the unfiltered list still showed them.
        """
        orders = tick_loop_results.order_history
        symbol = next(o.symbol for o in orders if o.symbol)
        units = [RunUnit(name='scenario', symbol=symbol, order_history=orders)]

        unfiltered = build_order_history_report('run', units)
        filtered = build_order_history_report('run', units, symbol=symbol)

        rejected = sum(1 for row in unfiltered.orders if row.status is OrderStatus.REJECTED)
        assert rejected > 0
        assert sum(1 for row in filtered.orders if row.status is OrderStatus.REJECTED) == rejected

