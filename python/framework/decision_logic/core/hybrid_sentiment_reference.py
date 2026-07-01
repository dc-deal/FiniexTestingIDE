"""
FiniexTestingIDE - Hybrid Sentiment Reference Decision Logic
Didactic example: fuse an INDICATOR (RSI) with a SIGNAL worker (LLM sentiment, #141).

This is a deliberately mechanical reference that demonstrates how a decision logic
combines a price indicator with pre-collected sentiment — NOT a profitable strategy
and not a claim of one. The RSI drives the core BUY/SELL/FLAT signal; sentiment boosts
confidence when aligned, blocks the signal when strongly opposed, and is ignored when
stale or unavailable (pure indicator mode). Long-only (spot-friendly).
"""

import traceback
from typing import Any, Dict, List, Optional

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.decision_logic_types import AwarenessLevel, Decision, DecisionLogicAction
from python.framework.types.market_types.market_types import TradingContext
from python.framework.types.parameter_types import InputParamDef, OutputParamDef
from python.framework.types.component_metadata_types import ComponentMetadata
from python.framework.types.worker_types import WorkerRequirement, WorkerResult
from python.framework.types.trading_env_types.order_types import (
    OrderDirection,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
)


class HybridSentimentReference(AbstractDecisionLogic):
    """
    Reference hybrid logic: RSI core gated/boosted by LLM sentiment.

    Configuration:
    - rsi_buy_threshold / rsi_sell_threshold: RSI levels for the core signal
    - min_sentiment_confidence: below this (or when stale) sentiment is ignored
    - sentiment_conflict_threshold: opposing sentiment magnitude that blocks a signal
    - sentiment_boost: confidence added when sentiment aligns with the signal
    - lot_size / min_free_margin: execution sizing + margin floor
    """

    def __init__(
        self,
        name,
        logger: ScenarioLogger,
        config,
        trading_context: TradingContext = None
    ):
        """
        Initialize hybrid sentiment reference logic.

        Args:
            name: Logic identifier
            logger: ScenarioLogger
            config: Configuration dict (schema-validated)
            trading_context: TradingContext
        """
        super().__init__(name, logger, config, trading_context=trading_context)

        self.rsi_buy = self.params.get('rsi_buy_threshold')
        self.rsi_sell = self.params.get('rsi_sell_threshold')
        self.min_sentiment_confidence = self.params.get('min_sentiment_confidence')
        self.sentiment_conflict_threshold = self.params.get('sentiment_conflict_threshold')
        self.sentiment_boost = self.params.get('sentiment_boost')
        self.lot_size = self.params.get('lot_size')
        self.min_free_margin = self.params.get('min_free_margin')

    # ============================================
    # Schema + metadata
    # ============================================

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, InputParamDef]:
        """Hybrid sentiment reference parameters."""
        return {
            'rsi_buy_threshold': InputParamDef(
                param_type=float, default=30, min_val=1, max_val=49,
                description='RSI threshold for the core buy signal',
                display=True, display_label='rsi_b',
            ),
            'rsi_sell_threshold': InputParamDef(
                param_type=float, default=70, min_val=51, max_val=99,
                description='RSI threshold for the core sell (exit) signal',
                display=True, display_label='rsi_s',
            ),
            'min_sentiment_confidence': InputParamDef(
                param_type=float, default=0.3, min_val=0.0, max_val=1.0,
                description='Sentiment confidence below which sentiment is ignored',
                display=True, display_label='min_sent',
            ),
            'sentiment_conflict_threshold': InputParamDef(
                param_type=float, default=0.3, min_val=0.0, max_val=1.0,
                description='Opposing sentiment magnitude that blocks the signal',
            ),
            'sentiment_boost': InputParamDef(
                param_type=float, default=0.3, min_val=0.0, max_val=1.0,
                description='Confidence added when sentiment aligns with the signal',
            ),
            'lot_size': InputParamDef(
                param_type=float, default=0.1, min_val=0.0, max_val=100.0,
                description='Fixed lot size for market orders',
            ),
            'min_free_margin': InputParamDef(
                param_type=float, default=1000, min_val=0,
                description='Minimum free margin required before opening a position',
            ),
        }

    @classmethod
    def get_output_schema(cls) -> Dict[str, OutputParamDef]:
        """Hybrid decision output parameters."""
        return {
            'confidence': OutputParamDef(
                param_type=float, min_val=0.0, max_val=1.0,
                description='Signal confidence after sentiment fusion',
                category='SIGNAL', display=True,
            ),
            'reason': OutputParamDef(
                param_type=str,
                description='Human-readable decision explanation',
                category='INFO',
            ),
            'price': OutputParamDef(
                param_type=float, min_val=0.0,
                description='Price at decision time',
                category='INFO',
            ),
            'timestamp': OutputParamDef(
                param_type=str,
                description='ISO format UTC timestamp at decision time',
                category='INFO',
            ),
        }

    @classmethod
    def get_metadata(cls) -> ComponentMetadata:
        """CORE reference decision logic metadata (crypto sentiment demo)."""
        return ComponentMetadata(
            version='1.0.0',
            doc_link='docs/user_guides/worker_naming_doc.md',
            recommended_markets=('crypto',),
        )

    @classmethod
    def get_required_order_types(cls, decision_logic_config: Dict[str, Any]) -> List[OrderType]:
        """Market orders only (long-only spot demo)."""
        return [OrderType.MARKET]

    def get_required_workers(self) -> Dict[str, WorkerRequirement]:
        """
        Declare worker instances + the signals this logic reads (#425).

        An RSI (core signal) and the LLM sentiment SIGNAL worker (overlay). Only
        the RSI value and the sentiment score / confidence / staleness are read.

        Returns:
            Dict[instance_name, WorkerRequirement]
        """
        return {
            'rsi_fast': WorkerRequirement.of('CORE/rsi', 'rsi_value'),
            'sentiment': WorkerRequirement.of(
                'CORE/llm_sentiment', 'sentiment_score', 'confidence', 'is_stale'),
        }

    # ============================================
    # Decision
    # ============================================

    def compute_tick(
        self,
        tick: TickData,
        worker_results: Dict[str, WorkerResult],
    ) -> Decision:
        """
        Fuse the RSI core signal with the sentiment overlay.

        RSI sets the base action (BUY below buy threshold, SELL/exit above sell
        threshold). Usable sentiment (present, not stale, confidence high enough)
        boosts confidence when aligned and blocks the signal when strongly opposed;
        otherwise the decision is pure indicator.

        Args:
            tick: Current tick
            worker_results: Results keyed by instance name (rsi_fast, sentiment)

        Returns:
            Decision with action, fused confidence, and reason
        """
        rsi_result = worker_results.get('rsi_fast')
        if not rsi_result:
            return self._flat(tick, 0.0, 'Missing RSI worker result')

        rsi_value = rsi_result.get_signal('rsi_value')

        if rsi_value < self.rsi_buy:
            base_action, base_confidence = DecisionLogicAction.BUY, 0.5
        elif rsi_value > self.rsi_sell:
            base_action, base_confidence = DecisionLogicAction.SELL, 0.5
        else:
            return self._flat(tick, 0.5, f'No RSI edge (rsi={rsi_value:.1f})')

        score, usable = self._read_sentiment(worker_results.get('sentiment'))

        if usable:
            aligned = (
                (base_action == DecisionLogicAction.BUY and score > 0) or
                (base_action == DecisionLogicAction.SELL and score < 0)
            )
            opposed = (
                (base_action == DecisionLogicAction.BUY and score < -self.sentiment_conflict_threshold) or
                (base_action == DecisionLogicAction.SELL and score > self.sentiment_conflict_threshold)
            )
            if opposed:
                self.notify_awareness(
                    f'Sentiment blocks {base_action.value} (score={score:+.2f})',
                    AwarenessLevel.NOTICE, 'sentiment_block')
                return self._flat(
                    tick, 0.3,
                    f'{base_action.value} blocked by opposing sentiment {score:+.2f}')
            if aligned:
                base_confidence = min(1.0, base_confidence + abs(score) * self.sentiment_boost)
                reason = f'{base_action.value} rsi={rsi_value:.1f} + sentiment {score:+.2f}'
            else:
                reason = f'{base_action.value} rsi={rsi_value:.1f} (sentiment neutral)'
        else:
            reason = f'{base_action.value} rsi={rsi_value:.1f} (indicator only)'

        self.notify_awareness(reason, AwarenessLevel.INFO, 'hybrid_signal')
        return Decision(
            action=base_action,
            outputs={
                'confidence': base_confidence,
                'reason': reason,
                'price': tick.mid,
                'timestamp': tick.timestamp.isoformat(),
            },
        )

    def _read_sentiment(self, sentiment_result: Optional[WorkerResult]) -> tuple:
        """
        Read the usable sentiment score from the SIGNAL worker result.

        Args:
            sentiment_result: The sentiment worker result, or None if absent

        Returns:
            Tuple (sentiment_score, usable) — usable is False when the result is
            missing, stale, or below min_sentiment_confidence
        """
        if not sentiment_result:
            return 0.0, False
        confidence = sentiment_result.get_signal('confidence')
        is_stale = sentiment_result.get_signal('is_stale')
        score = sentiment_result.get_signal('sentiment_score')
        usable = (not is_stale) and confidence >= self.min_sentiment_confidence
        return score, usable

    def _flat(self, tick: TickData, confidence: float, reason: str) -> Decision:
        """Build a FLAT decision with the given confidence and reason."""
        return Decision(
            action=DecisionLogicAction.FLAT,
            outputs={
                'confidence': confidence,
                'reason': reason,
                'price': tick.mid,
                'timestamp': tick.timestamp.isoformat(),
            },
        )

    # ============================================
    # Execution (long-only spot demo)
    # ============================================

    def _execute_decision_impl(
        self,
        decision: Decision,
        tick: TickData
    ) -> Optional[OrderResult]:
        """
        Execute the decision: BUY opens a long, SELL exits it (one position).

        Args:
            decision: Decision from compute_tick
            tick: Current tick

        Returns:
            OrderResult if an order was sent, else None
        """
        if not self.trading_api:
            return None
        if self.trading_api.has_pending_orders():
            return None

        open_positions = self.trading_api.get_open_positions()

        if decision.action == DecisionLogicAction.BUY:
            if open_positions:
                return None  # already long
            account = self.trading_api.get_account_info(OrderDirection.LONG)
            if account.free_margin < self.min_free_margin:
                return None
            try:
                order_result = self.trading_api.send_order(
                    symbol=tick.symbol,
                    order_type=OrderType.MARKET,
                    side=OrderSide.BUY,
                    lots=self.lot_size,
                    comment='HybridSentiment: open long',
                )
                if order_result.status == OrderStatus.PENDING:
                    self.emit_event(
                        f'Long opened: {self.lot_size} lots',
                        AwarenessLevel.INFO, 'order_submitted')
                return order_result
            except Exception:
                self.logger.error(
                    f'❌ Order execution failed: \n{traceback.format_exc()}')
                return None

        if decision.action == DecisionLogicAction.SELL and open_positions:
            self.trading_api.close_position(open_positions[0].position_id)

        return None
