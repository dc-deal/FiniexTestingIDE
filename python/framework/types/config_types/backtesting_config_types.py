"""
FiniexTestingIDE - Backtesting Pipeline Configuration Types
Pydantic models for the app_config.json::backtesting section.
"""
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict

from python.framework.types.config_types.strict_config_model import StrictConfigModel

from python.framework.types.config_types.performance_tracking_config_types import (
    PerformanceTrackingConfig,
)


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


class BacktestingExecutionConfig(StrictConfigModel):
    """Backtesting batch execution settings."""
    parallel_scenarios: bool = True
    max_parallel_scenarios: int = 99
    default_scenario_execution_config: DefaultScenarioExecutionConfig = DefaultScenarioExecutionConfig()


class TradeSimulatorSeeds(StrictConfigModel):
    """RNG seeds for deterministic simulation."""
    inbound_latency_seed: int = 42


class TradeSimulatorDefaults(StrictConfigModel):
    """
    Trade simulator defaults — the base layer of the 3-level cascade.

    It is strict like every other block. This docstring used to argue that the strictness was
    safe because the SCENARIO-level dict "never passes through this model" — and that was
    simply false: `scenario_config_loader` validates the merged scenario dict against this
    model per scenario. So `account_currency`, a real setting two sandbox sets carry, was
    refused as an extra input and BOTH sets died at startup, before a single scenario ran.
    A validation whose justification rests on a path that does not exist fails on the day
    somebody uses the feature it was meant to allow.

    So the key is DECLARED, which is what the old docstring prescribed for exactly this case.
    Declaring it is permissive only: the value is still read from the raw dict by
    `ScenarioValidator.set_scenario_account_currency`, which is what actually applies it.
    """
    balances: Dict[str, float] = {'USD': 10000}
    seeds: TradeSimulatorSeeds = TradeSimulatorSeeds()
    # Declared so the strict model accepts it on the SCENARIO level, where it is really used.
    # None = the account currency is derived from the symbol's quote currency (#265).
    account_currency: Optional[str] = None
    inbound_latency_min_ms: int = 20
    inbound_latency_max_ms: int = 80


class DetailedLiveStatsExports(StrictConfigModel):
    """Monitoring TUI export toggles."""
    export_portfolio_stats: bool = False
    export_current_bars: bool = False


class MonitoringConfig(StrictConfigModel):
    """Backtesting TUI monitoring settings."""
    enabled: bool = True
    tui_refresh_rate_ms: int = 300
    detailed_live_stats: bool = True
    detailed_live_stats_threshold: int = 3
    detailed_live_stats_exports: DetailedLiveStatsExports = DetailedLiveStatsExports()
    event_tape_size: int = 5


class DataValidationConfig(StrictConfigModel):
    """
    Warmup, data gap and data-origin validation settings.

    `admitted_origin_classes` is the gate #518 builds, and it starts OPEN on purpose. Until a
    producer stamps an identity and the legacy archive is attested, every file resolves to
    `unknown` — so a strict default would refuse every scenario on the day it shipped, which is
    how a gate gets switched off permanently instead of being narrowed once. It therefore ramps:
    a scenario reading anything other than production data the producer itself stamped is
    WARNED about from the start, so the refusal can be measured before it is armed, and
    narrowing this list is what turns the warning into an exclusion.
    """
    warmup_quality_mode: str = 'standard'
    allowed_gap_categories: List[str] = ['seamless', 'short', 'weekend', 'holiday']
    admitted_origin_classes: List[str] = ['production', 'development', 'unknown']


class BacktestingPaths(StrictConfigModel):
    """Filesystem paths used exclusively by the backtesting pipeline."""
    scenario_sets: str = 'configs/scenario_sets'
    generator_template: str = 'configs/generator/template_scenario_set_header.json'
    generator_output: str = 'configs/scenario_sets'


class ParameterOptimizationConfig(StrictConfigModel):
    """Parameter-sweep (#390) settings: data-mount reuse + fail-fast abort (#419)."""
    # Reuse the prepared data mount across a sweep's combinations instead of reloading
    # (the data identity is constant across a grid that varies only strategy_config).
    mount_reuse_enabled: bool = True
    # Abort the whole sweep when the first executed combination crashes for a data-level
    # reason (subprocess OOM) — every combination shares this data, so the rest would fail
    # identically.
    villain_abort_enabled: bool = True


class BacktestingConfig(StrictConfigModel):
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
