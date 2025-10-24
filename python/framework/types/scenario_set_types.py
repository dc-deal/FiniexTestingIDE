"""
FiniexTestingIDE - Core Domain Types
Complete type system for blackbox framework

PERFORMANCE OPTIMIZED:
- TickData.timestamp is now datetime instead of str
- Eliminates 20,000+ pd.to_datetime() calls in bar rendering
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
import shutil
from typing import Any, Dict, List, Optional, Tuple
from python.components.logger.scenario_logger import ScenarioLogger


@dataclass
class SingleScenario:
    """Test scenario configuration for batch testing"""

    symbol: str
    start_date: str
    end_date: str
    max_ticks: Optional[int] = None
    data_mode: str = "realistic"
    enabled: bool = True  # Default: enabled
    logger: ScenarioLogger = None

    # ============================================
    # STRATEGY PARAMETERS
    # ============================================
    # Strategy-Logic (→ WorkerCoordinator sammelt Requirements & dessen Parameter)
    strategy_config: Dict[str, Any] = field(default_factory=dict)

    # NEW: Execution-Optimization (→ Framework)
    execution_config: Optional[Dict[str, Any]] = None

    # NEW: TradeSimulator configuration (per scenario)
    # Allows each scenario to have different balance/currency/leverage
    trade_simulator_config: Optional[Dict[str, Any]] = None

    name: Optional[str] = None

    def __post_init__(self):
        if self.name is None:
            self.name = f"{self.symbol}_{self.start_date}_{self.end_date}"

        # Smart Defaults für Execution Config
        if self.execution_config is None:
            self.execution_config = {
                # ============================================
                # EXECUTION CONFIGURATION STANDARD
                # ============================================
                # Worker-Level Parallelization
                # True = Workers parallel (gut bei 4+ workers)
                "parallel_workers": None,  # Auto-detect
                "worker_parallel_threshold_ms": 1.0,  # Nur parallel wenn Worker >1ms
                # ← NEU: Künstliche Last - NUR für Heavy workers
                # Ist eher für self-testing szenarios und stress tests gedacht.
                "artificial_load_ms": 5.0,  # 5ms pro Worker
                # Performance Tuning
                "adaptive_parallelization": True,  # Auto-detect optimal mode
                "log_performance_stats": True,  # Log timing statistics
            }


class ScenarioSet:
    """
        Test scenario set configuration for batch testing
        Describes a full scenario Set.
        The Logger logs globally & scenario-specific (files)
    """

    def __init__(self,
                 scenario_set_name: str,
                 logger: ScenarioLogger,
                 scenarios: List[SingleScenario],
                 printed_summary_logger: ScenarioLogger = None
                 ):
        self.scenario_set_name = scenario_set_name
        self.logger = logger
        self.scenarios = scenarios
        self.printed_summary_logger = printed_summary_logger

    def flush_set_buffer(self):
        self.logger.flush_buffer()
        for scenario in self.scenarios:
            scenario.logger.flush_buffer()
