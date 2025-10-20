

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ScenarioPerformanceStats:
    """
    Performance statistics for a single scenario.

    Contains all performance data that was previously in batch_results dict.
    """
    # Scenario metadata
    scenario_index: int  # Original position in scenario array
    scenario_name: str
    symbol: str

    # Execution stats
    ticks_processed: int
    signals_generated: int
    signals_gen_buy: int
    signals_gen_sell: int
    signal_rate: float
    success: bool
    portfolio_value: float
    initial_balance: float
    elapsed_time: float

    # Worker statistics
    worker_statistics: Dict[str, Any]

    # Decision logic
    decision_logic_name: str

    # Scenario requirement
    scenario_requirement: Dict[str, Any]

    # Optional: First 10 signals for inspection
    sample_signals: List[Dict] = field(default_factory=list)

    # Portfolio & Trading Stats (per scenario)
    # Each scenario gets its own TradeSimulator, stats stored here
    portfolio_stats: Dict[str, Any] = field(default_factory=dict)
    execution_stats: Dict[str, Any] = field(default_factory=dict)
    cost_breakdown: Dict[str, Any] = field(default_factory=dict)

    # NEW: Profiling data from tick loop
    # Structure:
    # {
    #     'profile_times': {'trade_simulator': 123.45, 'bar_rendering': 67.89, ...},
    #     'profile_counts': {'trade_simulator': 100, 'bar_rendering': 100, ...},
    #     'total_per_tick': 456.78
    # }
    profiling_data: Dict[str, Any] = field(default_factory=dict)
