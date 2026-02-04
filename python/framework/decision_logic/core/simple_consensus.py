"""
FiniexTestingIDE - Simple Consensus Decision Logic ()
Reference implementation of RSI + Envelope consensus strategy

:
- Implements get_required_order_types() → [OrderType.MARKET]
- Implements execute_decision() → Market orders with margin checks
- Uses DecisionTradingAPI instead of TradeSimulator directly
- ONE POSITION ONLY: Closes existing position before opening new one

This is the default decision logic provided by the framework.
It demonstrates the separation between worker coordination and
decision-making strategy AND trade execution.

Strategy Rules:
- BUY when RSI oversold (≤30) AND price near lower envelope (≤30%)
- SELL when RSI overbought (≥70) AND price near upper envelope (≥70%)
- FLAT otherwise (no clear signal)

Trading Rules (NEW):
- Market orders only (MVP)
- Check free margin before trading (min 1000 EUR)
- Fixed lot size 0.1 (TODO: Position sizing logic)
- No SL/TP for MVP (TODO: Risk management)
- ONE POSITION ONLY: Maximum one position at a time

Position Management:
- FLAT signal → Close existing position
- Same direction signal → Skip (already have position)
- Opposite direction signal → Close old, open new (reversal)

This logic requires two workers:
- RSI: Relative Strength Index indicator
- Envelope: Price envelope/bollinger bands
"""

import traceback
from typing import Any, Dict, List, Optional

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.decision_logic.abstract_decision_logic import \
    AbstractDecisionLogic
from python.framework.types.market_data_types import Bar, TickData
from python.framework.types.decision_logic_types import Decision, DecisionLogicAction

from python.framework.types.market_types import TradingContext
from python.framework.types.order_types import (
    OrderStatus,
    OrderType,
    OrderDirection,
    OrderResult
)
from python.framework.types.worker_types import WorkerResult


class SimpleConsensus(AbstractDecisionLogic):
    """
    Simple consensus strategy using RSI and Envelope indicators.

    This is a conservative strategy that requires confirmation from
    both indicators before generating buy/sell signals.

    Configuration options:
    - rsi_oversold: RSI threshold for oversold (default: 30)
    - rsi_overbought: RSI threshold for overbought (default: 70)
    - envelope_lower_threshold: Price position threshold for buy (default: 0.3)
    - envelope_upper_threshold: Price position threshold for sell (default: 0.7)
    - min_confidence: Minimum confidence to generate signal (default: 0.5)
    - min_free_margin: Minimum free margin required for trades (default: 1000)
    - lot_size: Fixed lot size for orders (default: 0.1)
    """

    def __init__(
        self,
        name,
        logger: ScenarioLogger,
        config: Dict[str, Any],
        trading_context: TradingContext = None
    ):
        """
        Initialize Simple Consensus logic.

        No longer accepts trading_env parameter.

        Args:
            name: Logic identifier
            config: Configuration dict with thresholds
        """
        super().__init__(name, logger, config)

        # Configuration with defaults
        self.rsi_oversold = self.get_config_value("rsi_oversold", 30)
        self.rsi_overbought = self.get_config_value("rsi_overbought", 70)
        self.envelope_lower = self.get_config_value(
            "envelope_lower_threshold", 0.3)
        self.envelope_upper = self.get_config_value(
            "envelope_upper_threshold", 0.7)
        self.min_confidence = self.get_config_value("min_confidence", 0.5)

        # Trading configuration
        self.min_free_margin = self.get_config_value("min_free_margin", 1000)
        self.lot_size = self.get_config_value("lot_size", 0.1)

        self.logger.debug(
            f"SimpleConsensus initialized: "
            f"RSI({self.rsi_oversold}/{self.rsi_overbought}), "
            f"Envelope({self.envelope_lower}/{self.envelope_upper}), "
            f"Lots={self.lot_size}, MinMargin={self.min_free_margin}"
        )

    # ============================================
    # New abstractmethods
    # ============================================

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
        # Get BOTH positions AND pending orders
        # ============================================
        open_positions = self.trading_api.get_open_positions()

        # Note: get_open_positions() already filters out positions being closed.
        # Latency simulation is handled internally by TradeSimulator.

        new_direction = OrderDirection.LONG if decision.action == DecisionLogicAction.BUY else OrderDirection.SHORT

        # ============================================
        # STEP 1: Handle FLAT signal (exit strategy)
        # ============================================
        if decision.action == DecisionLogicAction.FLAT:
            if len(open_positions) > 0:
                position = open_positions[0]
                self.logger.info(
                    f"📍 FLAT signal - closing {position.direction} position "
                    f"(ID: {position.position_id})"
                )
                return self.trading_api.close_position(position.position_id)
            # No position to close, nothing to do
            return None

        # ============================================
        # STEP 2: Check if we already have a position
        # ============================================
        new_direction = OrderDirection.LONG if decision.action == DecisionLogicAction.BUY else OrderDirection.SHORT

        if len(open_positions) > 0:
            current_position = open_positions[0]
            if (current_position.pending):
                # waiting for full close (or open)!
                return
            # Same direction? Skip (we already have what the strategy wants)
            if current_position.direction == new_direction:
                # self.logger.debug(
                #     f"⏭️  Already holding {new_direction} position "
                #     f"(ID: {current_position.position_id}) - skipping duplicate signal"
                # )
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
        Define required worker instances for AggressiveTrend strategy.

        Requires:
        - rsi_fast: Fast RSI indicator for trend detection
        - envelope_main: Envelope for price position analysis

        Returns:
            Dict[instance_name, worker_type]
        """
        return {
            "rsi_fast": "CORE/rsi",
            "envelope_main": "CORE/envelope"
        }

    def compute(
        self,
        tick: TickData,
        worker_results: Dict[str, WorkerResult],
    ) -> Decision:
        """
        Generate trading decision based on consensus between RSI and Envelope.

        This is a conservative strategy - BOTH indicators must agree before
        generating a buy/sell signal.

        Args:
            tick: Current tick data
            worker_results: Results from rsi and envelope workers

        Returns:
            Decision object with action, confidence, and reason
        """
        # Extract worker results
        rsi_result = worker_results.get("rsi_fast")
        envelope_result = worker_results.get("envelope_main")

        if not rsi_result or not envelope_result:
            return Decision(
                action=DecisionLogicAction.FLAT,
                confidence=0.0,
                reason="Missing worker results",
                price=tick.mid,
                timestamp=tick.timestamp.isoformat(),
            )

        # Extract indicator values
        rsi_value = rsi_result.value
        envelope_data = envelope_result.value

        # Envelope provides position (0.0 = lower band, 1.0 = upper band)
        envelope_position = envelope_data.get("position", 0.5)

        # Check for BUY signal (consensus required)
        if (
            rsi_value <= self.rsi_oversold
            and envelope_position <= self.envelope_lower
        ):
            confidence = self._calculate_buy_confidence(
                rsi_value, envelope_position)

            if confidence >= self.min_confidence:
                return Decision(
                    action=DecisionLogicAction.BUY,
                    confidence=confidence,
                    reason=f"RSI={rsi_value:.1f} (oversold) + Envelope={envelope_position:.2f} (lower)",
                    price=tick.mid,
                    timestamp=tick.timestamp.isoformat(),
                )

        # Check for SELL signal (consensus required)
        if (
            rsi_value >= self.rsi_overbought
            and envelope_position >= self.envelope_upper
        ):
            confidence = self._calculate_sell_confidence(
                rsi_value, envelope_position)

            if confidence >= self.min_confidence:
                return Decision(
                    action=DecisionLogicAction.SELL,
                    confidence=confidence,
                    reason=f"RSI={rsi_value:.1f} (overbought) + Envelope={envelope_position:.2f} (upper)",
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
