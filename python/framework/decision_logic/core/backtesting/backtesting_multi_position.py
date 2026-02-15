"""
FiniexTestingIDE - Backtesting Multi-Position Decision Logic
Decision logic for multi-position validation testing (#114)

Responsibilities:
1. Execute overlapping deterministic trade sequence
2. Track multiple simultaneous positions
3. Close positions selectively by position_id (not blanket FLAT)
4. Expose all validation data via get_statistics() → BacktestingMetadata

This decision logic is designed for TESTING, not production trading.
It validates that the framework correctly handles:
- Multiple simultaneous positions on the same symbol
- Hedging (LONG + SHORT simultaneously)
- Per-position P&L isolation
- Selective close (close one position, others unchanged)
- Portfolio aggregation correctness (sum of parts = total)

Unlike BacktestingDeterministic:
- Opens positions even when others are already open
- Tracks multiple active_trades simultaneously (Dict, not singular)
- Closes selectively by position_id when hold_ticks expires
- FLAT means "no new position" — NOT "close everything"

Configuration Example:
{
    "trade_sequence": [
        {
            "tick_number": 100,
            "direction": "LONG",
            "hold_ticks": 8000,
            "lot_size": 0.01
        },
        {
            "tick_number": 2000,
            "direction": "LONG",
            "hold_ticks": 5500,
            "lot_size": 0.02
        },
        {
            "tick_number": 3000,
            "direction": "SHORT",
            "hold_ticks": 4000,
            "lot_size": 0.01
        }
    ],
    "lot_size": 0.1
}

Data Flow:
1. Worker provides warmup_status in metadata (first tick)
2. Decision opens positions at configured tick_numbers
3. Decision closes positions selectively when hold_ticks expires
4. get_statistics() returns BacktestingMetadata with all tracking data

Position Lifecycle (example with 3 overlapping trades):
    Tick 100:  Open LONG #0      → 1 open
    Tick 2000: Open LONG #1      → 2 open (stacking)
    Tick 3000: Open SHORT #2     → 3 open (hedging)
    Tick 7000: Close SHORT #2    → 2 open
    Tick 7500: Close LONG #1     → 1 open
    Tick 8100: Close LONG #0     → 0 open
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


class BacktestingMultiPosition(AbstractDecisionLogic):
    """
    Multi-position decision logic for validation testing.

    Executes overlapping trades at predetermined ticks to validate
    that the framework correctly manages multiple simultaneous positions.

    Key differences from BacktestingDeterministic:
    - No `len(open_positions) == 0` guard — opens regardless
    - Tracks Dict[order_id, trade_info] instead of singular active_trade
    - Closes by position_id, not blanket FLAT
    - FLAT signal = "no new position this tick" (not "close all")

    Configuration:
        trade_sequence: List of trade specifications
            - tick_number: When to open trade
            - direction: "LONG" or "SHORT"
            - hold_ticks: How many ticks to hold before closing
            - lot_size: Position size
        lot_size: Default lot size for trades without explicit lot_size
    """

    def __init__(
        self,
        name,
        logger: ScenarioLogger,
        config,
        trading_context: TradingContext = None
    ):
        """
        Initialize BacktestingMultiPosition logic.

        Args:
            name: Logic identifier
            logger: ScenarioLogger instance
            config: Configuration dict with trade_sequence
            trading_context: TradingContext (optional)
        """
        super().__init__(name, logger, config, trading_context=trading_context)

        # Trade sequence configuration
        self.trade_sequence = self.params.get('trade_sequence')
        self.default_lot_size = self.params.get('lot_size')

        # Internal state
        self.tick_count = 0

        # ============================================
        # Multi-Position Tracking
        # ============================================

        # order_id → {close_tick, sequence_index, direction, lot_size, open_tick}
        self._active_trades: Dict[str, Dict[str, Any]] = {}

        # sequence_index → order_id (for test validation)
        self._position_map: Dict[int, str] = {}

        # Prevent duplicate opens for same sequence entry
        self._opened_sequences: Set[int] = set()

        # Recorded close events for validation
        self._close_events: List[Dict[str, Any]] = []

        # Peak concurrent position count
        self._max_concurrent: int = 0

        # ============================================
        # Backtesting Tracking (same pattern as BacktestingDeterministic)
        # ============================================
        self.warmup_errors: List[str] = []
        self.bar_snapshots: Dict[str, Dict[str, Any]] = {}
        self.expected_trades: List[Dict[str, Any]] = []
        self.warmup_checked = False

        self.logger.info(
            f"BacktestingMultiPosition initialized: "
            f"{len(self.trade_sequence)} trades in sequence, "
            f"lot_size={self.default_lot_size}"
        )

    # ============================================
    # Class Methods (Factory Interface)
    # ============================================

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, ParameterDef]:
        """Backtesting multi-position decision logic parameters."""
        return {
            'trade_sequence': ParameterDef(
                param_type=list,
                default=[],
                description="List of trade specs: tick_number, direction, hold_ticks, lot_size"
            ),
            'lot_size': ParameterDef(
                param_type=float,
                default=0.1,
                min_val=0.01,
                max_val=100.0,
                description="Default lot size for trades without explicit lot_size"
            ),
        }

    @classmethod
    def get_required_order_types(cls, decision_logic_config: Dict[str, Any]) -> List[OrderType]:
        """
        Declare required order types.

        BacktestingMultiPosition uses only Market orders.

        Returns:
            List containing OrderType.MARKET
        """
        return [OrderType.MARKET]

    def get_required_worker_instances(self) -> Dict[str, str]:
        """
        Declare required worker instance.

        Requires BacktestingSampleWorker for warmup validation.
        Worker is reused from BacktestingDeterministic — no new worker needed.

        Returns:
            Dict with worker instance mapping
        """
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
        """
        Generate trading decision based on tick count.

        Unlike BacktestingDeterministic:
        - Signals OPEN only ONCE per sequence entry (exact tick match)
        - FLAT means "no new position" — closes are handled in execute
        - No dependency on current open position count

        Args:
            tick: Current tick data
            worker_results: Results from BacktestingSampleWorker

        Returns:
            Decision: BUY/SELL for new position, FLAT otherwise
        """
        self.tick_count += 1

        # ============================================
        # Extract Worker Data (First Tick Only)
        # ============================================
        if not self.warmup_checked:
            self._extract_worker_data(worker_results)
            self.warmup_checked = True

        # ============================================
        # Check Trade Sequence for New Opens
        # ============================================
        for idx, spec in enumerate(self.trade_sequence):
            if self.tick_count == spec['tick_number'] and idx not in self._opened_sequences:
                # Mark as opened to prevent duplicate signals
                self._opened_sequences.add(idx)

                direction = spec['direction']
                action = (
                    DecisionLogicAction.BUY
                    if direction == 'LONG'
                    else DecisionLogicAction.SELL
                )
                lot_size = spec.get('lot_size', self.default_lot_size)
                hold_ticks = spec.get('hold_ticks', 100)

                self.logger.info(
                    f"🎯 Multi-position signal at tick {self.tick_count}: "
                    f"{direction} {lot_size} lots, hold {hold_ticks} ticks "
                    f"(trade #{idx}, {len(self._active_trades)} already active)"
                )

                return Decision(
                    action=action,
                    confidence=1.0,
                    reason=f"Multi-position open {direction} at tick {self.tick_count}",
                    price=tick.mid,
                    timestamp=tick.timestamp.isoformat(),
                    metadata={
                        'lot_size': lot_size,
                        'sequence_index': idx,
                        'hold_ticks': hold_ticks,
                    }
                )

        # ============================================
        # No Open Signal (FLAT)
        # ============================================
        return Decision(
            action=DecisionLogicAction.FLAT,
            confidence=0.0,
            reason="No open signal",
            price=tick.mid,
            timestamp=tick.timestamp.isoformat()
        )

    def _execute_decision_impl(
        self,
        decision: Decision,
        tick: TickData
    ) -> Optional[OrderResult]:
        """
        Execute multi-position trading logic.

        Called every tick. Handles both closes and opens:
        1. Close expired positions (selective, by position_id)
        2. Open new position if BUY/SELL signal

        This is the key difference from BacktestingDeterministic:
        - Closes happen independently of the decision signal
        - Opens happen without checking existing position count
        - Each position is tracked and closed individually

        Args:
            decision: Decision from compute()
            tick: Current tick data

        Returns:
            OrderResult if new order was sent, None otherwise
        """
        if not self.trading_api:
            self.logger.warning(
                "No trading_api available - skipping execution")
            return None

        # ============================================
        # STEP 1: Close Expired Positions
        # ============================================
        self._close_expired_positions()

        # ============================================
        # STEP 2: Open New Position (if signaled)
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
            hold_ticks = decision.metadata.get('hold_ticks', 100)

            order_result = self.trading_api.send_order(
                symbol=tick.symbol,
                order_type=OrderType.MARKET,
                direction=direction,
                lots=lot_size,
                comment=f"MultiPos #{seq_idx} {direction.value}"
            )

            if order_result and not order_result.is_rejected:
                # Track active trade for selective close
                self._active_trades[order_result.order_id] = {
                    'close_tick': self.tick_count + hold_ticks,
                    'sequence_index': seq_idx,
                    'direction': 'LONG' if direction == OrderDirection.LONG else 'SHORT',
                    'lot_size': lot_size,
                    'open_tick': self.tick_count
                }
                self._position_map[seq_idx] = order_result.order_id

                # Record expected trade for test validation
                self.expected_trades.append({
                    'signal_tick': self.tick_count,
                    'direction': 'LONG' if direction == OrderDirection.LONG else 'SHORT',
                    'lot_size': lot_size,
                    'hold_ticks': hold_ticks,
                    'sequence_index': seq_idx,
                    'order_id': order_result.order_id
                })

            elif order_result and order_result.is_rejected:
                self.logger.error(
                    f"❌ Multi-position order rejected at tick {self.tick_count}: "
                    f"{order_result.rejection_message}"
                )

        # ============================================
        # STEP 3: Track Peak Concurrent Positions
        # ============================================
        self._max_concurrent = max(
            self._max_concurrent, len(self._active_trades)
        )

        return order_result

    def _close_expired_positions(self) -> None:
        """
        Close positions whose hold_ticks have expired.

        Iterates active trades, finds expired ones, sends selective
        close orders via trading_api. Each position is closed individually
        by its position_id — other positions remain untouched.

        Close orders go through latency simulation (same as opens).
        Position is removed from _active_trades immediately to prevent
        duplicate close submissions.
        """
        to_close: List[str] = []

        # Identify expired positions
        for order_id, info in self._active_trades.items():
            if self.tick_count >= info['close_tick']:
                to_close.append(order_id)

        # Close each expired position
        for order_id in to_close:
            seq_idx = self._active_trades[order_id]['sequence_index']

            # Find position in trading API's view
            positions = self.trading_api.get_open_positions()
            closed = False

            for pos in positions:
                if pos.position_id == order_id:
                    if self.trading_api.is_pending_close(pos.position_id):
                        # Close already in flight — skip
                        break
                    self.trading_api.close_position(pos.position_id)

                    self._close_events.append({
                        'position_id': order_id,
                        'close_tick': self.tick_count,
                        'sequence_index': seq_idx
                    })

                    self.logger.info(
                        f"📍 Multi-position close at tick {self.tick_count}: "
                        f"{order_id} (trade #{seq_idx}, "
                        f"{len(self._active_trades) - 1} remaining)"
                    )
                    closed = True
                    break

            if not closed:
                self.logger.warning(
                    f"⚠️ Could not close {order_id} at tick {self.tick_count} "
                    f"(trade #{seq_idx} - position not found or pending close)"
                )

            # Remove from active trades regardless
            # (prevents duplicate close attempts on next tick)
            del self._active_trades[order_id]

    # ============================================
    # Worker Data Extraction
    # ============================================

    def _extract_worker_data(self, worker_results: Dict[str, WorkerResult]) -> None:
        """
        Extract warmup validation data from BacktestingSampleWorker.

        Same pattern as BacktestingDeterministic — reuses worker
        for warmup validation. Bar snapshots are captured but not
        required for multi-position testing.

        Args:
            worker_results: Results from all workers
        """
        worker_result = worker_results.get('backtesting_worker')

        if not worker_result:
            self.logger.warning(
                "❌ BacktestingSampleWorker result not found - "
                "warmup validation skipped"
            )
            self.warmup_errors.append("Worker result not found")
            return

        # Extract warmup status
        warmup_status = worker_result.metadata.get('warmup_status', {})

        for timeframe, status in warmup_status.items():
            if not status.get('valid', True):
                error_msg = f"{timeframe}: {status.get('error', 'Unknown error')}"
                self.warmup_errors.append(error_msg)
                self.logger.warning(f"❌ Warmup error: {error_msg}")
            else:
                self.logger.debug(
                    f"✅ Warmup valid: {timeframe} = {status['actual']} bars"
                )

        # Store bar snapshots reference (mutable — accumulates over ticks)
        self.bar_snapshots = worker_result.metadata.get('bar_snapshots', {})

    # ============================================
    # Statistics Override
    # ============================================

    def get_statistics(self) -> DecisionLogicStats:
        """
        Get statistics with BacktestingMetadata.

        Overrides parent to include multi-position validation data:
        - warmup_errors
        - bar_snapshots (from worker)
        - expected_trades (with sequence_index and order_id)
        - tick_count
        - position_map, close_events, max_concurrent (in metadata)

        Returns:
            DecisionLogicStats with backtesting_metadata populated
        """
        # Get base stats from parent (signals + timing)
        base_stats = super().get_statistics()

        # Create and attach BacktestingMetadata
        base_stats.backtesting_metadata = BacktestingMetadata(
            warmup_errors=self.warmup_errors,
            bar_snapshots=self.bar_snapshots,
            expected_trades=self.expected_trades,
            tick_count=self.tick_count
        )

        self.logger.debug(
            f"📊 MultiPosition Metadata: "
            f"errors={len(self.warmup_errors)}, "
            f"expected_trades={len(self.expected_trades)}, "
            f"close_events={len(self._close_events)}, "
            f"max_concurrent={self._max_concurrent}, "
            f"ticks={self.tick_count}"
        )

        return base_stats
