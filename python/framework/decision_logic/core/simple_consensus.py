"""
FiniexTestingIDE - Simple Consensus Decision Logic
Reference implementation of RSI + Envelope consensus strategy

This is the default decision logic provided by the framework.
It demonstrates the separation between worker coordination and
decision-making strategy.

Strategy Rules:
- BUY when RSI oversold (≤30) AND price near lower envelope (≤30%)
- SELL when RSI overbought (≥70) AND price near upper envelope (≥70%)
- FLAT otherwise (no clear signal)

This logic requires two workers:
- RSI: Relative Strength Index indicator
- Envelope: Price envelope/bollinger bands
"""

import logging
from typing import Any, Dict, List

from python.framework.decision_logic.abstract_decision_logic import \
    AbstractDecisionLogic
from python.framework.types import Bar, Decision, TickData, WorkerResult

logger = logging.getLogger(__name__)


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
    """

    def __init__(self, name: str = "simple_consensus", config: Dict[str, Any] = None):
        """
        Initialize Simple Consensus logic.

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

        logger.debug(
            f"SimpleConsensus initialized: "
            f"RSI({self.rsi_oversold}/{self.rsi_overbought}), "
            f"Envelope({self.envelope_lower}/{self.envelope_upper})"
        )

    def get_required_workers(self) -> List[str]:
        """
        Declare required workers for this strategy.

        Simple Consensus needs:
        - RSI: For overbought/oversold detection
        - Envelope: For price position relative to bands

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
        Generate trading decision based on RSI + Envelope consensus.

        Logic flow:
        1. Check if both workers have sufficient confidence
        2. Extract RSI value and envelope position
        3. Apply consensus rules:
           - BUY: RSI oversold AND price near lower band
           - SELL: RSI overbought AND price near upper band
           - FLAT: No consensus or insufficient confidence

        Args:
            tick: Current tick data
            worker_results: Dict with RSI and Envelope results
            current_bars: Current bars (not used in this logic)
            bar_history: Historical bars (not used in this logic)

        Returns:
            Decision object with action, confidence, and reasoning
        """
        # Validate that we have all required workers
        # (This is done by orchestrator, but we double-check)
        self.validate_worker_results(worker_results)

        # Extract worker results
        rsi_result = worker_results.get("RSI")
        envelope_result = worker_results.get("Envelope")

        # Check confidence levels
        if rsi_result.confidence < self.min_confidence:
            decision = Decision(
                action="FLAT",
                confidence=0.0,
                reason="RSI confidence too low",
                price=tick.mid,
                timestamp=tick.timestamp,
            )
            self._update_statistics(decision)
            return decision

        if envelope_result.confidence < self.min_confidence:
            decision = Decision(
                action="FLAT",
                confidence=0.0,
                reason="Envelope confidence too low",
                price=tick.mid,
                timestamp=tick.timestamp,
            )
            self._update_statistics(decision)
            return decision

        # Extract indicator values
        rsi = rsi_result.value
        envelope = envelope_result.value
        envelope_position = envelope["position"]

        # Initialize decision variables
        action = "FLAT"
        reason = "No clear signal"
        confidence = 0.5

        # BUY SIGNAL: RSI oversold + price near lower band
        if rsi <= self.rsi_oversold and envelope_position <= self.envelope_lower:
            action = "BUY"
            reason = (
                f"RSI oversold ({rsi:.1f} ≤ {self.rsi_oversold}) + "
                f"price near lower band (position: {envelope_position:.2f})"
            )
            # Confidence: Average of both worker confidences
            confidence = min(rsi_result.confidence, envelope_result.confidence)

        # SELL SIGNAL: RSI overbought + price near upper band
        elif rsi >= self.rsi_overbought and envelope_position >= self.envelope_upper:
            action = "SELL"
            reason = (
                f"RSI overbought ({rsi:.1f} ≥ {self.rsi_overbought}) + "
                f"price near upper band (position: {envelope_position:.2f})"
            )
            # Confidence: Average of both worker confidences
            confidence = min(rsi_result.confidence, envelope_result.confidence)

        # Create decision object
        decision = Decision(
            action=action,
            confidence=confidence,
            reason=reason,
            price=tick.mid,
            timestamp=tick.timestamp,
            metadata={
                "rsi": rsi,
                "envelope_position": envelope_position,
                "envelope_upper": envelope["upper"],
                "envelope_lower": envelope["lower"],
                "envelope_middle": envelope["middle"],
            },
        )

        # Update statistics
        self._update_statistics(decision)

        # Log significant signals (not FLAT)
        if action != "FLAT":
            logger.debug(
                f"🎯 {action} signal: {reason} (confidence: {confidence:.2f})"
            )

        return decision
