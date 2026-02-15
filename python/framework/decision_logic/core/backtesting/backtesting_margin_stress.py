"""
FiniexTestingIDE - Backtesting Margin Stress Decision Logic
Decision logic for margin validation testing (Margin Validation Issue)

Responsibilities:
1. Execute deterministic trade sequence that exhausts margin
2. Track rejections (margin and lot validation)
3. Close positions to recover margin
4. Retry previously rejected orders
5. Embed edge-case operations (invalid lots, non-existent close)
6. Expose all validation data via get_statistics() → BacktestingMetadata

This decision logic is designed for TESTING, not production trading.
It validates that the framework correctly handles:
- Margin exhaustion and INSUFFICIENT_MARGIN rejection
- Margin recovery after closing a position
- Lot size validation (below min, above max, misaligned step)
- Close of non-existent position
- Execution statistics accuracy (sent, executed, rejected)

Configuration Example:
{
    "trade_sequence": [
        {
            "tick_number": 100,
            "direction": "LONG",
            "lot_size": 1.0,
            "hold_ticks": 5000,
            "_comment": "Position #0: First position, consumes margin"
        },
        {
            "tick_number": 200,
            "direction": "LONG",
            "lot_size": 1.0,
            "hold_ticks": 4000,
            "_comment": "Position #1: Second position, consumes more margin"
        },
        {
            "tick_number": 300,
            "direction": "LONG",
            "lot_size": 1.0,
            "expect_rejection": true,
            "_comment": "Position #2: Should be rejected - margin exhausted"
        }
    ],
    "close_events": [
        {
            "tick_number": 6000,
            "sequence_index": 1,
            "_comment": "Close position #1 to free margin"
        }
    ],
    "retry_events": [
        {
            "tick_number": 6100,
            "direction": "LONG",
            "lot_size": 1.0,
            "hold_ticks": 2000,
            "_comment": "Retry after margin recovery - should succeed"
        }
    ],
    "edge_case_orders": [
        {
            "tick_number": 500,
            "type": "invalid_lot_below_min",
            "lot_size": 0.001,
            "_comment": "Below volume_min (0.01) → INVALID_LOT_SIZE"
        },
        {
            "tick_number": 600,
            "type": "invalid_lot_above_max",
            "lot_size": 200.0,
            "_comment": "Above volume_max (100) → INVALID_LOT_SIZE"
        },
        {
            "tick_number": 700,
            "type": "close_nonexistent",
            "position_id": "FAKE_POS_999",
            "_comment": "Close non-existent → BROKER_ERROR"
        }
    ],
    "lot_size": 1.0
}

Data Flow:
1. Worker provides warmup_status in metadata (first tick)
2. Decision opens positions at configured tick_numbers
3. Some positions get rejected (margin exhaustion, lot validation)
4. Close events free margin for recovery tests
5. Retry events verify margin recovery
6. Edge case orders test validation paths
7. get_statistics() returns BacktestingMetadata with all tracking data
"""

from typing import Any, Dict, List, Optional, Set

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.types.decision_logic_types import Decision, DecisionLogicAction
from python.framework.types.market_data_types import TickData
from python.framework.types.market_types import TradingContext
from python.framework.types.parameter_types import ParameterDef
from python.framework.types.worker_types import WorkerResult
from python.framework.types.order_types import OrderResult, OrderType, OrderDirection
from python.framework.types.performance_stats_types import DecisionLogicStats
from python.framework.types.backtesting_metadata_types import BacktestingMetadata


class BacktestingMarginStress(AbstractDecisionLogic):
    """
    Margin stress decision logic for validation testing.

    Executes a trade sequence designed to exhaust margin, trigger
    rejections, test recovery, and validate edge cases.

    Configuration:
        trade_sequence: List of trade specifications (some expect rejection)
            - tick_number: When to open trade
            - direction: "LONG" or "SHORT"
            - lot_size: Position size
            - hold_ticks: How many ticks to hold (optional, for auto-close)
            - expect_rejection: If true, order is expected to be rejected
        close_events: Explicit close commands by sequence_index
            - tick_number: When to close
            - sequence_index: Which trade to close
        retry_events: Orders to retry after margin recovery
            - tick_number: When to retry
            - direction: "LONG" or "SHORT"
            - lot_size: Position size
            - hold_ticks: How many ticks to hold
        edge_case_orders: Invalid operations for rejection testing
            - tick_number: When to attempt
            - type: "invalid_lot_below_min", "invalid_lot_above_max",
                    "invalid_lot_step", "close_nonexistent"
            - lot_size: For lot validation tests
            - position_id: For close_nonexistent tests
        lot_size: Default lot size
    """

    def __init__(
        self,
        name,
        logger: ScenarioLogger,
        config,
        trading_context: TradingContext = None
    ):
        super().__init__(name, logger, config, trading_context=trading_context)

        # Trade sequence configuration
        self.trade_sequence = self.params.get('trade_sequence')
        self.close_events = self.params.get('close_events')
        self.retry_events = self.params.get('retry_events')
        self.edge_case_orders = self.params.get('edge_case_orders')
        self.default_lot_size = self.params.get('lot_size')

        # Internal state
        self.tick_count = 0

        # ============================================
        # Position Tracking
        # ============================================

        # order_id → {sequence_index, direction, lot_size, open_tick, close_tick}
        self._active_trades: Dict[str, Dict[str, Any]] = {}

        # sequence_index → order_id
        self._position_map: Dict[int, str] = {}

        # Prevent duplicate opens/closes/retries/edge_cases
        self._opened_sequences: Set[int] = set()
        self._executed_closes: Set[int] = set()
        self._executed_retries: Set[int] = set()
        self._executed_edge_cases: Set[int] = set()

        # ============================================
        # Rejection & Event Tracking
        # ============================================
        self._rejection_events: List[Dict[str, Any]] = []
        self._close_events_log: List[Dict[str, Any]] = []
        self._retry_results: List[Dict[str, Any]] = []
        self._edge_case_results: List[Dict[str, Any]] = []

        # ============================================
        # Backtesting Tracking (same pattern as BacktestingMultiPosition)
        # ============================================
        self.warmup_errors: List[str] = []
        self.bar_snapshots: Dict[str, Dict[str, Any]] = {}
        self.expected_trades: List[Dict[str, Any]] = []
        self.warmup_checked = False

        self.logger.info(
            f"BacktestingMarginStress initialized: "
            f"{len(self.trade_sequence)} trades, "
            f"{len(self.close_events)} close events, "
            f"{len(self.retry_events)} retry events, "
            f"{len(self.edge_case_orders)} edge cases, "
            f"lot_size={self.default_lot_size}"
        )

    # ============================================
    # Class Methods (Factory Interface)
    # ============================================

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, ParameterDef]:
        return {
            'trade_sequence': ParameterDef(
                param_type=list,
                default=[],
                description="List of trade specs with optional expect_rejection flag"
            ),
            'close_events': ParameterDef(
                param_type=list,
                default=[],
                description="List of explicit close commands by sequence_index"
            ),
            'retry_events': ParameterDef(
                param_type=list,
                default=[],
                description="List of retry orders after margin recovery"
            ),
            'edge_case_orders': ParameterDef(
                param_type=list,
                default=[],
                description="List of edge-case orders for rejection testing"
            ),
            'lot_size': ParameterDef(
                param_type=float,
                default=1.0,
                min_val=0.001,
                max_val=200.0,
                description="Default lot size"
            ),
        }

    @classmethod
    def get_required_order_types(cls, decision_logic_config: Dict[str, Any]) -> List[OrderType]:
        return [OrderType.MARKET]

    def get_required_worker_instances(self) -> Dict[str, str]:
        return {
            "backtesting_worker": "CORE/backtesting/backtesting_sample_worker"
        }

    # ============================================
    # Core Logic: compute() + execute()
    # ============================================

    def compute(
        self,
        tick: TickData,
        worker_results: Dict[str, WorkerResult],
    ) -> Decision:
        self.tick_count += 1

        # Extract Worker Data (First Tick Only)
        if not self.warmup_checked:
            self._extract_worker_data(worker_results)
            self.warmup_checked = True

        # Check Trade Sequence for New Opens
        for idx, spec in enumerate(self.trade_sequence):
            if self.tick_count == spec['tick_number'] and idx not in self._opened_sequences:
                self._opened_sequences.add(idx)

                direction = spec['direction']
                action = (
                    DecisionLogicAction.BUY
                    if direction == 'LONG'
                    else DecisionLogicAction.SELL
                )
                lot_size = spec.get('lot_size', self.default_lot_size)
                hold_ticks = spec.get('hold_ticks')
                expect_rejection = spec.get('expect_rejection', False)

                self.logger.info(
                    f"{'[EXPECT REJECT] ' if expect_rejection else ''}"
                    f"Margin stress signal at tick {self.tick_count}: "
                    f"{direction} {lot_size} lots "
                    f"(trade #{idx}, {len(self._active_trades)} active)"
                )

                return Decision(
                    action=action,
                    confidence=1.0,
                    reason=f"Margin stress open {direction} at tick {self.tick_count}",
                    price=tick.mid,
                    timestamp=tick.timestamp.isoformat(),
                    metadata={
                        'lot_size': lot_size,
                        'sequence_index': idx,
                        'hold_ticks': hold_ticks,
                        'expect_rejection': expect_rejection,
                        'event_type': 'trade_sequence',
                    }
                )

        # Check Retry Events
        for idx, spec in enumerate(self.retry_events):
            if self.tick_count == spec['tick_number'] and idx not in self._executed_retries:
                self._executed_retries.add(idx)

                direction = spec['direction']
                action = (
                    DecisionLogicAction.BUY
                    if direction == 'LONG'
                    else DecisionLogicAction.SELL
                )
                lot_size = spec.get('lot_size', self.default_lot_size)
                hold_ticks = spec.get('hold_ticks', 2000)

                self.logger.info(
                    f"Margin recovery retry at tick {self.tick_count}: "
                    f"{direction} {lot_size} lots (retry #{idx})"
                )

                return Decision(
                    action=action,
                    confidence=1.0,
                    reason=f"Margin recovery retry at tick {self.tick_count}",
                    price=tick.mid,
                    timestamp=tick.timestamp.isoformat(),
                    metadata={
                        'lot_size': lot_size,
                        'hold_ticks': hold_ticks,
                        'expect_rejection': False,
                        'event_type': 'retry',
                        'retry_index': idx,
                    }
                )

        # Check Edge Case Orders
        for idx, spec in enumerate(self.edge_case_orders):
            if self.tick_count == spec['tick_number'] and idx not in self._executed_edge_cases:
                self._executed_edge_cases.add(idx)

                edge_type = spec['type']

                if edge_type == 'close_nonexistent':
                    # Special handling: close a fake position
                    return Decision(
                        action=DecisionLogicAction.FLAT,
                        confidence=0.0,
                        reason=f"Edge case: close_nonexistent at tick {self.tick_count}",
                        price=tick.mid,
                        timestamp=tick.timestamp.isoformat(),
                        metadata={
                            'event_type': 'edge_case',
                            'edge_type': edge_type,
                            'position_id': spec.get('position_id', 'FAKE_POS_999'),
                            'edge_index': idx,
                        }
                    )
                else:
                    # Lot validation edge cases: send order with invalid lots
                    lot_size = spec.get('lot_size', 0.001)
                    direction = spec.get('direction', 'LONG')
                    action = (
                        DecisionLogicAction.BUY
                        if direction == 'LONG'
                        else DecisionLogicAction.SELL
                    )

                    self.logger.info(
                        f"Edge case order at tick {self.tick_count}: "
                        f"{edge_type} lots={lot_size}"
                    )

                    return Decision(
                        action=action,
                        confidence=1.0,
                        reason=f"Edge case: {edge_type} at tick {self.tick_count}",
                        price=tick.mid,
                        timestamp=tick.timestamp.isoformat(),
                        metadata={
                            'lot_size': lot_size,
                            'event_type': 'edge_case',
                            'edge_type': edge_type,
                            'edge_index': idx,
                            'expect_rejection': True,
                        }
                    )

        # No signal
        return Decision(
            action=DecisionLogicAction.FLAT,
            confidence=0.0,
            reason="No signal",
            price=tick.mid,
            timestamp=tick.timestamp.isoformat()
        )

    def _execute_decision_impl(
        self,
        decision: Decision,
        tick: TickData
    ) -> Optional[OrderResult]:
        if not self.trading_api:
            self.logger.warning("No trading_api available - skipping execution")
            return None

        # ============================================
        # STEP 1: Close Expired Positions (hold_ticks)
        # ============================================
        self._close_expired_positions()

        # ============================================
        # STEP 2: Process Explicit Close Events
        # ============================================
        self._process_close_events()

        # ============================================
        # STEP 3: Process Edge Case (close_nonexistent)
        # ============================================
        event_type = decision.metadata.get('event_type') if decision.metadata else None

        if event_type == 'edge_case' and decision.metadata.get('edge_type') == 'close_nonexistent':
            position_id = decision.metadata['position_id']
            self.logger.info(
                f"Edge case: closing non-existent position '{position_id}' "
                f"at tick {self.tick_count}"
            )

            result = self.trading_api.close_position(position_id)

            self._edge_case_results.append({
                'tick': self.tick_count,
                'type': 'close_nonexistent',
                'position_id': position_id,
                'rejected': result.is_rejected if result else True,
                'rejection_reason': (
                    result.rejection_reason.value if result and result.rejection_reason else None
                ),
                'edge_index': decision.metadata.get('edge_index'),
            })

            return result

        # ============================================
        # STEP 4: Open New Position (if signaled)
        # ============================================
        order_result = None

        if decision.action in (DecisionLogicAction.BUY, DecisionLogicAction.SELL):
            direction = (
                OrderDirection.LONG
                if decision.action == DecisionLogicAction.BUY
                else OrderDirection.SHORT
            )
            lot_size = decision.metadata.get('lot_size', self.default_lot_size)
            seq_idx = decision.metadata.get('sequence_index')
            hold_ticks = decision.metadata.get('hold_ticks')
            expect_rejection = decision.metadata.get('expect_rejection', False)

            order_result = self.trading_api.send_order(
                symbol=tick.symbol,
                order_type=OrderType.MARKET,
                direction=direction,
                lots=lot_size,
                comment=f"MarginStress #{seq_idx if seq_idx is not None else 'retry'} "
                        f"{'[EXPECT_REJECT]' if expect_rejection else ''}"
            )

            if order_result and not order_result.is_rejected:
                # Track active trade
                trade_info = {
                    'sequence_index': seq_idx,
                    'direction': 'LONG' if direction == OrderDirection.LONG else 'SHORT',
                    'lot_size': lot_size,
                    'open_tick': self.tick_count,
                }
                if hold_ticks is not None:
                    trade_info['close_tick'] = self.tick_count + hold_ticks

                self._active_trades[order_result.order_id] = trade_info

                if seq_idx is not None:
                    self._position_map[seq_idx] = order_result.order_id

                # Record expected trade
                trade_record = {
                    'signal_tick': self.tick_count,
                    'direction': 'LONG' if direction == OrderDirection.LONG else 'SHORT',
                    'lot_size': lot_size,
                    'order_id': order_result.order_id,
                    'event_type': event_type,
                }
                if seq_idx is not None:
                    trade_record['sequence_index'] = seq_idx
                if hold_ticks is not None:
                    trade_record['hold_ticks'] = hold_ticks

                self.expected_trades.append(trade_record)

                if event_type == 'retry':
                    self._retry_results.append({
                        'tick': self.tick_count,
                        'retry_index': decision.metadata.get('retry_index'),
                        'success': True,
                        'order_id': order_result.order_id,
                    })

                if expect_rejection:
                    self.logger.warning(
                        f"Expected rejection but order succeeded at tick {self.tick_count}"
                    )

            elif order_result and order_result.is_rejected:
                self._rejection_events.append({
                    'tick': self.tick_count,
                    'sequence_index': seq_idx,
                    'direction': 'LONG' if direction == OrderDirection.LONG else 'SHORT',
                    'lot_size': lot_size,
                    'reason': order_result.rejection_reason.value if order_result.rejection_reason else None,
                    'message': order_result.rejection_message,
                    'expected': expect_rejection,
                    'event_type': event_type,
                })

                if event_type == 'retry':
                    self._retry_results.append({
                        'tick': self.tick_count,
                        'retry_index': decision.metadata.get('retry_index'),
                        'success': False,
                        'reason': order_result.rejection_reason.value if order_result.rejection_reason else None,
                    })

                if event_type == 'edge_case':
                    self._edge_case_results.append({
                        'tick': self.tick_count,
                        'type': decision.metadata.get('edge_type'),
                        'lot_size': lot_size,
                        'rejected': True,
                        'rejection_reason': order_result.rejection_reason.value if order_result.rejection_reason else None,
                        'edge_index': decision.metadata.get('edge_index'),
                    })

                self.logger.info(
                    f"{'[EXPECTED] ' if expect_rejection else '[UNEXPECTED] '}"
                    f"Order rejected at tick {self.tick_count}: "
                    f"{order_result.rejection_message}"
                )

        return order_result

    def _close_expired_positions(self) -> None:
        """Close positions whose hold_ticks have expired."""
        to_close: List[str] = []

        for order_id, info in self._active_trades.items():
            close_tick = info.get('close_tick')
            if close_tick is not None and self.tick_count >= close_tick:
                to_close.append(order_id)

        for order_id in to_close:
            seq_idx = self._active_trades[order_id].get('sequence_index')
            positions = self.trading_api.get_open_positions()
            closed = False

            for pos in positions:
                if pos.position_id == order_id:
                    if self.trading_api.is_pending_close(pos.position_id):
                        break
                    self.trading_api.close_position(pos.position_id)
                    self._close_events_log.append({
                        'position_id': order_id,
                        'close_tick': self.tick_count,
                        'sequence_index': seq_idx,
                        'trigger': 'hold_ticks_expired',
                    })
                    closed = True
                    break

            if not closed:
                self.logger.warning(
                    f"Could not close {order_id} at tick {self.tick_count} "
                    f"(trade #{seq_idx} - position not found or pending close)"
                )

            del self._active_trades[order_id]

    def _process_close_events(self) -> None:
        """Process explicit close events from config."""
        for idx, event in enumerate(self.close_events):
            if self.tick_count == event['tick_number'] and idx not in self._executed_closes:
                self._executed_closes.add(idx)

                seq_idx = event['sequence_index']
                order_id = self._position_map.get(seq_idx)

                if not order_id:
                    self.logger.warning(
                        f"Close event at tick {self.tick_count}: "
                        f"no position for sequence #{seq_idx}"
                    )
                    continue

                positions = self.trading_api.get_open_positions()
                closed = False

                for pos in positions:
                    if pos.position_id == order_id:
                        if self.trading_api.is_pending_close(pos.position_id):
                            break
                        self.trading_api.close_position(pos.position_id)
                        self._close_events_log.append({
                            'position_id': order_id,
                            'close_tick': self.tick_count,
                            'sequence_index': seq_idx,
                            'trigger': 'explicit_close_event',
                        })

                        self.logger.info(
                            f"Explicit close at tick {self.tick_count}: "
                            f"{order_id} (trade #{seq_idx})"
                        )
                        closed = True
                        break

                if closed and order_id in self._active_trades:
                    del self._active_trades[order_id]

    # ============================================
    # Worker Data Extraction
    # ============================================

    def _extract_worker_data(self, worker_results: Dict[str, WorkerResult]) -> None:
        worker_result = worker_results.get('backtesting_worker')

        if not worker_result:
            self.warmup_errors.append("Worker result not found")
            return

        warmup_status = worker_result.metadata.get('warmup_status', {})

        for timeframe, status in warmup_status.items():
            if not status.get('valid', True):
                error_msg = f"{timeframe}: {status.get('error', 'Unknown error')}"
                self.warmup_errors.append(error_msg)

        self.bar_snapshots = worker_result.metadata.get('bar_snapshots', {})

    # ============================================
    # Statistics Override
    # ============================================

    def get_statistics(self) -> DecisionLogicStats:
        base_stats = super().get_statistics()

        base_stats.backtesting_metadata = BacktestingMetadata(
            warmup_errors=self.warmup_errors,
            bar_snapshots=self.bar_snapshots,
            expected_trades=self.expected_trades,
            tick_count=self.tick_count
        )

        self.logger.debug(
            f"MarginStress Metadata: "
            f"expected_trades={len(self.expected_trades)}, "
            f"rejections={len(self._rejection_events)}, "
            f"close_events={len(self._close_events_log)}, "
            f"retries={len(self._retry_results)}, "
            f"edge_cases={len(self._edge_case_results)}, "
            f"ticks={self.tick_count}"
        )

        return base_stats

    # ============================================
    # Public Access for Test Assertions
    # ============================================

    @property
    def rejection_events(self) -> List[Dict[str, Any]]:
        """All recorded rejection events."""
        return self._rejection_events

    @property
    def close_events_log(self) -> List[Dict[str, Any]]:
        """All recorded close events."""
        return self._close_events_log

    @property
    def retry_results(self) -> List[Dict[str, Any]]:
        """All recorded retry results."""
        return self._retry_results

    @property
    def edge_case_results(self) -> List[Dict[str, Any]]:
        """All recorded edge case results."""
        return self._edge_case_results

    @property
    def position_map(self) -> Dict[int, str]:
        """Sequence index → order_id mapping."""
        return self._position_map
