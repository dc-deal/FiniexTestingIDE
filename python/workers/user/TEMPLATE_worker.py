"""
USER Worker Template — Copy, rename, and implement.

Steps:
1. Copy this file: cp TEMPLATE_worker.py my_indicator.py
2. Rename class: TEMPLATEWorker → MyIndicatorWorker
3. Implement compute() with your indicator logic
4. Reference in config: "USER/my_indicator"

See docs/user_modules_and_hot_reload_mechanics.md for details.
See docs/quickstart_guide.md for a full walkthrough.
"""

from typing import Any, Dict, List

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.market_types.market_data_types import Bar, TickData
from python.framework.types.market_types.market_types import TradingContext
from python.framework.types.parameter_types import ParameterDef
from python.framework.types.worker_types import WorkerResult, WorkerType
from python.framework.workers.abstract_worker import AbstractWorker


class TEMPLATEWorker(AbstractWorker):
    """Replace with your worker description."""

    def __init__(self, name, parameters, logger, trading_context=None):
        super().__init__(
            name=name, parameters=parameters,
            logger=logger, trading_context=trading_context
        )
        # Access custom parameters via self.params.get('my_param')
        # 'periods' is auto-extracted for INDICATOR type workers

    # ============================================
    # Class methods for Factory
    # ============================================

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, ParameterDef]:
        """Define your configurable parameters with validation ranges."""
        return {
            # 'my_param': ParameterDef(
            #     param_type=float,
            #     default=14.0,
            #     min_val=1.0,
            #     max_val=200.0,
            #     description="Example parameter"
            # ),
        }

    @classmethod
    def get_worker_type(cls) -> WorkerType:
        return WorkerType.INDICATOR

    # ============================================
    # Instance methods for Runtime
    # ============================================

    def get_warmup_requirements(self) -> Dict[str, int]:
        """How many bars needed before trading starts."""
        return self.periods

    def get_required_timeframes(self) -> List[str]:
        """Which timeframes this worker needs."""
        return list(self.periods.keys())

    def get_max_computation_time_ms(self) -> float:
        """Maximum allowed computation time in ms."""
        return 50.0

    def should_recompute(self, tick: TickData, bar_updated: bool) -> bool:
        """When to recompute: on every tick or only when a bar closes."""
        return bar_updated

    def compute(
        self,
        tick: TickData,
        bar_history: Dict[str, List[Bar]],
        current_bars: Dict[str, Bar],
    ) -> WorkerResult:
        """
        Your indicator logic here.

        Args:
            tick: Current tick data
            bar_history: Historical bars per timeframe
            current_bars: Current (incomplete) bars per timeframe

        Returns:
            WorkerResult with computed value
        """
        # Example: return a placeholder value
        return WorkerResult(
            worker_name=self.name,
            value=0.0,
            confidence=0.0,
            metadata={'status': 'template_not_implemented'},
        )
