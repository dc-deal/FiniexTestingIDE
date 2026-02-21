"""
FiniexTestingIDE - Backtesting Deterministic Decision Logic
Decision logic for MVP validation testing with deterministic trade sequences

Responsibilities:
1. Execute deterministic trade sequence based on tick count
2. Extract warmup validation data from BacktestingSampleWorker
3. Track expected trades for validation
4. Expose all data via get_statistics() → BacktestingMetadata

This decision logic is designed for TESTING, not production trading.
It executes trades at predetermined ticks to validate:
- Order execution flow
- Latency simulation determinism
- P&L calculation accuracy

Configuration Example:
{
    "trade_sequence": [
        {
            "tick_number": 10,
            "direction": "LONG",
            "hold_ticks": 300,
            "lot_size": 0.01,
            "stop_loss": 1.2500,
            "take_profit": 1.2800
        },
        {
            "tick_number": 500,
            "direction": "SHORT",
            "order_type": "LIMIT",
            "price": 1.2900,
            "hold_ticks": 200,
            "lot_size": 0.01
        }
    ],
    "modify_sequence": [
        {
            "tick_number": 100,
            "stop_loss": 1.2550,
            "take_profit": 1.2750
        }
    ],
    "modify_limit_sequence": [
        {
            "tick_number": 200,
            "price": 1.2850
        }
    ],
    "lot_size": 0.1
}

Data Flow:
1. Worker provides warmup_status + bar_snapshots in metadata
2. Decision extracts and stores for BacktestingMetadata
3. Trade sequence executes at predetermined ticks
4. get_statistics() returns DecisionLogicStats with backtesting_metadata
"""

from typing import Any, Dict, List, Optional

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


class BacktestingDeterministic(AbstractDecisionLogic):
    """
    Deterministic decision logic for validation testing.

    Executes trades at predetermined tick numbers for reproducible testing.
    Collects validation data from BacktestingSampleWorker and aggregates
    into BacktestingMetadata for test suite validation.

    Unlike production decision logics (AggressiveTrend, SimpleConsensus):
    - Ignores worker computation values
    - Trades based on tick count, not indicators
    - Designed for test reproducibility, not profitability

    Configuration:
        trade_sequence: List of trade specifications
            - tick_number: When to open trade
            - direction: "LONG" or "SHORT"
            - hold_ticks: How many ticks to hold before closing
            - lot_size: Position size (optional, uses default)
            - order_type: "MARKET" or "LIMIT" (optional, default: "MARKET")
            - price: Limit price (required when order_type="LIMIT")
            - stop_loss: SL price level (optional)
            - take_profit: TP price level (optional)
        modify_sequence: List of SL/TP modification specs (optional)
            - tick_number: When to apply modification
            - stop_loss: New SL price (optional, omit = no change)
            - take_profit: New TP price (optional, omit = no change)
        modify_limit_sequence: List of pending limit order modification specs (optional)
            - tick_number: When to apply modification
            - price: New limit price (optional, omit = no change)
            - stop_loss: New SL price (optional, omit = no change)
            - take_profit: New TP price (optional, omit = no change)
        lot_size: Default lot size for trades (default: 0.1)
    """

    def __init__(
        self,
        name,
        logger: ScenarioLogger,
        config,
        trading_context: TradingContext = None
    ):
        """
        Initialize BacktestingDeterministic logic.

        Args:
            name: Logic identifier
            logger: ScenarioLogger instance
            config: Configuration dict with trade_sequence
        """
        super().__init__(name, logger, config, trading_context=trading_context)

        # Trade sequence configuration
        self.trade_sequence = self.params.get('trade_sequence')
        self.default_lot_size = self.params.get('lot_size')
        self.modify_sequence = self.params.get('modify_sequence')
        self.modify_limit_sequence = self.params.get('modify_limit_sequence')

        # Internal state
        self.tick_count = 0
        self.active_trade: Optional[Dict[str, Any]] = None
        self._started_trade_indices: set = set()
        self._pending_limit_order_id: Optional[str] = None

        # Backtesting tracking
        self.warmup_errors: List[str] = []
        self.bar_snapshots: Dict[str, Dict[str, Any]] = {}
        self.expected_trades: List[Dict[str, Any]] = []
        self.warmup_checked = False

        self.logger.debug(
            f"BacktestingDeterministic initialized: "
            f"{len(self.trade_sequence)} trades in sequence, "
            f"lot_size={self.default_lot_size}"
        )

    # ============================================
    # Required Abstract Methods
    # ============================================

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, ParameterDef]:
        """Backtesting deterministic decision logic parameters."""
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
            'modify_sequence': ParameterDef(
                param_type=list,
                default=[],
                description="List of SL/TP modification specs: tick_number, stop_loss, take_profit"
            ),
            'modify_limit_sequence': ParameterDef(
                param_type=list,
                default=[],
                description="List of pending limit order modification specs: tick_number, price, stop_loss, take_profit"
            ),
        }

    @classmethod
    def get_required_order_types(cls, decision_logic_config: Dict[str, Any]) -> List[OrderType]:
        """
        Declare required order types based on trade_sequence config.

        Scans trade_sequence for order_type fields. MARKET is always
        included (close orders use MARKET internally).

        Args:
            decision_logic_config: Decision logic configuration dict

        Returns:
            List of unique OrderType values needed by this config
        """
        order_types = {OrderType.MARKET}
        for spec in decision_logic_config.get('trade_sequence', []):
            ot_str = spec.get('order_type', 'MARKET')
            order_types.add(OrderType[ot_str])
        return list(order_types)

    def get_required_worker_instances(self) -> Dict[str, str]:
        """
        Declare required worker instance.

        Requires BacktestingSampleWorker for warmup validation
        and bar snapshot capture.

        Returns:
            Dict with worker instance mapping
        """
        return {
            "backtesting_worker": "CORE/backtesting/backtesting_sample_worker"
        }

    def compute(
        self,
        tick: TickData,
        worker_results: Dict[str, WorkerResult],
    ) -> Decision:
        """
        Generate trading decision based on tick count.

        Decision logic:
        1. First tick: Extract warmup data from worker
        2. Check if should close active trade (hold_ticks reached)
        3. Check if should open new trade (tick_number matches sequence)
        4. Otherwise: FLAT (no action)

        Args:
            tick: Current tick data
            worker_results: Results from BacktestingSampleWorker

        Returns:
            Decision object with action based on trade sequence
        """
        self.tick_count += 1

        # ============================================
        # Extract Worker Data (First Tick)
        # ============================================
        if not self.warmup_checked:
            self._extract_worker_data(worker_results)
            self.warmup_checked = True

        # ============================================
        # Check Active Trade Close
        # ============================================
        if self.active_trade and self.tick_count >= self.active_trade['close_tick']:
            # Time to close active trade
            self.active_trade = None
            return Decision(
                action=DecisionLogicAction.FLAT,
                confidence=1.0,
                reason=f"Close trade at tick {self.tick_count}",
                price=tick.mid,
                timestamp=tick.timestamp.isoformat()
            )

        # ============================================
        # Check Trade Sequence for New Trade
        # ============================================
        for idx, spec in enumerate(self.trade_sequence):
            if (self.tick_count >= spec['tick_number']
                    and self.tick_count <= spec['tick_number']+spec['hold_ticks']):

                # Skip trades already submitted where position is gone (SL/TP closed it)
                if idx in self._started_trade_indices:
                    if (self.trading_api
                            and not self.trading_api.has_pending_orders()
                            and len(self.trading_api.get_open_positions()) == 0):
                        continue

                # Time to open new trade
                lot_size = spec.get('lot_size', self.default_lot_size)
                hold_ticks = spec.get('hold_ticks', 100)
                direction = spec['direction']

                # Determine action
                action = (
                    DecisionLogicAction.BUY
                    if direction == 'LONG'
                    else DecisionLogicAction.SELL
                )

                # log once
                if self.tick_count == spec['tick_number']:
                    self._started_trade_indices.add(idx)

                    # Record active trade
                    self.active_trade = {
                        'signal_tick': self.tick_count,
                        'close_tick': self.tick_count + hold_ticks,
                        'direction': direction
                    }

                    # Record expected trade for validation
                    self.expected_trades.append({
                        'signal_tick': self.tick_count,
                        'direction': direction,
                        'lot_size': lot_size,
                        'hold_ticks': hold_ticks
                    })

                    self.logger.info(
                        f"🎯 Trade signal at tick {self.tick_count}: "
                        f"{direction} {lot_size} lots, hold {hold_ticks} ticks"
                    )

                return Decision(
                    action=action,
                    confidence=1.0,
                    reason=f"Open {direction} at tick {self.tick_count}",
                    price=tick.mid,
                    timestamp=tick.timestamp.isoformat(),
                    metadata={
                        'lot_size': lot_size,
                        'signal_tick': self.tick_count,
                        'hold_ticks': hold_ticks,
                        'order_type': spec.get('order_type', 'MARKET'),
                        'limit_price': spec.get('price'),
                        'stop_loss': spec.get('stop_loss'),
                        'take_profit': spec.get('take_profit'),
                    }
                )

        # ============================================
        # No Action (FLAT) or close
        # ============================================
        return Decision(
            action=DecisionLogicAction.FLAT,
            confidence=0.0,
            reason="Waiting for next trade trigger",
            price=tick.mid,
            timestamp=tick.timestamp.isoformat()
        )

    def _execute_decision_impl(
        self,
        decision: Decision,
        tick: TickData
    ) -> Optional[OrderResult]:
        """
        Execute trading decision via DecisionTradingAPI.

        Simplified execution for backtesting:
        - BUY → Open long position
        - SELL → Open short position
        - FLAT with active position → Close position

        Args:
            decision: Decision from compute()
            tick: Current tick data

        Returns:
            OrderResult if order was sent, None otherwise
        """
        if not self.trading_api:
            self.logger.warning(
                "No trading_api available - skipping execution")
            return None

        # Process any pending modifications (positions + limit orders)
        self._process_modify_sequence()
        self._process_modify_limit_sequence()

        # Get parameters from decision metadata or defaults
        lot_size = decision.metadata.get('lot_size', self.default_lot_size)
        stop_loss = decision.metadata.get('stop_loss')
        take_profit = decision.metadata.get('take_profit')
        order_type = OrderType[decision.metadata.get('order_type', 'MARKET')]
        limit_price = decision.metadata.get('limit_price')

        # Check for pending orders
        if self.trading_api.has_pending_orders():
            return None

        # Get confirmed open positions
        open_positions = self.trading_api.get_open_positions()

        # ============================================
        # Handle BUY Signal
        # ============================================
        if decision.action == DecisionLogicAction.BUY:
            if len(open_positions) == 0:
                # Open long position
                order_response = self.trading_api.send_order(
                    symbol=tick.symbol,
                    order_type=order_type,
                    direction=OrderDirection.LONG,
                    lots=lot_size,
                    price=limit_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    comment=f"Backtest LONG at tick {self.tick_count}"
                )
                if (order_response.is_rejected == True):
                    self.logger.error(order_response.rejection_message)
                # Track limit order ID for modify_limit_sequence
                if order_type == OrderType.LIMIT and order_response.order_id:
                    self._pending_limit_order_id = order_response.order_id
                return order_response

        # ============================================
        # Handle SELL Signal
        # ============================================
        elif decision.action == DecisionLogicAction.SELL:
            if len(open_positions) == 0:
                # Open short position
                order_response = self.trading_api.send_order(
                    symbol=tick.symbol,
                    order_type=order_type,
                    direction=OrderDirection.SHORT,
                    lots=lot_size,
                    price=limit_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    comment=f"Backtest SHORT at tick {self.tick_count}"
                )
                if (order_response.is_rejected == True):
                    self.logger.error(order_response.rejection_message)
                # Track limit order ID for modify_limit_sequence
                if order_type == OrderType.LIMIT and order_response.order_id:
                    self._pending_limit_order_id = order_response.order_id
                return order_response

        # ============================================
        # Handle FLAT Signal (Close)
        # ============================================
        elif decision.action == DecisionLogicAction.FLAT:
            for current_position in open_positions:
                self.trading_api.close_position(current_position.position_id)

            # FLAT doesn't return an OrderResult
            return None

        return None

    def _process_modify_sequence(self) -> None:
        """
        Execute pending SL/TP modifications from modify_sequence config.

        Modifies the first open position at the configured tick_number.
        Only keys present in the spec are modified (omitted = no change).
        """
        if not self.modify_sequence:
            return

        for spec in self.modify_sequence:
            if self.tick_count == spec['tick_number']:
                open_positions = self.trading_api.get_open_positions()
                if not open_positions:
                    self.logger.warning(
                        f"Modify at tick {self.tick_count}: no open positions")
                    return

                position = open_positions[0]
                kwargs: Dict[str, Any] = {}
                if 'stop_loss' in spec:
                    kwargs['stop_loss'] = spec['stop_loss']
                if 'take_profit' in spec:
                    kwargs['take_profit'] = spec['take_profit']

                result = self.trading_api.modify_position(
                    position_id=position.position_id,
                    **kwargs
                )
                self.logger.info(
                    f"🔧 Position modify at tick {self.tick_count}: "
                    f"{'success' if result.success else 'FAILED'} — {kwargs}"
                )

    def _process_modify_limit_sequence(self) -> None:
        """
        Execute pending limit order modifications from modify_limit_sequence config.

        Modifies the tracked pending limit order at the configured tick_number.
        Only keys present in the spec are passed (omitted = no change).
        """
        if not self.modify_limit_sequence:
            return

        for spec in self.modify_limit_sequence:
            if self.tick_count == spec['tick_number']:
                if not self._pending_limit_order_id:
                    self.logger.warning(
                        f"Modify limit at tick {self.tick_count}: "
                        f"no pending limit order tracked")
                    return

                kwargs: Dict[str, Any] = {}
                if 'price' in spec:
                    kwargs['price'] = spec['price']
                if 'stop_loss' in spec:
                    kwargs['stop_loss'] = spec['stop_loss']
                if 'take_profit' in spec:
                    kwargs['take_profit'] = spec['take_profit']

                result = self.trading_api.modify_limit_order(
                    order_id=self._pending_limit_order_id,
                    **kwargs
                )
                self.logger.info(
                    f"🔧 Limit order modify at tick {self.tick_count}: "
                    f"{'success' if result.success else 'FAILED'} — {kwargs}"
                )

    def _extract_worker_data(self, worker_results: Dict[str, WorkerResult]) -> None:
        """
        Extract warmup validation and bar snapshots from worker.

        BacktestingSampleWorker provides:
        - warmup_status: Dict[timeframe, {valid, expected, actual, error}]
        - bar_snapshots: Dict[key, bar_dict]

        Decision Logic aggregates errors and stores snapshots for
        BacktestingMetadata.

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

        # Extract bar snapshots (already serialized dicts from worker)
        self.bar_snapshots = worker_result.metadata.get('bar_snapshots', {})

        if self.bar_snapshots:
            self.logger.debug(
                f"📸 Received {len(self.bar_snapshots)} bar snapshots from worker"
            )

    # ============================================
    # Statistics Override
    # ============================================

    def get_statistics(self) -> DecisionLogicStats:
        """
        Get statistics with BacktestingMetadata.

        Overrides parent to include backtesting validation data:
        - warmup_errors
        - bar_snapshots
        - expected_trades
        - tick_count

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
            f"📊 BacktestingMetadata: "
            f"errors={len(self.warmup_errors)}, "
            f"snapshots={len(self.bar_snapshots)}, "
            f"expected_trades={len(self.expected_trades)}, "
            f"ticks={self.tick_count}"
        )

        return base_stats
