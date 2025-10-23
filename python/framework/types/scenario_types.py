"""
FiniexTestingIDE - Orchestrator Types
Type definitions for batch orchestration and scenario execution
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


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
