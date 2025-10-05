"""
FiniexTestingIDE - Aggressive Trend Decision Logic
Alternative implementation demonstrating different trading philosophy

This logic is more aggressive than SimpleConsensus:
- Acts on single indicator signals (no consensus needed)
- Uses wider RSI thresholds (35/65 instead of 30/70)
- Generates more signals, higher risk/reward

Strategy Rules:
- BUY when RSI < 35 OR price below lower envelope
- SELL when RSI > 65 OR price above upper envelope
- Uses OR logic instead of AND (more aggressive)

This demonstrates how different DecisionLogic implementations
can use the same workers but with completely different strategies.
"""

from python.components.logger.bootstrap_logger import setup_logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from python.framework.decision_logic.abstract_decision_logic import \
    AbstractDecisionLogic
from python.framework.types import Bar, Decision, TickData, WorkerResult

# Avoid circular import
if TYPE_CHECKING:
    from python.framework.trading_env.trade_simulator import TradeSimulator

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
    """

    def __init__(
        self,
        name: str = "aggressive_trend",
        config: Dict[str, Any] = None,
        trading_env: Optional['TradeSimulator'] = None
    ):
        """
        Initialize Aggressive Trend logic.

        Args:
            name: Logic identifier
            config: Configuration dict with thresholds
            trading_env: TradeSimulator instance (NEW in C#003)
        """
        super().__init__(name, config, trading_env)

        # Configuration with aggressive defaults
        self.rsi_buy = self.get_config_value("rsi_buy_threshold", 35)
        self.rsi_sell = self.get_config_value("rsi_sell_threshold", 65)
        self.envelope_extremes = self.get_config_value(
            "envelope_extremes", 0.25)
        self.min_confidence = self.get_config_value("min_confidence", 0.4)

        vLog.debug(
            f"AggressiveTrend initialized: "
            f"RSI({self.rsi_buy}/{self.rsi_sell}), "
            f"Envelope extremes({self.envelope_extremes})"
        )

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
        envelope_result = worker_results.get("envelope")

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
