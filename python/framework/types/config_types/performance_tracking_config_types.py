"""
FiniexTestingIDE - Performance Tracking Configuration Types
Pydantic models for the nested 'performance_tracking' sub-group inside
execution_config (Backtesting) and autotrader (AutoTrader).

Two independent toggles control two independent tracking layers:
- Layer A (worker_decision_tracking): per-worker / decision counters
- Layer B (tick_loop_profiling): operation-level timers in the tick loop

AutoTrader has no Layer-B equivalent — only Layer A applies there.
"""
from pydantic import BaseModel, ConfigDict


class PerformanceTrackingConfig(BaseModel):
    """
    Nested performance tracking toggles for the Backtesting pipeline.

    Cascade-compatible: lives inside execution_config and follows the
    3-level cascade (app_config → scenario_set global → scenario).
    """
    model_config = ConfigDict(extra='forbid')
    tick_loop_profiling: bool = True
    worker_decision_tracking: bool = False


class AutoTraderPerformanceTrackingConfig(BaseModel):
    """
    Nested performance tracking toggles for the AutoTrader pipeline.

    AutoTrader's tick loop has no operation-level profiler, so only
    Layer A applies. Cascade: app_config.autotrader → profile.
    """
    model_config = ConfigDict(extra='forbid')
    worker_decision_tracking: bool = False
