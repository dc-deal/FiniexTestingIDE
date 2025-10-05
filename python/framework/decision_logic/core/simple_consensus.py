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

from python.components.logger.bootstrap_logger import setup_logging
from typing import Any, Dict, List

from python.framework.decision_logic.abstract_decision_logic import \
    AbstractDecisionLogic
from python.framework.types import Bar, Decision, TickData, WorkerResult

from python.framework.trading_env.trade_simulator import TradeSimulator

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
    """

    def __init__(
        self,
        name: str = "simple_consensus",
        config: Dict[str, Any] = None,
        trading_env: TradeSimulator = None
    ):
        """
        Initialize Simple Consensus logic.

        Args:
            name: Logic identifier
            config: Configuration dict with thresholds
            trading_env: TradeSimulator instance (NEW in C#003)
        """
        super().__init__(name, config, trading_env)

        # Configuration with defaults
        self.rsi_oversold = self.get_config_value("rsi_oversold", 30)
        self.rsi_overbought = self.get_config_value("rsi_overbought", 70)
        self.envelope_lower = self.get_config_value(
            "envelope_lower_threshold", 0.3)
        self.envelope_upper = self.get_config_value(
            "envelope_upper_threshold", 0.7)
        self.min_confidence = self.get_config_value("min_confidence", 0.5)

        vLog.debug(
            f"SimpleConsensus initialized: "
            f"RSI({self.rsi_oversold}/{self.rsi_overbought}), "
            f"Envelope({self.envelope_lower}/{self.envelope_upper})"
        )

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
