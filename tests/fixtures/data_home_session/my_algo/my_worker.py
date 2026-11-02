"""
Test fixture: the worker the strategy names relative to its own file.
"""

from typing import Dict, List, Optional

from python.framework.types.market_types.market_data_types import Bar, TickData
from python.framework.types.parameter_types import InputParamDef, OutputParamDef
from python.framework.types.worker_types import ComputeBasis, WorkerResult, WorkerType
from python.framework.workers.abstract_indicator_worker import AbstractIndicatorWorker


class MyWorker(AbstractIndicatorWorker):
    """Computes nothing."""

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, InputParamDef]:
        return {}

    @classmethod
    def get_output_schema(cls) -> Dict[str, OutputParamDef]:
        return {}

    @classmethod
    def get_worker_type(cls) -> WorkerType:
        return WorkerType.INDICATOR

    @classmethod
    def get_required_activity_metric(cls) -> Optional[str]:
        return None

    def get_warmup_requirements(self) -> Dict[str, int]:
        return self.periods

    def get_required_timeframes(self) -> List[str]:
        return list(self.periods.keys())

    def get_default_compute_basis(self) -> ComputeBasis:
        return ComputeBasis.LIVE

    def should_recompute(self, tick: TickData, bar_updated: bool) -> bool:
        return bar_updated

    def compute(
        self,
        tick: TickData,
        bar_history: Dict[str, List[Bar]],
        current_bars: Dict[str, Bar],
    ) -> WorkerResult:
        return WorkerResult(outputs={})
