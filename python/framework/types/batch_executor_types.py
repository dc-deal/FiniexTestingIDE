from dataclasses import dataclass
from typing import List
from python.configuration.app_config_loader import AppConfigLoader
from python.data_worker.data_loader.core import TickDataLoader
from python.framework.factory.decision_logic_factory import DecisionLogicFactory
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.reporting.scenario_set_performance_manager import ScenarioSetPerformanceManager
from python.framework.types.scenario_types import ScenarioExecutionResult


@dataclass
class BatchExecutionSummary:
    """Summary of batch execution results."""
    success: bool
    scenarios_count: int
    summary_execution_time: float
    summary_list:  List[ScenarioExecutionResult] = None


@dataclass
class ScenarioExecutorDependencies:
    """
    All dependencies needed for scenario execution.

    Encapsulates dependencies to avoid long constructor signatures
    and make testing easier.

    Attributes:
        data_worker: TickDataLoader for loading tick data
        app_config: Application configuration
        performance_log: Statistics collection container
        worker_factory: Factory for creating workers
        decision_logic_factory: Factory for creating decision logic
    """
    data_worker: TickDataLoader
    app_config: AppConfigLoader
    performance_log: ScenarioSetPerformanceManager
    worker_factory: WorkerFactory
    decision_logic_factory: DecisionLogicFactory
