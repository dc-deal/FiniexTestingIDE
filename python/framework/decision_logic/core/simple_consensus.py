"""
FiniexTestingIDE - Simple Consensus Decision Logic (REFACTORED)
Reference implementation of RSI + Envelope consensus strategy

REFACTORED:
- Implements get_required_order_types() → [OrderType.MARKET]
- Implements execute_decision() → Market orders with margin checks
- Uses DecisionTradingAPI instead of TradeSimulator directly

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

This logic requires two workers:
- RSI: Relative Strength Index indicator
- Envelope: Price envelope/bollinger bands
"""

import traceback
from python.components.logger.bootstrap_logger import setup_logging
from typing import Any, Dict, List, Optional

from python.framework.decision_logic.abstract_decision_logic import \
    AbstractDecisionLogic
from python.framework.types import Bar, Decision, TickData, WorkerResult
from python.framework.trading_env.order_types import (
    OrderType,
    OrderDirection,
    OrderResult
)

vLog = setup_logging(name="StrategyRunner")


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
        name: str = "simple_consensus",
        config: Dict[str, Any] = None
    ):
        """
        Initialize Simple Consensus logic.

        REFACTORED: No longer accepts trading_env parameter.

        Args:
            name: Logic identifier
            config: Configuration dict with thresholds
        """
        super().__init__(name, config)

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

        vLog.debug(
            f"SimpleConsensus initialized: "
            f"RSI({self.rsi_oversold}/{self.rsi_overbought}), "
            f"Envelope({self.envelope_lower}/{self.envelope_upper}), "
            f"Lots={self.lot_size}, MinMargin={self.min_free_margin}"
        )

    # ============================================
    # REFACTORED: New abstractmethods
    # ============================================

    def get_required_order_types(self) -> List[OrderType]:
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

        Called by execute_decision() template method.
        Statistics are updated automatically after this returns.

        Checks account state before placing orders:
        - Free margin must be >= min_free_margin
        - Only executes BUY/SELL decisions (ignores FLAT)

        MVP: Fixed lot size, no SL/TP, no position sizing.
        Post-MVP: Position sizing, risk management, SL/TP.

        Args:
            decision: Decision object from compute()
            tick: Current tick data

        Returns:
            OrderResult if order was sent, None if no trade
        """
        # Only trade on BUY/SELL signals
        if decision.action == "FLAT":
            return None

        # Check if trading API is available
        if not self.trading_api:
            vLog.warning("Trading API not available - cannot execute decision")
            return None

        # Check account state
        account = self.trading_api.get_account_info()

        if account.free_margin < self.min_free_margin:
            vLog.debug(
                f"Insufficient free margin: {account.free_margin:.2f} "
                f"< {self.min_free_margin} - skipping trade"
            )
            return None

        # Determine order direction
        direction = OrderDirection.BUY if decision.action == "BUY" else OrderDirection.SELL

        # Send market order
        try:
            order_result = self.trading_api.send_order(
                symbol=tick.symbol,
                order_type=OrderType.MARKET,
                direction=direction,
                lots=self.lot_size,
                # Truncate reason
                comment=f"SimpleConsensus: {decision.reason[:50]}"
            )

            if order_result.is_success:
                vLog.debug(
                    f"✓ Order executed: {direction.value} {self.lot_size} lots "
                    f"@ {order_result.executed_price:.5f} (ID: {order_result.order_id})"
                )
            else:
                vLog.warning(
                    f"✗ Order rejected: {order_result.rejection_reason.value if order_result.rejection_reason else 'Unknown'} - "
                    f"{order_result.rejection_message}"
                )

            return order_result

        except Exception as e:
            vLog.error(f"❌ Order execution failed: \n{traceback.format_exc()}")
            return None

    # ============================================
    # Existing methods (unchanged)
    # ============================================

    def get_required_workers(self) -> List[str]:
        """
        Declare required workers for this strategy.

        SimpleConsensus needs both RSI and Envelope for consensus.

        Returns:
            List of worker names required
        """
        return ["RSI", "Envelope"]

    def compute(
        self,
        tick: TickData,
        worker_results: Dict[str, WorkerResult],
        current_bars: Dict[str, Bar],
        bar_history: Dict[str, List[Bar]],
    ) -> Decision:
        """
        Generate trading decision based on consensus between RSI and Envelope.

        This is a conservative strategy - BOTH indicators must agree before
        generating a buy/sell signal.

        Args:
            tick: Current tick data
            worker_results: Results from rsi and envelope workers
            current_bars: Current bars (not used in simple strategy)
            bar_history: Historical bars (not used in simple strategy)

        Returns:
            Decision object with action, confidence, and reason
        """
        # Extract worker results
        rsi_result = worker_results.get("RSI")
        envelope_result = worker_results.get("Envelope")

        if not rsi_result or not envelope_result:
            return Decision(
                action="FLAT",
                confidence=0.0,
                reason="Missing worker results",
                price=tick.mid,
                timestamp=tick.timestamp,
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
                    action="BUY",
                    confidence=confidence,
                    reason=f"RSI={rsi_value:.1f} (oversold) + Envelope={envelope_position:.2f} (lower)",
                    price=tick.mid,
                    timestamp=tick.timestamp,
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
                    action="SELL",
                    confidence=confidence,
                    reason=f"RSI={rsi_value:.1f} (overbought) + Envelope={envelope_position:.2f} (upper)",
                    price=tick.mid,
                    timestamp=tick.timestamp,
                )

        # No clear signal - stay flat
        return Decision(
            action="FLAT",
            confidence=0.5,
            reason="No consensus signal",
            price=tick.mid,
            timestamp=tick.timestamp,
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
