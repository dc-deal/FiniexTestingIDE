"""
FiniexTestingIDE - Aggressive Trend Decision Logic (REFACTORED)
Alternative implementation demonstrating different trading philosophy

REFACTORED:
- Implements get_required_order_types() → [OrderType.MARKET]
- Implements execute_decision() → Market orders with margin checks
- Uses DecisionTradingAPI instead of TradeSimulator directly

This logic is more aggressive than SimpleConsensus:
- Acts on single indicator signals (no consensus needed)
- Uses wider RSI thresholds (35/65 instead of 30/70)
- Generates more signals, higher risk/reward

Strategy Rules:
- BUY when RSI < 35 OR price below lower envelope
- SELL when RSI > 65 OR price above upper envelope
- Uses OR logic instead of AND (more aggressive)

Trading Rules (NEW):
- Market orders only (MVP)
- Check free margin before trading (min 1000 EUR)
- Fixed lot size 0.1 (TODO: Position sizing logic)
- No SL/TP for MVP (TODO: Risk management)

This demonstrates how different DecisionLogic implementations
can use the same workers but with completely different strategies.
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


class AggressiveTrend(AbstractDecisionLogic):
    """
    Aggressive trend-following strategy using RSI and Envelope.

    Unlike SimpleConsensus, this logic:
    - Uses OR instead of AND (single indicator can trigger)
    - Wider thresholds (more signals)
    - Higher risk, potentially higher reward

    Configuration options:
    - rsi_buy_threshold: RSI level for buy signal (default: 35)
    - rsi_sell_threshold: RSI level for sell signal (default: 65)
    - envelope_extremes: How far from center to trigger (default: 0.25)
    - min_confidence: Minimum confidence required (default: 0.4)
    - min_free_margin: Minimum free margin required for trades (default: 1000)
    - lot_size: Fixed lot size for orders (default: 0.1)
    """

    def __init__(
        self,
        name: str = "aggressive_trend",
        config: Dict[str, Any] = None
    ):
        """
        Initialize Aggressive Trend logic.

        REFACTORED: No longer accepts trading_env parameter.

        Args:
            name: Logic identifier
            config: Configuration dict with thresholds
        """
        super().__init__(name, config)

        # Configuration with aggressive defaults
        self.rsi_buy = self.get_config_value("rsi_buy_threshold", 35)
        self.rsi_sell = self.get_config_value("rsi_sell_threshold", 65)
        self.envelope_extremes = self.get_config_value(
            "envelope_extremes", 0.25)
        self.min_confidence = self.get_config_value("min_confidence", 0.4)

        # Trading configuration
        self.min_free_margin = self.get_config_value("min_free_margin", 1000)
        self.lot_size = self.get_config_value("lot_size", 0.1)

        vLog.info(
            f"AggressiveTrend initialized: "
            f"RSI({self.rsi_buy}/{self.rsi_sell}), "
            f"Envelope extremes({self.envelope_extremes}), "
            f"Lots={self.lot_size}, MinMargin={self.min_free_margin}"
        )

    # ============================================
    # REFACTORED: New abstractmethods
    # ============================================

    def get_required_order_types(self) -> List[OrderType]:
        """
        Declare required order types for this strategy.

        AggressiveTrend uses only Market orders for MVP.
        Same as SimpleConsensus - demonstrates standardization.

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

        Same execution logic as SimpleConsensus:
        - Check free margin
        - Send market order
        - Log results

        This shows how execution logic can be standardized
        across different decision strategies.

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
                comment=f"AggressiveTrend: {decision.reason[:50]}"
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
        Declare required workers - same as SimpleConsensus.

        Both strategies use the same workers but interpret them differently.
        This shows the power of DecisionLogic abstraction.

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
        Generate trading decision using OR logic (aggressive).

        Unlike SimpleConsensus, this strategy triggers on ANY single
        indicator showing an extreme value - no consensus required.

        Args:
            tick: Current tick data
            worker_results: Results from rsi and envelope workers
            current_bars: Current bars (not used)
            bar_history: Historical bars (not used)

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
        envelope_position = envelope_data.get("position", 0.5)

        # Check for BUY signal (OR logic - either indicator is enough)
        buy_signal_rsi = rsi_value < self.rsi_buy
        buy_signal_envelope = envelope_position < self.envelope_extremes

        if buy_signal_rsi or buy_signal_envelope:
            confidence = self._calculate_buy_confidence(
                rsi_value, envelope_position, buy_signal_rsi, buy_signal_envelope
            )

            if confidence >= self.min_confidence:
                reason = self._build_buy_reason(
                    rsi_value, envelope_position, buy_signal_rsi, buy_signal_envelope
                )

                return Decision(
                    action="BUY",
                    confidence=confidence,
                    reason=reason,
                    price=tick.mid,
                    timestamp=tick.timestamp,
                )

        # Check for SELL signal (OR logic - either indicator is enough)
        sell_signal_rsi = rsi_value > self.rsi_sell
        sell_signal_envelope = envelope_position > (
            1.0 - self.envelope_extremes)

        if sell_signal_rsi or sell_signal_envelope:
            confidence = self._calculate_sell_confidence(
                rsi_value, envelope_position, sell_signal_rsi, sell_signal_envelope
            )

            if confidence >= self.min_confidence:
                reason = self._build_sell_reason(
                    rsi_value, envelope_position, sell_signal_rsi, sell_signal_envelope
                )

                return Decision(
                    action="SELL",
                    confidence=confidence,
                    reason=reason,
                    price=tick.mid,
                    timestamp=tick.timestamp,
                )

        # No signal
        return Decision(
            action="FLAT",
            confidence=0.5,
            reason="No extreme indicator values",
            price=tick.mid,
            timestamp=tick.timestamp,
        )

    def _calculate_buy_confidence(
        self,
        rsi_value: float,
        envelope_position: float,
        rsi_triggered: bool,
        envelope_triggered: bool,
    ) -> float:
        """Calculate buy signal confidence (OR logic allows partial confidence)"""
        confidence = 0.4  # Base confidence for aggressive strategy

        if rsi_triggered:
            # More extreme RSI = higher confidence
            rsi_strength = (self.rsi_buy - rsi_value) / self.rsi_buy
            confidence += rsi_strength * 0.3

        if envelope_triggered:
            # More extreme envelope = higher confidence
            env_strength = (self.envelope_extremes -
                            envelope_position) / self.envelope_extremes
            confidence += env_strength * 0.3

        return min(1.0, confidence)

    def _calculate_sell_confidence(
        self,
        rsi_value: float,
        envelope_position: float,
        rsi_triggered: bool,
        envelope_triggered: bool,
    ) -> float:
        """Calculate sell signal confidence (OR logic allows partial confidence)"""
        confidence = 0.4  # Base confidence

        if rsi_triggered:
            rsi_strength = (rsi_value - self.rsi_sell) / (100 - self.rsi_sell)
            confidence += rsi_strength * 0.3

        if envelope_triggered:
            env_threshold = 1.0 - self.envelope_extremes
            env_strength = (envelope_position - env_threshold) / \
                self.envelope_extremes
            confidence += env_strength * 0.3

        return min(1.0, confidence)

    def _build_buy_reason(
        self,
        rsi_value: float,
        envelope_position: float,
        rsi_triggered: bool,
        envelope_triggered: bool,
    ) -> str:
        """Build explanation for buy signal"""
        reasons = []

        if rsi_triggered:
            reasons.append(f"RSI={rsi_value:.1f}")

        if envelope_triggered:
            reasons.append(f"Envelope={envelope_position:.2f}")

        return " OR ".join(reasons) + " (aggressive)"

    def _build_sell_reason(
        self,
        rsi_value: float,
        envelope_position: float,
        rsi_triggered: bool,
        envelope_triggered: bool,
    ) -> str:
        """Build explanation for sell signal"""
        reasons = []

        if rsi_triggered:
            reasons.append(f"RSI={rsi_value:.1f}")

        if envelope_triggered:
            reasons.append(f"Envelope={envelope_position:.2f}")

        return " OR ".join(reasons) + " (aggressive)"
