"""
Test fixture (#588): a worker that states its warmup twice and disagrees with itself.

The classmethod the batch sizes its bar load with returns the configured period; the instance
an AutoTrader session loads by returns three times that — the drift a recursive average invites.
Must be refused when it is created.
"""

from typing import Dict, List

from python.framework.types.market_types.market_data_types import Bar, TickData
from python.framework.types.worker_types import WorkerResult, WorkerType
from python.framework.workers.abstract_indicator_worker import AbstractIndicatorWorker


class TwoAnswersWorker(AbstractIndicatorWorker):
    """Asks for its period through one door and three times its period through the other."""

    @classmethod
    def get_worker_type(cls) -> WorkerType:
        return WorkerType.INDICATOR

    def get_warmup_requirements(self) -> Dict[str, int]:
        return {timeframe: period * 3 for timeframe, period in self.periods.items()}

    def get_required_timeframes(self) -> List[str]:
        return list(self.periods.keys())

    def should_recompute(self, tick: TickData, bar_updated: bool) -> bool:
        return bar_updated

    def compute(
        self,
        tick: TickData,
        bar_history: Dict[str, List[Bar]],
        current_bars: Dict[str, Bar],
    ) -> WorkerResult:
        return WorkerResult(outputs={})
