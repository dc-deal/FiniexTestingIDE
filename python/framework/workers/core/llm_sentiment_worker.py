"""
FiniexTestingIDE - LLM Sentiment Worker
First SIGNAL worker: reads pre-collected LLM sentiment by timestamp (#141).
"""

from typing import Dict, Optional

from python.framework.types.component_metadata_types import ComponentMetadata
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.parameter_types import InputParamDef, OutputParamDef
from python.framework.types.signal_data_types import ResolvedSignal
from python.framework.types.worker_types import WorkerResult
from python.framework.workers.abstract_signal_worker import AbstractSignalWorker


class LlmSentimentWorker(AbstractSignalWorker):
    """
    SIGNAL worker reading pre-collected LLM sentiment snapshots.

    On each tick it resolves the most recent snapshot (collected_msc <= tick) for
    the scenario symbol and maps the per-symbol result to a WorkerResult. A gap
    (no snapshot yet) yields a neutral, zero-confidence result; an outdated
    snapshot is flagged is_stale (older than max_staleness_minutes).
    """

    SIGNAL_SOURCE = 'llm_sentiment'

    @classmethod
    def get_metadata(cls) -> ComponentMetadata:
        """CORE worker metadata (version + doc pointer + market fit)."""
        return ComponentMetadata(
            version='1.1.0',
            doc_link='docs/user_guides/worker_naming_doc.md',
            recommended_markets=('crypto',),
        )

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, InputParamDef]:
        """LLM sentiment worker parameters."""
        return {
            'max_staleness_minutes': InputParamDef(
                param_type=int,
                default=30,
                min_val=1,
                description='Snapshot age (tick − collected_msc) above which the '
                            'result is flagged is_stale',
            ),
            'data_path': InputParamDef(
                param_type=str,
                default='',
                description='Optional explicit signal archive path '
                            '(empty = auto-resolved from broker + symbol by the loader)',
            ),
        }

    @classmethod
    def get_output_schema(cls) -> Dict[str, OutputParamDef]:
        """LLM sentiment outputs."""
        return {
            'sentiment_score': OutputParamDef(
                param_type=float, min_val=-1.0, max_val=1.0,
                description='Net sentiment score (-1 bearish .. +1 bullish)',
                category='SIGNAL', display=True, display_label='sentiment',
            ),
            'confidence': OutputParamDef(
                param_type=float, min_val=0.0, max_val=1.0,
                description='Model confidence (0 when no news)',
                category='SIGNAL', display=True, display_label='conf',
            ),
            'signal': OutputParamDef(
                param_type=str, choices=('BUY', 'SELL', 'HOLD'),
                description='Discrete sentiment signal',
                category='SIGNAL', display=True, display_label='signal',
            ),
            'urgency': OutputParamDef(
                param_type=float, min_val=0.0, max_val=1.0,
                description='Breaking-news urgency (breaking gate input)',
                category='INFO',
            ),
            'is_breaking': OutputParamDef(
                param_type=bool,
                description='Whether this snapshot is a breaking-news event',
                category='INFO',
            ),
            'reasoning': OutputParamDef(
                param_type=str,
                description='Model reasoning for the sentiment (transparency)',
                category='INFO',
            ),
        }

    def _evaluate_stale(self, resolved: Optional[ResolvedSignal], tick: TickData) -> bool:
        """
        Stale when the snapshot is older than max_staleness_minutes (or a gap).

        The one staleness definition (#434) — drives both the is_stale output
        and the base class's staleness-flip refresh trigger.

        Args:
            resolved: The point-in-time signal, or None on a gap
            tick: Current tick (age reference)

        Returns:
            True if the signal is stale at this tick
        """
        if resolved is None:
            return True
        age_minutes = (tick.timestamp - resolved.collected_msc).total_seconds() / 60.0
        return age_minutes > self.params.get('max_staleness_minutes')

    def _build_result(
        self,
        resolved: Optional[ResolvedSignal],
        tick: TickData
    ) -> WorkerResult:
        """
        Map a resolved sentiment snapshot (or a gap) to a WorkerResult.

        Args:
            resolved: The point-in-time signal, or None on a gap (no snapshot <= tick)
            tick: Current tick (for staleness against collected_msc)

        Returns:
            WorkerResult with the sentiment outputs
        """
        if resolved is None:
            return WorkerResult(outputs={
                'sentiment_score': 0.0,
                'confidence': 0.0,
                'signal': 'HOLD',
                'urgency': 0.0,
                'is_breaking': False,
                'reasoning': 'No signal data',
            })

        result = resolved.result

        return WorkerResult(outputs={
            'sentiment_score': float(result.sentiment_score),
            'confidence': float(result.confidence),
            'signal': result.signal,
            'urgency': float(result.urgency),
            'is_breaking': bool(result.is_breaking),
            'reasoning': result.reasoning,
        })
