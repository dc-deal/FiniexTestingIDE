# ============================================
# python/framework/decision_logic/core/aggressive_trend.py
# ============================================
"""
FiniexTestingIDE - Aggressive Trend Decision Logic (REFACTORED + FIXED)
Alternative implementation demonstrating different trading philosophy

REFACTORED:
- Implements get_required_order_types() → [OrderType.MARKET]
- Implements execute_decision() → Market orders with margin checks
- Uses DecisionTradingAPI instead of TradeSimulator directly
- ONE POSITION ONLY: Closes existing position before opening new one

FIXED (Issue: Duplicate Orders):
- Now checks BOTH open_positions AND pending_orders
- Prevents duplicate order submissions during execution delays
- Uses _normalize_direction() for robust direction comparison

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
- ONE POSITION ONLY: Maximum one position at a time

Position Management:
- FLAT signal → Close existing position
- Same direction signal → Skip (already have position)
- Opposite direction signal → Close old, open new (reversal)

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
    OrderStatus,
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

        vLog.debug(
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

    def _normalize_direction(self, direction) -> str:
        """
        Helper: Normalize direction to string for comparison.

        Handles both OrderDirection enum and string types robustly.
        This fixes the issue where position.direction can be either type.

        Args:
            direction: Either OrderDirection enum or string

        Returns:
            Direction as string ("BUY" or "SELL")
        """
        if isinstance(direction, str):
            return direction.upper()
        elif isinstance(direction, OrderDirection):
            return direction.value.upper()
        else:
            return str(direction).upper()

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

        CRITICAL FIX: Now checks BOTH open positions AND pending orders
        to prevent duplicate order submissions during execution delays!

        Args:
            decision: Decision object from compute()
            tick: Current tick data

        Returns:
            OrderResult if order was sent, None if no trade
        """
        # Check if trading API is available
        if not self.trading_api:
            vLog.warning("Trading API not available - cannot execute decision")
            return None

        # ============================================
        # NEW: Get BOTH positions AND pending orders
        # ============================================
        open_positions = self.trading_api.get_open_positions()
        pending_orders = self.trading_api.get_pending_orders()

        # CRITICAL: Check if we have pending orders for same direction
        # This prevents duplicate submissions during execution delay!
        new_direction = OrderDirection.BUY if decision.action == "BUY" else OrderDirection.SELL
        new_direction_str = self._normalize_direction(new_direction)

        # Check if we already have a pending order for this direction
        for pending in pending_orders:
            pending_dir = self._normalize_direction(pending["direction"])
            if pending_dir == new_direction_str:
                return None

        # ============================================
        # STEP 1: Handle FLAT signal (exit strategy)
        # ============================================
        if decision.action == "FLAT":
            if len(open_positions) > 0:
                position = open_positions[0]
                position_dir_str = self._normalize_direction(
                    position.direction)
                vLog.info(
                    f"📍 FLAT signal - closing {position_dir_str} position "
                    f"(ID: {position.position_id})"
                )
                return self.trading_api.close_position(position.position_id)
            # No position to close, nothing to do
            return None

        # ============================================
        # STEP 2: Check if we already have a position
        # ============================================
        if len(open_positions) > 0:
            current_position = open_positions[0]
            current_dir_str = self._normalize_direction(
                current_position.direction)

            # Same direction? Skip (we already have what the strategy wants)
            if current_dir_str == new_direction_str:
                # vLog.debug(
                #     f"⏭️  Already holding {new_direction_str} position "
                #     f"(ID: {current_position.position_id}) - skipping duplicate signal"
                # )
                return None

            # Opposite direction? Close old position (signal reversal)
            vLog.info(
                f"🔄 Signal reversal detected: {current_dir_str} → {new_direction_str}"
            )
            vLog.info(
                f"   Closing {current_dir_str} position "
                f"(ID: {current_position.position_id})"
            )
            self.trading_api.close_position(current_position.position_id)
            # Continue to open new position below

        # ============================================
        # STEP 3: Open new position
        # ============================================

        # Check account state (margin available)
        account = self.trading_api.get_account_info()

        if account.free_margin < self.min_free_margin:
            vLog.info(
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
                comment=f"AggressiveTrend: {decision.reason[:50]}"
            )

            # Log order submission status
            if order_result.status == OrderStatus.PENDING:
                vLog.info(
                    f"⏳ Order submitted: {new_direction_str} {self.lot_size} lots "
                    f"(ID: {order_result.order_id}) - awaiting execution"
                )
            elif order_result.is_rejected:
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
        rsi_result = worker_results.get("rsi_fast")
        envelope_result = worker_results.get("envelope_main")

        if not rsi_result or not envelope_result:
            return Decision(
                action="FLAT",
                confidence=0.0,
                reason="Missing worker results",
                price=tick.mid,
                timestamp=tick.timestamp.isoformat(),
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
                    timestamp=tick.timestamp.isoformat(),
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
                    timestamp=tick.timestamp.isoformat(),
                )

        # No signal
        return Decision(
            action="FLAT",
            confidence=0.5,
            reason="No extreme indicator values",
            price=tick.mid,
            timestamp=tick.timestamp.isoformat(),
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
