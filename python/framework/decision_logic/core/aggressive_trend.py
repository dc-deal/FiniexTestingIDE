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
from typing import Any, Dict, List

from python.framework.decision_logic.abstract_decision_logic import \
    AbstractDecisionLogic
from python.framework.types import Bar, Decision, TickData, WorkerResult

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

    def __init__(self, name: str = "aggressive_trend", config: Dict[str, Any] = None):
        """
        Initialize Aggressive Trend logic.

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
            List of worker names
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
        Generate aggressive trading decision with OR logic.

        Logic flow:
        1. Check worker confidence (lower threshold than conservative)
        2. Extract RSI and envelope values
        3. Apply aggressive rules:
           - BUY if RSI < 35 OR position < 0.25 (lower band)
           - SELL if RSI > 65 OR position > 0.75 (upper band)
           - Use highest confidence from triggered indicator

        Args:
            tick: Current tick data
            worker_results: Dict with RSI and Envelope results
            current_bars: Current bars (unused)
            bar_history: Historical bars (unused)

        Returns:
            Decision object with action and reasoning
        """
        # Validate workers
        self.validate_worker_results(worker_results)

        # Extract results
        rsi_result = worker_results.get("RSI")
        envelope_result = worker_results.get("Envelope")

        # Early exit if both confidences too low
        if (rsi_result.confidence < self.min_confidence and
                envelope_result.confidence < self.min_confidence):
            decision = Decision(
                action="FLAT",
                confidence=0.0,
                reason="Both indicators have low confidence",
                price=tick.mid,
                timestamp=tick.timestamp,
            )
            self._update_statistics(decision)
            return decision

        # Extract values
        rsi = rsi_result.value
        envelope = envelope_result.value
        envelope_position = envelope["position"]

        # Track which indicators triggered
        rsi_triggered = False
        envelope_triggered = False
        action = "FLAT"
        reason_parts = []

        # BUY CONDITIONS (OR logic)
        if rsi < self.rsi_buy:
            action = "BUY"
            reason_parts.append(f"RSI bullish ({rsi:.1f} < {self.rsi_buy})")
            rsi_triggered = True

        if envelope_position < self.envelope_extremes:
            if action != "BUY":  # Don't override RSI signal
                action = "BUY"
            reason_parts.append(
                f"price at lower extreme (pos: {envelope_position:.2f})"
            )
            envelope_triggered = True

        # SELL CONDITIONS (OR logic) - only if no buy signal
        if action != "BUY":
            if rsi > self.rsi_sell:
                action = "SELL"
                reason_parts.append(
                    f"RSI bearish ({rsi:.1f} > {self.rsi_sell})")
                rsi_triggered = True

            if envelope_position > (1.0 - self.envelope_extremes):
                if action != "SELL":
                    action = "SELL"
                reason_parts.append(
                    f"price at upper extreme (pos: {envelope_position:.2f})"
                )
                envelope_triggered = True

        # Calculate confidence based on which indicators triggered
        if action == "FLAT":
            confidence = 0.3
            reason = "No clear trend signal"
        else:
            # Use max confidence from triggered indicators
            triggered_confidences = []
            if rsi_triggered:
                triggered_confidences.append(rsi_result.confidence)
            if envelope_triggered:
                triggered_confidences.append(envelope_result.confidence)

            confidence = max(
                triggered_confidences) if triggered_confidences else 0.5
            reason = " OR ".join(reason_parts)

        # Create decision
        decision = Decision(
            action=action,
            confidence=confidence,
            reason=reason,
            price=tick.mid,
            timestamp=tick.timestamp,
            metadata={
                "strategy": "aggressive_trend",
                "rsi": rsi,
                "rsi_triggered": rsi_triggered,
                "envelope_position": envelope_position,
                "envelope_triggered": envelope_triggered,
                "indicators_triggered": len(reason_parts),
            },
        )

        # Update statistics
        self._update_statistics(decision)

        # Log aggressive signals
        if action != "FLAT":
            vLog.info(
                f"⚡ AGGRESSIVE {action}: {reason} "
                f"(confidence: {confidence:.2f})"
            )

        return decision
