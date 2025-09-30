"""
FiniexTestingIDE Blackbox Framework
Complete blackbox trading system
"""

from python.blackbox.types import (
    TickData,
    Bar,
    WorkerResult,
    WorkerContract,
    TestScenario,
    GlobalContract,
    TimeframeConfig,
    WorkerState,
)

from python.blackbox.decision_orchestrator import DecisionOrchestrator
from python.blackbox.batch_orchestrator import BatchOrchestrator
from python.blackbox.bar_rendering_orchestrator import BarRenderingOrchestrator
from python.blackbox.bar_renderer import BarRenderer
from python.blackbox.warmup_manager import WarmupManager
from python.blackbox.tick_data_preparator import TickDataPreparator


__all__ = [
    # Core Types
    "TickData",
    "Bar",
    "WorkerResult",
    "WorkerContract",
    "TestScenario",
    "GlobalContract",
    "TimeframeConfig",
    "WorkerState",
    # Orchestration
    "DecisionOrchestrator",
    "BatchOrchestrator",
    # bar rendering system
    "BarRenderingOrchestrator",
    "BarRenderer",
    "WarmupManager",
    "TickDataPreparator"
]
