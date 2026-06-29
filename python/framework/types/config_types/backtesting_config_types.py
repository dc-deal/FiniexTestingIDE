"""
FiniexTestingIDE - Backtesting Pipeline Configuration Types
Pydantic models for the app_config.json::backtesting section.
"""
from typing import Dict, List
from pydantic import BaseModel, ConfigDict

from python.framework.types.config_types.performance_tracking_config_types import PerformanceTrackingConfig


class DefaultScenarioExecutionConfig(BaseModel):
    """Per-scenario execution defaults (base layer of 3-level cascade)."""
    model_config = ConfigDict(extra='forbid')
    parallel_workers: bool = False
    worker_parallel_threshold_ms: float = 1.0
    adaptive_parallelization: bool = True
    performance_tracking: PerformanceTrackingConfig = PerformanceTrackingConfig()
    strict_parameter_validation: bool = True
    tick_processing_budget_ms: float = 0.0
    heartbeat_interval_ms: int = 1000  # sim ghost-pass cadence (#360); 0 = disabled


class BacktestingExecutionConfig(BaseModel):
    """Backtesting batch execution settings."""
    parallel_scenarios: bool = True
    max_parallel_scenarios: int = 99
    default_scenario_execution_config: DefaultScenarioExecutionConfig = DefaultScenarioExecutionConfig()


class TradeSimulatorSeeds(BaseModel):
    """RNG seeds for deterministic simulation."""
    inbound_latency_seed: int = 42


class TradeSimulatorDefaults(BaseModel):
    """Trade simulator defaults (base layer of 3-level cascade)."""
    model_config = ConfigDict(extra='ignore')
    balances: Dict[str, float] = {'USD': 10000}
    seeds: TradeSimulatorSeeds = TradeSimulatorSeeds()
    inbound_latency_min_ms: int = 20
    inbound_latency_max_ms: int = 80


class DetailedLiveStatsExports(BaseModel):
    """Monitoring TUI export toggles."""
    export_portfolio_stats: bool = False
    export_current_bars: bool = False


class MonitoringConfig(BaseModel):
    """Backtesting TUI monitoring settings."""
    enabled: bool = True
    tui_refresh_rate_ms: int = 300
    detailed_live_stats: bool = True
    detailed_live_stats_threshold: int = 3
    detailed_live_stats_exports: DetailedLiveStatsExports = DetailedLiveStatsExports()
    event_tape_size: int = 5


class DataValidationConfig(BaseModel):
    """Warmup and data gap validation settings."""
    warmup_quality_mode: str = 'standard'
    allowed_gap_categories: List[str] = ['seamless', 'short', 'weekend', 'holiday']


class BacktestingPaths(BaseModel):
    """Filesystem paths used exclusively by the backtesting pipeline."""
    scenario_sets: str = 'configs/scenario_sets'
    brokers: str = 'configs/brokers'
    generator_template: str = 'configs/generator/template_scenario_set_header.json'
    generator_output: str = 'configs/scenario_sets'


class ParameterOptimizationConfig(BaseModel):
    """Parameter-sweep (#390) settings: data-mount reuse + fail-fast abort (#419)."""
    # Reuse the prepared data mount across a sweep's combinations instead of reloading
    # (the data identity is constant across a grid that varies only strategy_config).
    mount_reuse_enabled: bool = True
    # Abort the whole sweep when the first executed combination crashes for a data-level
    # reason (subprocess OOM) — every combination shares this data, so the rest would fail
    # identically.
    villain_abort_enabled: bool = True


class BacktestingConfig(BaseModel):
    """
    Top-level model for app_config.json::backtesting.
    Groups all settings that apply only to the backtesting pipeline.
    """
    execution: BacktestingExecutionConfig = BacktestingExecutionConfig()
    default_trade_simulator_config: TradeSimulatorDefaults = TradeSimulatorDefaults()
    monitoring: MonitoringConfig = MonitoringConfig()
    data_validation: DataValidationConfig = DataValidationConfig()
    paths: BacktestingPaths = BacktestingPaths()
    parameter_optimization: ParameterOptimizationConfig = ParameterOptimizationConfig()
