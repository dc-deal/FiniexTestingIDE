"""
FiniexTestingIDE - Simple Consensus Decision Logic (REFACTORED)
Reference implementation of RSI + Envelope consensus strategy

REFACTORED:
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

from python.framework.decision_logic.abstract_decision_logic import \
    AbstractDecisionLogic
from python.framework.types.global_types import Bar, Decision, TickData, WorkerResult
from python.framework.trading_env.order_types import (
    OrderStatus,
    OrderType,
    OrderDirection,
    OrderResult
)

from python.components.logger.bootstrap_logger import get_logger
vLog = get_logger()


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
        new_direction = OrderDirection.BUY if decision.action == "BUY" else OrderDirection.SELL
        new_direction_str = self._normalize_direction(new_direction)

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
                comment=f"SimpleConsensus: {decision.reason[:50]}"
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
                    action="SELL",
                    confidence=confidence,
                    reason=f"RSI={rsi_value:.1f} (overbought) + Envelope={envelope_position:.2f} (upper)",
                    price=tick.mid,
                    timestamp=tick.timestamp.isoformat(),
                )

        # No clear signal - stay flat
        return Decision(
            action="FLAT",
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
