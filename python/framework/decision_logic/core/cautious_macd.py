# ============================================
# python/framework/decision_logic/core/cautious_macd.py
# ============================================
"""
FiniexTestingIDE - Cautious MACD Decision Logic

Strategy:
- MACD histogram crossover (zero-line cross) as directional signal
- RSI as AND-filter (no entry against extremes)
- Entry via STOP order (breakout confirmation, not market fill)
- SL/TP set directly at send_order time
- Break-even via modify_position after configurable profit move
- Cancel pending STOP order on counter-direction crossover only
- Exit open position on MACD counter-crossover

State Machine:
  FLAT → (crossover + RSI ok + confidence ok) → PENDING_ENTRY (STOP placed)
  PENDING_ENTRY → (STOP triggered) → IN_POSITION
  PENDING_ENTRY → (counter-direction crossover only) → FLAT (cancel_stop_order)
  IN_POSITION → (break-even threshold) → modify_position SL=entry
  IN_POSITION → (counter-crossover) → FLAT (close_position)

Order types used:
- STOP (entry with breakout confirmation)
- modify_position (break-even)
- cancel_stop_order (signal reversal before trigger)
- close_position (MACD counter-signal while in position)
"""

import traceback
from typing import Any, Dict, List, Optional

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.decision_logic.abstract_decision_logic import \
    AbstractDecisionLogic
from python.framework.types.market_data_types import Bar, TickData
from python.framework.types.decision_logic_types import Decision, DecisionLogicAction
from python.framework.types.market_types import TradingContext
from python.framework.types.parameter_types import ParameterDef
from python.framework.types.worker_types import WorkerResult
from python.framework.types.order_types import (
    OrderStatus,
    OrderType,
    OrderDirection,
    OrderResult
)


class CautiousMACD(AbstractDecisionLogic):
    """
    Cautious MACD strategy: STOP entries with RSI filter, SL/TP, break-even.

    Unlike AggressiveTrend (market orders, OR-logic):
    - AND-logic (both MACD crossover + RSI filter required)
    - STOP orders only (no immediate market fill)
    - Integrated SL/TP from order submission
    - Active pending order management (cancel on counter-signal)
    - Break-even modification after configurable profit move

    Configuration options:
    - rsi_filter_buy: RSI max for BUY entry (default: 60, not overbought)
    - rsi_filter_sell: RSI min for SELL entry (default: 40, not oversold)
    - stop_distance_pips: STOP trigger distance from current price (default: 15)
    - sl_pips: Stop loss distance from estimated entry (default: 20)
    - tp_pips: Take profit distance from estimated entry (default: 40)
    - pip_size: Pip size for the traded instrument (default: 0.0001)
    - break_even_trigger_pips: Break-even after X pips profit (default: 15)
    - min_histogram: Minimum histogram absolute value for valid crossover (default: 0.00005)
    - min_confidence: Minimum signal confidence to act (0.0 = disabled, default: 0.0)
    - lot_size: Fixed lot size (default: 0.1)
    - min_free_margin: Minimum free margin for new entries (default: 1000)
    """

    def __init__(
        self,
        name,
        logger: ScenarioLogger,
        config,
        trading_context: TradingContext = None
    ):
        """
        Initialize Cautious MACD logic.

        Args:
            name: Logic identifier
            logger: Scenario logger
            config: Configuration dict or ValidatedParameters
            trading_context: Optional trading context
        """
        super().__init__(name, logger, config, trading_context=trading_context)

        # Trading parameters (all guaranteed by schema defaults)
        self.rsi_filter_buy = self.params.get('rsi_filter_buy')
        self.rsi_filter_sell = self.params.get('rsi_filter_sell')
        self.stop_distance_pips = self.params.get('stop_distance_pips')
        self.sl_pips = self.params.get('sl_pips')
        self.tp_pips = self.params.get('tp_pips')
        self.pip_size = self.params.get('pip_size')
        self.break_even_trigger_pips = self.params.get('break_even_trigger_pips')
        self.min_histogram = self.params.get('min_histogram')
        self.min_confidence = self.params.get('min_confidence')
        self.lot_size = self.params.get('lot_size')
        self.min_free_margin = self.params.get('min_free_margin')
        self.use_stop_limit = self.params.get('use_stop_limit')
        self.stop_limit_offset_pips = self.params.get('stop_limit_offset_pips')

        # Internal state
        self._prev_histogram: Optional[float] = None
        self._pending_stop_order_id: Optional[str] = None
        self._pending_direction: Optional[OrderDirection] = None
        self._break_even_applied: bool = False
        self._current_position_id: Optional[str] = None

        _order_mode = "STOP_LIMIT" if self.use_stop_limit else "STOP"
        _conf_str = f"minConf={self.min_confidence}" if self.min_confidence > 0.0 else "minConf=off"
        self.logger.debug(
            f"CautiousMACD initialized: "
            f"RSI filter({self.rsi_filter_buy}/{self.rsi_filter_sell}), "
            f"{_order_mode} dist={self.stop_distance_pips}pip, "
            f"SL={self.sl_pips}/TP={self.tp_pips}, "
            f"BreakEven={self.break_even_trigger_pips}pip, "
            f"Lots={self.lot_size}, {_conf_str}"
        )

    # ============================================
    # abstractmethods
    # ============================================

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, ParameterDef]:
        """CautiousMACD decision logic parameters."""
        return {
            'rsi_filter_buy': ParameterDef(
                param_type=float, default=60, min_val=1, max_val=99,
                description="RSI max for BUY entry (skip if RSI is overbought)"
            ),
            'rsi_filter_sell': ParameterDef(
                param_type=float, default=40, min_val=1, max_val=99,
                description="RSI min for SELL entry (skip if RSI is oversold)"
            ),
            'stop_distance_pips': ParameterDef(
                param_type=float, default=15, min_val=1, max_val=500,
                description="STOP trigger distance from current price in pips"
            ),
            'sl_pips': ParameterDef(
                param_type=float, default=20, min_val=1, max_val=1000,
                description="Stop loss distance from estimated entry in pips"
            ),
            'tp_pips': ParameterDef(
                param_type=float, default=40, min_val=1, max_val=2000,
                description="Take profit distance from estimated entry in pips"
            ),
            'pip_size': ParameterDef(
                param_type=float, default=0.0001, min_val=0.000001, max_val=1.0,
                description="Pip size for the traded instrument (0.01 for JPY pairs)"
            ),
            'break_even_trigger_pips': ParameterDef(
                param_type=float, default=15, min_val=0, max_val=1000,
                description="Move SL to entry after X pips profit (0 = disabled)"
            ),
            'min_histogram': ParameterDef(
                param_type=float, default=0.00005, min_val=0, max_val=100000,
                description="Minimum |histogram| value for a valid crossover (noise filter)"
            ),
            'min_confidence': ParameterDef(
                param_type=float, default=0.0, min_val=0.0, max_val=1.0,
                description="Minimum confidence score to act on a crossover (0.0 = disabled)"
            ),
            'lot_size': ParameterDef(
                param_type=float, default=0.1, min_val=0.01, max_val=100.0,
                description="Fixed lot size for STOP orders"
            ),
            'min_free_margin': ParameterDef(
                param_type=float, default=1000, min_val=0,
                description="Minimum free margin before opening new position"
            ),
            'use_stop_limit': ParameterDef(
                param_type=bool, default=False,
                description="Use STOP_LIMIT instead of STOP (required for Kraken)"
            ),
            'stop_limit_offset_pips': ParameterDef(
                param_type=float, default=2, min_val=0, max_val=100,
                description="Limit price offset beyond stop_price for STOP_LIMIT orders (in pips)"
            ),
        }

    @classmethod
    def get_required_order_types(cls, decision_logic_config: Dict[str, Any]) -> List[OrderType]:
        """
        Declare required order types for CautiousMACD.

        Uses STOP by default. Set use_stop_limit=true in decision_logic_config
        for brokers that do not support pure STOP orders (e.g. Kraken).

        Returns:
            List containing OrderType.STOP or OrderType.STOP_LIMIT
        """
        if decision_logic_config.get('use_stop_limit', False):
            return [OrderType.STOP_LIMIT]
        return [OrderType.STOP]

    def get_required_worker_instances(self) -> Dict[str, str]:
        """
        Define required worker instances for CautiousMACD strategy.

        Requires:
        - macd_main: MACD for crossover signal
        - rsi_filter: RSI for entry filter

        Returns:
            Dict[instance_name, worker_type]
        """
        return {
            "macd_main": "CORE/macd",
            "rsi_filter": "CORE/rsi",
        }

    def compute(
        self,
        tick: TickData,
        worker_results: Dict[str, WorkerResult],
    ) -> Decision:
        """
        Generate trading decision from MACD crossover + RSI filter.

        Crossover detection: MACD histogram crosses zero line with
        sufficient magnitude (|histogram| >= min_histogram).
        RSI prevents entries against overbought/oversold extremes.

        _prev_histogram is updated unconditionally each call.
        No crossover fires when MACD did not recompute (histogram
        stays constant between bar updates → prev == current).

        Args:
            tick: Current tick data
            worker_results: Results from macd_main and rsi_filter workers

        Returns:
            Decision with BUY, SELL, or FLAT action
        """
        macd_result = worker_results.get("macd_main")
        rsi_result = worker_results.get("rsi_filter")

        if not macd_result or not rsi_result:
            return Decision(
                action=DecisionLogicAction.FLAT,
                confidence=0.0,
                reason="Missing worker results",
                price=tick.mid,
                timestamp=tick.timestamp.isoformat(),
            )

        histogram = macd_result.value.get("histogram", 0.0)
        macd_line = macd_result.value.get("macd", 0.0)
        signal_line = macd_result.value.get("signal", 0.0)
        rsi_value = rsi_result.value

        _prev = f"{self._prev_histogram:.4f}" if self._prev_histogram is not None else "None"
        self.logger.debug(
            f"[compute] MACD={macd_line:.4f} sig={signal_line:.4f} "
            f"hist={histogram:.4f} (prev={_prev}) RSI={rsi_value:.1f}"
        )

        # Crossover detection (no crossover if histogram didn't change)
        crossed_up = False
        crossed_down = False

        if self._prev_histogram is not None:
            if (self._prev_histogram <= 0 and histogram > 0
                    and abs(histogram) >= self.min_histogram):
                crossed_up = True
                self.logger.info(
                    f"📈 MACD cross-UP detected: hist {self._prev_histogram:.4f} → {histogram:.4f}, "
                    f"RSI={rsi_value:.1f} (filter_buy={self.rsi_filter_buy})"
                )
            elif (self._prev_histogram >= 0 and histogram < 0
                    and abs(histogram) >= self.min_histogram):
                crossed_down = True
                self.logger.info(
                    f"📉 MACD cross-DOWN detected: hist {self._prev_histogram:.4f} → {histogram:.4f}, "
                    f"RSI={rsi_value:.1f} (filter_sell={self.rsi_filter_sell})"
                )
            elif self._prev_histogram != histogram:
                # Histogram changed but no crossover (same side, or too small)
                if abs(histogram) < self.min_histogram and (
                    (self._prev_histogram <= 0 and histogram > 0)
                    or (self._prev_histogram >= 0 and histogram < 0)
                ):
                    self.logger.debug(
                        f"[compute] Cross attempt blocked: |hist|={abs(histogram):.4f} < min={self.min_histogram}"
                    )

        self._prev_histogram = histogram

        # BUY: crossed up + RSI not overbought
        if crossed_up and rsi_value < self.rsi_filter_buy:
            confidence = self._calculate_confidence(histogram, rsi_value, True)
            if self.min_confidence > 0.0 and confidence < self.min_confidence:
                self.logger.info(
                    f"🚫 BUY blocked by confidence: {confidence:.2f} < {self.min_confidence}"
                )
            else:
                return Decision(
                    action=DecisionLogicAction.BUY,
                    confidence=confidence,
                    reason=f"MACD cross-up hist={histogram:.4f}, RSI={rsi_value:.1f}, conf={confidence:.2f}",
                    price=tick.mid,
                    timestamp=tick.timestamp.isoformat(),
                )

        if crossed_up and rsi_value >= self.rsi_filter_buy:
            self.logger.info(
                f"🚫 BUY blocked by RSI: {rsi_value:.1f} >= {self.rsi_filter_buy}"
            )

        # SELL: crossed down + RSI not oversold
        if crossed_down and rsi_value > self.rsi_filter_sell:
            confidence = self._calculate_confidence(histogram, rsi_value, False)
            if self.min_confidence > 0.0 and confidence < self.min_confidence:
                self.logger.info(
                    f"🚫 SELL blocked by confidence: {confidence:.2f} < {self.min_confidence}"
                )
            else:
                return Decision(
                    action=DecisionLogicAction.SELL,
                    confidence=confidence,
                    reason=f"MACD cross-down hist={histogram:.4f}, RSI={rsi_value:.1f}, conf={confidence:.2f}",
                    price=tick.mid,
                    timestamp=tick.timestamp.isoformat(),
                )

        if crossed_down and rsi_value <= self.rsi_filter_sell:
            self.logger.info(
                f"🚫 SELL blocked by RSI: {rsi_value:.1f} <= {self.rsi_filter_sell}"
            )

        return Decision(
            action=DecisionLogicAction.FLAT,
            confidence=0.5,
            reason="No MACD crossover or RSI filter blocked",
            price=tick.mid,
            timestamp=tick.timestamp.isoformat(),
        )

    def _execute_decision_impl(
        self,
        decision: Decision,
        tick: TickData
    ) -> Optional[OrderResult]:
        """
        Execute state machine: FLAT / PENDING_ENTRY / IN_POSITION.

        FLAT:
          - New BUY/SELL signal + margin ok → place STOP order, store ID

        PENDING_ENTRY (stop in latency or active):
          - In pipeline (has_pipeline_orders=True): wait
          - Active STOP + counter-direction crossover → cancel_stop_order
          - Active STOP + FLAT → keep waiting (stop may still trigger)

        IN_POSITION:
          - New position detected → reset break-even flag
          - Break-even threshold reached → modify_position SL=entry_price
          - Counter-crossover signal → close_position

        Args:
            decision: Decision from compute()
            tick: Current tick data

        Returns:
            OrderResult if order was placed, None otherwise
        """
        if not self.trading_api:
            self.logger.warning("Trading API not available - cannot execute")
            return None

        open_positions = self.trading_api.get_open_positions()
        active_counts = self.trading_api.get_active_order_counts()
        in_pipeline = self.trading_api.has_pipeline_orders()
        has_active_stop = active_counts.get("active_stops", 0) > 0

        self.logger.debug(
            f"[state] positions={len(open_positions)} in_pipeline={in_pipeline} "
            f"active_stops={active_counts.get('active_stops', 0)} "
            f"decision={decision.action.value}"
        )

        # ============================================
        # IN_POSITION
        # ============================================
        if open_positions:
            position = open_positions[0]

            # New position appeared (STOP was triggered)
            if position.position_id != self._current_position_id:
                self._current_position_id = position.position_id
                self._pending_stop_order_id = None
                self._pending_direction = None
                self._break_even_applied = False
                self.logger.info(
                    f"🟢 STOP triggered → Position open: "
                    f"{position.direction} {position.lots} lots "
                    f"@ {position.entry_price:.5f} (ID: {position.position_id})"
                )

            # Break-even check
            if not self._break_even_applied and self.break_even_trigger_pips > 0:
                self._check_and_apply_break_even(position, tick)

            # Counter-signal exit
            new_dir = (OrderDirection.LONG
                       if decision.action == DecisionLogicAction.BUY
                       else OrderDirection.SHORT)

            if (decision.action != DecisionLogicAction.FLAT
                    and position.direction != new_dir):
                self.logger.info(
                    f"🔄 MACD counter-signal: closing {position.direction} "
                    f"position (ID: {position.position_id})"
                )
                self.trading_api.close_position(position.position_id)
                self._current_position_id = None
                self._break_even_applied = False

            return None

        # ============================================
        # PENDING_ENTRY
        # ============================================
        if in_pipeline or has_active_stop:
            # In latency pipeline: cannot cancel yet, just wait
            if in_pipeline:
                return None

            # Active STOP: cancel only on counter-direction crossover (FLAT = keep waiting)
            if has_active_stop and self._pending_stop_order_id:
                should_cancel = False
                if (decision.action != DecisionLogicAction.FLAT
                        and self._pending_direction is not None):
                    new_dir = (OrderDirection.LONG
                               if decision.action == DecisionLogicAction.BUY
                               else OrderDirection.SHORT)
                    should_cancel = (new_dir != self._pending_direction)

                if should_cancel:
                    cancelled = self.trading_api.cancel_stop_order(
                        self._pending_stop_order_id
                    )
                    if cancelled:
                        self.logger.info(
                            f"❌ STOP order cancelled (counter/flat signal): "
                            f"{self._pending_stop_order_id}"
                        )
                    self._pending_stop_order_id = None
                    self._pending_direction = None

            return None

        # ============================================
        # FLAT — new entry
        # ============================================
        if decision.action == DecisionLogicAction.FLAT:
            return None

        new_direction = (OrderDirection.LONG
                         if decision.action == DecisionLogicAction.BUY
                         else OrderDirection.SHORT)

        # Margin check
        account = self.trading_api.get_account_info(new_direction)
        if account.free_margin < self.min_free_margin:
            self.logger.info(
                f"Insufficient free margin: {account.free_margin:.2f} "
                f"< {self.min_free_margin} - skipping entry"
            )
            return None

        # Calculate STOP price and SL/TP levels
        dist = self.stop_distance_pips * self.pip_size
        sl_dist = self.sl_pips * self.pip_size
        tp_dist = self.tp_pips * self.pip_size

        if new_direction == OrderDirection.LONG:
            stop_price = tick.ask + dist
            sl_price = stop_price - sl_dist
            tp_price = stop_price + tp_dist
        else:
            stop_price = tick.bid - dist
            sl_price = stop_price + sl_dist
            tp_price = stop_price - tp_dist

        try:
            if self.use_stop_limit:
                # STOP_LIMIT: stop_price = trigger, price = limit fill price
                offset = self.stop_limit_offset_pips * self.pip_size
                limit_price = (stop_price + offset
                               if new_direction == OrderDirection.LONG
                               else stop_price - offset)
                order_result = self.trading_api.send_order(
                    symbol=tick.symbol,
                    order_type=OrderType.STOP_LIMIT,
                    direction=new_direction,
                    lots=self.lot_size,
                    stop_price=stop_price,
                    price=limit_price,
                    stop_loss=sl_price,
                    take_profit=tp_price,
                    comment=f"CautiousMACD: {decision.reason[:50]}"
                )
            else:
                order_result = self.trading_api.send_order(
                    symbol=tick.symbol,
                    order_type=OrderType.STOP,
                    direction=new_direction,
                    lots=self.lot_size,
                    stop_price=stop_price,
                    stop_loss=sl_price,
                    take_profit=tp_price,
                    comment=f"CautiousMACD: {decision.reason[:50]}"
                )

            _mode = "STOP_LIMIT" if self.use_stop_limit else "STOP"
            if order_result.status == OrderStatus.PENDING:
                self._pending_stop_order_id = order_result.order_id
                self._pending_direction = new_direction
                self.logger.info(
                    f"⏳ {_mode} order placed: {new_direction} {self.lot_size} lots "
                    f"trigger={stop_price:.5f} SL={sl_price:.5f} TP={tp_price:.5f} "
                    f"(ID: {order_result.order_id})"
                )
            elif order_result.is_rejected:
                self.logger.warning(
                    f"✗ {_mode} order rejected: "
                    f"{order_result.rejection_reason.value if order_result.rejection_reason else 'Unknown'} - "
                    f"{order_result.rejection_message}"
                )

            return order_result

        except Exception:
            self.logger.error(
                f"❌ STOP order failed:\n{traceback.format_exc()}"
            )
            return None

    # ============================================
    # Private helpers
    # ============================================

    def _check_and_apply_break_even(self, position, tick: TickData) -> None:
        """
        Move SL to entry_price once profit exceeds break_even_trigger_pips.

        Args:
            position: Current open Position
            tick: Current tick data
        """
        trigger = self.break_even_trigger_pips * self.pip_size

        if position.direction == OrderDirection.LONG:
            profit_move = tick.bid - position.entry_price
        else:
            profit_move = position.entry_price - tick.ask

        if profit_move < trigger:
            return

        result = self.trading_api.modify_position(
            position_id=position.position_id,
            stop_loss=position.entry_price,
        )

        if result.success:
            self._break_even_applied = True
            self.logger.info(
                f"🔒 Break-even set: SL → {position.entry_price:.5f} "
                f"(profit move: {profit_move / self.pip_size:.1f} pips)"
            )
        else:
            self.logger.debug(
                f"Break-even modify rejected: {result.rejection_reason}"
            )

    def _calculate_confidence(
        self,
        histogram: float,
        rsi_value: float,
        is_buy: bool,
    ) -> float:
        """
        Calculate signal confidence from histogram magnitude and RSI distance.

        Args:
            histogram: Current MACD histogram value
            rsi_value: Current RSI value
            is_buy: True for BUY signal, False for SELL

        Returns:
            Confidence value between 0.0 and 1.0
        """
        # Base confidence for any valid crossover
        confidence = 0.5

        # Larger histogram = stronger momentum
        hist_strength = min(abs(histogram) / (self.min_histogram * 10), 0.3)
        confidence += hist_strength

        # RSI distance from filter boundary = extra margin of safety
        if is_buy:
            rsi_margin = (self.rsi_filter_buy - rsi_value) / self.rsi_filter_buy
        else:
            rsi_margin = (rsi_value - self.rsi_filter_sell) / (100 - self.rsi_filter_sell)

        confidence += max(0.0, min(rsi_margin * 0.2, 0.2))

        return min(1.0, confidence)
