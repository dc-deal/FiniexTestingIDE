"""
FiniexTestingIDE - Simple Consensus Decision Logic (OBV Enhanced)
Reference implementation of RSI + Envelope + OBV consensus strategy

ENHANCED: OBV as confirmation filter
- Blocks trades where volume trend opposes price signal
- Boosts confidence when volume confirms direction

:
- Implements get_required_order_types() → [OrderType.MARKET]
- Implements execute_decision() → Market orders with margin checks
- Uses DecisionTradingAPI instead of TradeSimulator directly
- ONE POSITION ONLY: Closes existing position before opening new one

Strategy Rules:
- BUY when RSI oversold (≤30) AND price near lower envelope (≤30%)
        AND OBV trend is NOT bearish (volume confirmation)
- SELL when RSI overbought (≥70) AND price near upper envelope (≥70%)
        AND OBV trend is NOT bullish (volume confirmation)
- FLAT otherwise (no clear signal)

Trading Rules:
- Market orders only (MVP)
- Check free margin before trading (min 1000 EUR)
- Fixed lot size 0.1 (TODO: Position sizing logic)
- No SL/TP for MVP (TODO: Risk management)
- ONE POSITION ONLY: Maximum one position at a time

Position Management:
- FLAT signal → Close existing position
- Same direction signal → Skip (already have position)
- Opposite direction signal → Close old, open new (reversal)

This logic requires three workers:
- RSI: Relative Strength Index indicator
- Envelope: Price envelope/bollinger bands
- OBV: On-Balance Volume (volume confirmation)
"""

import traceback
from typing import Any, Dict, List, Optional

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.decision_logic.abstract_decision_logic import \
    AbstractDecisionLogic
from python.framework.types.market_data_types import Bar, TickData
from python.framework.types.market_types import TradingContext
from python.framework.types.decision_logic_types import Decision, DecisionLogicAction

from python.framework.types.order_types import (
    OrderStatus,
    OrderType,
    OrderDirection,
    OrderResult
)
from python.framework.types.parameter_types import ParameterDef
from python.framework.types.worker_types import WorkerResult


class SimpleConsensus(AbstractDecisionLogic):
    """
    Simple consensus strategy using RSI, Envelope, and OBV indicators.

    This is a conservative strategy that requires confirmation from
    multiple indicators before generating buy/sell signals.

    Configuration options:
    - rsi_oversold: RSI threshold for oversold (default: 30)
    - rsi_overbought: RSI threshold for overbought (default: 70)
    - envelope_lower_threshold: Price position threshold for buy (default: 0.3)
    - envelope_upper_threshold: Price position threshold for sell (default: 0.7)
    - min_confidence: Minimum confidence to generate signal (default: 0.5)
    - min_free_margin: Minimum free margin required for trades (default: 1000)
    - lot_size: Fixed lot size for orders (default: 0.1)

    OBV Configuration (NEW):
    - obv_filter_enabled: Enable/disable OBV filter (default: True)
    - obv_block_opposite_trend: Block trades against OBV trend (default: True)
    - obv_confidence_boost: Confidence bonus when OBV confirms (default: 0.1)
    """

    def __init__(
        self,
        name,
        logger: ScenarioLogger,
        config,
        trading_context: TradingContext = None
    ):
        """
        Initialize Simple Consensus logic.

        Args:
            name: Logic identifier
            config: Configuration dict with thresholds
            trading_context: Trading environment context (optional)
        """
        super().__init__(name, logger, config, trading_context=trading_context)
        # Store trading context
        self._trading_context = trading_context

        # All values guaranteed present by schema defaults + Factory validation
        self.rsi_oversold = self.params.get('rsi_oversold')
        self.rsi_overbought = self.params.get('rsi_overbought')
        self.envelope_lower = self.params.get('envelope_lower_threshold')
        self.envelope_upper = self.params.get('envelope_upper_threshold')
        self.min_confidence = self.params.get('min_confidence')

        # Trading configuration
        self.min_free_margin = self.params.get('min_free_margin')
        self.lot_size = self.params.get('lot_size')

        # OBV configuration
        self.obv_filter_enabled = self.params.get('obv_filter_enabled')
        self.obv_block_opposite_trend = self.params.get(
            'obv_block_opposite_trend')
        self.obv_confidence_boost = self.params.get('obv_confidence_boost')

        self.logger.debug(
            f"SimpleConsensus initialized: "
            f"RSI({self.rsi_oversold}/{self.rsi_overbought}), "
            f"Envelope({self.envelope_lower}/{self.envelope_upper}), "
            f"OBV(enabled={self.obv_filter_enabled}, boost={self.obv_confidence_boost}), "
            f"Lots={self.lot_size}, MinMargin={self.min_free_margin}"
        )

    # ============================================
    # abstractmethods
    # ============================================

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, ParameterDef]:
        """SimpleConsensus decision logic parameters with validation ranges."""
        return {
            'rsi_oversold': ParameterDef(
                param_type=float, default=30, min_val=1, max_val=49,
                description="RSI oversold threshold (buy signal)"
            ),
            'rsi_overbought': ParameterDef(
                param_type=float, default=70, min_val=51, max_val=99,
                description="RSI overbought threshold (sell signal)"
            ),
            'envelope_lower_threshold': ParameterDef(
                param_type=float, default=0.3, min_val=0.0, max_val=1.0,
                description="Envelope position threshold for buy signal"
            ),
            'envelope_upper_threshold': ParameterDef(
                param_type=float, default=0.7, min_val=0.0, max_val=1.0,
                description="Envelope position threshold for sell signal"
            ),
            'min_confidence': ParameterDef(
                param_type=float, default=0.5, min_val=0.0, max_val=1.0,
                description="Minimum confidence to generate trading signal"
            ),
            'min_free_margin': ParameterDef(
                param_type=float, default=1000, min_val=0,
                description="Minimum free margin required before opening trade"
            ),
            'lot_size': ParameterDef(
                param_type=float, default=0.1, min_val=0.01, max_val=100.0,
                description="Fixed lot size for market orders"
            ),
            'obv_filter_enabled': ParameterDef(
                param_type=bool, default=True,
                description="Enable OBV volume confirmation filter"
            ),
            'obv_block_opposite_trend': ParameterDef(
                param_type=bool, default=True,
                description="Block trades when OBV trend opposes signal direction"
            ),
            'obv_confidence_boost': ParameterDef(
                param_type=float, default=0.1, min_val=0.0, max_val=1.0,
                description="Confidence bonus when OBV confirms trade direction"
            ),
        }

    @classmethod
    def get_required_order_types(cls, decision_logic_config: Dict[str, Any]) -> List[OrderType]:
        """
        Declare required order types for this strategy.

        SimpleConsensus uses only Market orders for MVP.
        Post-MVP: Could use Limit orders for better entry prices.

        Returns:
            List containing OrderType.MARKET
        """
        return [OrderType.MARKET]

    def _execute_decision_impl(
        self,
        decision: Decision,
        tick: TickData
    ) -> Optional[OrderResult]:
        """
        Implementation: Execute trading decision via DecisionTradingAPI.

        ONE POSITION ONLY Strategy:
        1. FLAT signal → Close existing position (exit)
        2. Same direction signal → Skip (already have what we want)
        3. Opposite direction signal → Close old, open new (reversal)
        4. New signal with no position → Open position (entry)

        Note: get_open_positions() automatically excludes positions being closed.
        Latency simulation is handled internally by TradeSimulator.

        Args:
            decision: Decision object from compute()
            tick: Current tick data

        Returns:
            OrderResult if order was sent, None if no trade
        """
        # Check if trading API is available
        if not self.trading_api:
            self.logger.warning(
                "Trading API not available - cannot execute decision")
            return None

        if decision.action == DecisionLogicAction.FLAT:
            return None

        # ============================================
        # Check for pending orders (avoid double-ordering)
        # ============================================
        if self.trading_api.has_pending_orders():
            # Orders in flight — wait for them to resolve
            return None

        # ============================================
        # Get confirmed open positions
        # ============================================
        open_positions = self.trading_api.get_open_positions()

        new_direction = OrderDirection.LONG if decision.action == DecisionLogicAction.BUY else OrderDirection.SHORT

        # ============================================
        # STEP 1: Check if we already have a position
        # ============================================
        if len(open_positions) > 0:
            current_position = open_positions[0]

            # Same direction? Skip (we already have what the strategy wants)
            if current_position.direction == new_direction:
                return None

            # Opposite direction? Close old position (signal reversal)
            self.logger.info(
                f"🔄 Signal reversal detected: {current_position.direction} → {new_direction}"
            )
            self.logger.info(
                f"   Closing {current_position.direction} position "
                f"(ID: {current_position.position_id})"
            )
            self.trading_api.close_position(current_position.position_id)
            # This is it for this tick, we'll wait until the position gets closed properly
            return

        # ============================================
        # STEP 3: Open new position
        # ============================================

        # Check account state (margin available)
        account = self.trading_api.get_account_info(new_direction)

        if account.free_margin < self.min_free_margin:
            self.logger.info(
                f"Insufficient free margin: {account.free_margin:.2f} "
                f"< {self.min_free_margin} - skipping trade"
            )
            return None

        # Send market order
        try:
            order_result = self.trading_api.send_order(
                symbol=tick.symbol,
                order_type=OrderType.MARKET,
                direction=new_direction,
                lots=self.lot_size,
                comment=f"SimpleConsensus: {decision.reason[:50]}"
            )

            # Log order submission status
            if order_result.status == OrderStatus.PENDING:
                self.logger.info(
                    f"⏳ Order submitted: {new_direction} {self.lot_size} lots "
                    f"(ID: {order_result.order_id}) - awaiting execution"
                )
            elif order_result.is_rejected:
                self.logger.warning(
                    f"✗ Order rejected: {order_result.rejection_reason.value if order_result.rejection_reason else 'Unknown'} - "
                    f"{order_result.rejection_message}"
                )

            return order_result

        except Exception as e:
            self.logger.error(
                f"❌ Order execution failed: \n{traceback.format_exc()}")
            return None

    # ============================================
    # Existing methods (unchanged)
    # ============================================

    def get_required_worker_instances(self) -> Dict[str, str]:
        """
        Define required worker instances for SimpleConsensus strategy.

        Requires:
        - rsi_fast: Fast RSI indicator for overbought/oversold detection
        - envelope_main: Envelope for price position analysis
        - obv_volume: On-Balance Volume for volume confirmation (NEW)

        Returns:
            Dict[instance_name, worker_type]
        """
        return {
            "rsi_fast": "CORE/rsi",
            "envelope_main": "CORE/envelope",
            "obv_volume": "CORE/obv"
        }

    def compute(
        self,
        tick: TickData,
        worker_results: Dict[str, WorkerResult],
    ) -> Decision:
        """
        Generate trading decision based on consensus between RSI, Envelope, and OBV.

        This is a conservative strategy - RSI and Envelope must agree,
        and OBV must not oppose the signal direction.

        Args:
            tick: Current tick data
            worker_results: Results from rsi, envelope, and obv workers

        Returns:
            Decision object with action, confidence, and reason
        """
        # Extract worker results
        rsi_result = worker_results.get("rsi_fast")
        envelope_result = worker_results.get("envelope_main")
        obv_result = worker_results.get("obv_volume")

        if not rsi_result or not envelope_result:
            return Decision(
                action=DecisionLogicAction.FLAT,
                confidence=0.0,
                reason="Missing worker results (RSI/Envelope)",
                price=tick.mid,
                timestamp=tick.timestamp.isoformat(),
            )

        # Extract indicator values
        rsi_value = rsi_result.value
        envelope_data = envelope_result.value

        # Envelope provides position (0.0 = lower band, 1.0 = upper band)
        envelope_position = envelope_data.get("position", 0.5)

        # Extract OBV trend (NEW)
        obv_trend = "neutral"
        obv_has_volume = False
        if obv_result and obv_result.metadata:
            obv_trend = obv_result.metadata.get("trend", "neutral")
            obv_has_volume = obv_result.metadata.get("has_volume", False)

        self.logger.verbose(
            f"📊 Indicators: RSI={rsi_value:.1f}, Envelope={envelope_position:.2f}, "
            f"OBV trend={obv_trend}, has_volume={obv_has_volume}"
        )

        # Check for BUY signal (consensus required)
        if (
            rsi_value <= self.rsi_oversold
            and envelope_position <= self.envelope_lower
        ):
            # OBV Filter: Block if trend is bearish (volume going against us)
            obv_blocks = (
                self.obv_filter_enabled
                and self.obv_block_opposite_trend
                and obv_trend == "bearish"
            )

            if obv_blocks:
                self.logger.verbose(
                    f"🚫 BUY blocked by OBV: trend={obv_trend} (bearish opposes buy)"
                )
                return Decision(
                    action=DecisionLogicAction.FLAT,
                    confidence=0.3,
                    reason=f"BUY signal blocked by OBV (trend={obv_trend})",
                    price=tick.mid,
                    timestamp=tick.timestamp.isoformat(),
                )

            confidence = self._calculate_buy_confidence(
                rsi_value, envelope_position)

            # OBV Confidence Boost: Add bonus if volume confirms direction
            obv_boost = 0.0
            if self.obv_filter_enabled and obv_trend == "bullish":
                obv_boost = self.obv_confidence_boost
                confidence = min(1.0, confidence + obv_boost)

            self.logger.verbose(
                f"✅ BUY signal: confidence={confidence:.2f} (OBV boost={obv_boost:.2f})"
            )

            if confidence >= self.min_confidence:
                return Decision(
                    action=DecisionLogicAction.BUY,
                    confidence=confidence,
                    reason=f"RSI={rsi_value:.1f} + Envelope={envelope_position:.2f} + OBV={obv_trend}",
                    price=tick.mid,
                    timestamp=tick.timestamp.isoformat(),
                )

        # Check for SELL signal (consensus required)
        if (
            rsi_value >= self.rsi_overbought
            and envelope_position >= self.envelope_upper
        ):
            # OBV Filter: Block if trend is bullish (volume going against us)
            obv_blocks = (
                self.obv_filter_enabled
                and self.obv_block_opposite_trend
                and obv_trend == "bullish"
            )

            if obv_blocks:
                self.logger.verbose(
                    f"🚫 SELL blocked by OBV: trend={obv_trend} (bullish opposes sell)"
                )
                return Decision(
                    action=DecisionLogicAction.FLAT,
                    confidence=0.3,
                    reason=f"SELL signal blocked by OBV (trend={obv_trend})",
                    price=tick.mid,
                    timestamp=tick.timestamp.isoformat(),
                )

            confidence = self._calculate_sell_confidence(
                rsi_value, envelope_position)

            # OBV Confidence Boost: Add bonus if volume confirms direction
            obv_boost = 0.0
            if self.obv_filter_enabled and obv_trend == "bearish":
                obv_boost = self.obv_confidence_boost
                confidence = min(1.0, confidence + obv_boost)

            self.logger.verbose(
                f"✅ SELL signal: confidence={confidence:.2f} (OBV boost={obv_boost:.2f})"
            )

            if confidence >= self.min_confidence:
                return Decision(
                    action=DecisionLogicAction.SELL,
                    confidence=confidence,
                    reason=f"RSI={rsi_value:.1f} + Envelope={envelope_position:.2f} + OBV={obv_trend}",
                    price=tick.mid,
                    timestamp=tick.timestamp.isoformat(),
                )

        # No clear signal - stay flat
        return Decision(
            action=DecisionLogicAction.FLAT,
            confidence=0.5,
            reason="No consensus signal",
            price=tick.mid,
            timestamp=tick.timestamp.isoformat(),
        )

    def _calculate_buy_confidence(
        self, rsi_value: float, envelope_position: float
    ) -> float:
        """
        Calculate buy signal confidence based on indicator extremes.

        More extreme values = higher confidence.

        Args:
            rsi_value: Current RSI value
            envelope_position: Current envelope position

        Returns:
            Confidence score (0.0 - 1.0)
        """
        # How far below oversold threshold (more extreme = higher confidence)
        rsi_strength = max(0, (self.rsi_oversold - rsi_value) / 30.0)

        # How far below envelope threshold (more extreme = higher confidence)
        envelope_strength = max(
            0, (self.envelope_lower - envelope_position) / 0.3)

        # Average the two strengths
        confidence = (rsi_strength + envelope_strength) / 2.0

        # Ensure within [0.5, 1.0] range for valid buy signals
        return max(0.5, min(1.0, 0.5 + confidence * 0.5))

    def _calculate_sell_confidence(
        self, rsi_value: float, envelope_position: float
    ) -> float:
        """
        Calculate sell signal confidence based on indicator extremes.

        More extreme values = higher confidence.

        Args:
            rsi_value: Current RSI value
            envelope_position: Current envelope position

        Returns:
            Confidence score (0.0 - 1.0)
        """
        # How far above overbought threshold
        rsi_strength = max(0, (rsi_value - self.rsi_overbought) / 30.0)

        # How far above envelope threshold
        envelope_strength = max(
            0, (envelope_position - self.envelope_upper) / 0.3)

        # Average the two strengths
        confidence = (rsi_strength + envelope_strength) / 2.0

        # Ensure within [0.5, 1.0] range for valid sell signals
        return max(0.5, min(1.0, 0.5 + confidence * 0.5))
