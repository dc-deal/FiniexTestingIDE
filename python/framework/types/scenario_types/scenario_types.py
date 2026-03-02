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
