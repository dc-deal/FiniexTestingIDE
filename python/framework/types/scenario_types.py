"""
FiniexTestingIDE - Orchestrator Types
Type definitions for batch orchestration and scenario execution
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from python.data_worker.data_loader.core import TickDataLoader
from python.framework.factory.decision_logic_factory import DecisionLogicFactory
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.reporting.scenario_set_performance_manager import (
    ScenarioSetPerformanceManager
)
from python.configuration import AppConfigLoader


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


@dataclass
class ScenarioRequirements:
    """
    Calculated requirements for a scenario based on its workers.

    Each scenario calculates its own requirements independently,
    allowing different scenarios to use completely different
    worker configurations.

    Attributes:
        max_warmup_bars: Maximum warmup bars needed across all workers
        all_timeframes: All unique timeframes required by workers
        warmup_by_timeframe: Maximum warmup bars per timeframe
        total_workers: Total number of workers in scenario
    """
    max_warmup_bars: int
    all_timeframes: List[str]
    warmup_by_timeframe: Dict[str, int]
    total_workers: int


@dataclass
class ScenarioExecutionResult:
    """
    Result from executing a single scenario.

    Returned after complete scenario execution (warmup + tick loop).

    Attributes:
        success: Whether scenario executed successfully
        scenario_name: Name of the executed scenario
        scenario_index: Index in scenario list
        error: Error message if execution failed
        execution_time: Total execution time in seconds
    """
    success: bool
    scenario_name: str
    scenario_index: int
    error: Optional[str] = None
    scenario_execution_time_ms: Optional[float] = None
