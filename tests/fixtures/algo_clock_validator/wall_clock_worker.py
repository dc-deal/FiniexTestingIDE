"""
Test fixture (#359): worker that reads wall-clock — must be flagged.
"""

import time
from typing import Dict, List

from python.framework.types.market_types.market_data_types import Bar, TickData
from python.framework.types.worker_types import WorkerResult, WorkerType
from python.framework.workers.abstract_worker import AbstractWorker


class WallClockWorker(AbstractWorker):
    """Reads time.time() in compute() — §9 violation."""

    @classmethod
    def get_worker_type(cls) -> WorkerType:
        return WorkerType.INDICATOR

    def get_warmup_requirements(self) -> Dict[str, int]:
        return {}

    def get_required_timeframes(self) -> List[str]:
        return []

    def should_recompute(self, tick: TickData, bar_updated: bool) -> bool:
        return False

    def compute(
        self,
        tick: TickData,
        bar_history: Dict[str, List[Bar]],
        current_bars: Dict[str, Bar],
    ) -> WorkerResult:
        self._computed_at = time.time()
        return None
