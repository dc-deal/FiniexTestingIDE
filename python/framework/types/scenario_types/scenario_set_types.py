"""
FiniexTestingIDE - Core Domain Types
Complete type system for blackbox framework

PERFORMANCE OPTIMIZED:
- TickData.timestamp is now datetime instead of str
- Eliminates 20,000+ pd.to_datetime() calls in bar rendering
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from python.framework.discoveries.signal_coverage.signal_coverage_report import SignalCoverageReport
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.config_types.robustness_config_types import (
    RobustnessConfig,
    RobustnessRole,
)
from python.framework.types.scenario_types.window_set_types import WindowSet
from python.framework.types.validation_types import ValidationResult


@dataclass
class SingleScenario:
    """Test scenario configuration for batch testing"""
    # identification for Scenario, must be unique
    name: str
    # internal index, the only source of truth for scenario picking.
    scenario_index: int
    symbol: str

    # ============================================
    # DATA SOURCE (REQUIRED)
    # ============================================
    # Determines which tick/bar data collection to load from
    # Examples: "mt5", "kraken_spot"
    # This is SEPARATE from broker_type (trading simulation config)
    data_broker_type: str  # REQUIRED - no default!

    start_date: datetime
    end_date: Optional[datetime] = None
    max_ticks: Optional[int] = None
    data_mode: str = 'realistic'
    enabled: bool = True  # Default: enabled

    # ============================================
    # SIGNAL DATA SOURCE (OPTIONAL, #429)
    # ============================================
    # First-class sentiment/signal source, resolved via the signal index
    # (analogue of data_broker_type for ticks). Keyed by the archive's
    # pipeline_id (e.g. 'crypto_sentiment'); symbol reuses `symbol` above.
    # Empty = scenario has no SIGNAL worker input.
    data_sentiment_type: str = ''

    # ============================================
    # STRATEGY PARAMETERS
    # ============================================
    strategy_config: Dict[str, Any] = field(default_factory=dict)
    execution_config: Optional[Dict[str, Any]] = None

    # TradeSimulator configuration (per scenario)
    # broker_type here is for TRADING simulation, not data source!
    broker_type: BrokerType = None
    trade_simulator_config: Optional[Dict[str, Any]] = None

    # Stress test configuration (per scenario, cascaded from global)
    stress_test_config: Optional[Dict[str, Any]] = None

    # OrderGuard configuration (per scenario, cascaded from global)
    # None = OrderGuardDefaults (60s cooldown, 2 rejections)
    order_guard_config: Optional[Dict[str, Any]] = None

    account_currency: str = ''

    # === ROBUSTNESS (#367) ===
    # Per-window IS/OOS label (manual config or generator-assigned). Default unassigned.
    role: RobustnessRole = RobustnessRole.UNASSIGNED
    # Volatility regime + trading session of the window — only populated for Profile Runs
    # (copied from the source GeneratedWindow); empty for manual / blocks scenarios.
    regime: str = ''
    session: str = ''

    # === VALIDATION TRACKING ===
    # never init validation_result — its only purpose is to be filled
    # by batch phases to exclude failed scenarios from execution
    validation_result: List[ValidationResult] = field(
        default_factory=list, init=False)

    # === DATA SOURCE METADATA (populated during data loading) ===
    # What this scenario actually READ, per overlapping archive file. Per SCENARIO and not per
    # run on purpose: one set spans symbols, brokers and windows, so a run-level answer would
    # be an aggregate over things that do not share one — and an aggregate is not a description.
    data_format_versions: List[str] = field(default_factory=list)
    origin_classes: List[str] = field(default_factory=list)
    origin_evidence_grades: List[str] = field(default_factory=list)
    # One entry per mounted BAR file — the only archive that stamps a price basis. A scenario
    # that mounted none stays EMPTY rather than falling back to the broker's declaration
    # (§31c): 'what was this rendered from' and 'what would a render produce today' are two
    # questions, and they disagree for exactly as long as a re-render is unfinished.
    price_bases: List[str] = field(default_factory=list)

    # === PROFILE RUN METADATA (populated from a WindowSet) ===
    is_profile_run: bool = False

    def __post_init__(self):
        if self.name is None:
            raise ValueError(
                'Property name of scenario array Objects must be filled.')

        # Smart defaults for execution config
        if self.execution_config is None:
            self.execution_config = {
                # ============================================
                # EXECUTION CONFIGURATION STANDARD
                # ============================================
                # Worker-Level Parallelization
                # True = workers run in parallel (good with 4+ workers)
                'parallel_workers': None,  # Auto-detect
                'worker_parallel_threshold_ms': 1.0,  # Only parallelize when worker takes >1ms
                # Performance Tuning
                'adaptive_parallelization': True,  # Auto-detect optimal mode
                # Parameter Validation
                # True = abort on boundary violations, False = warn only
                'strict_parameter_validation': True,
            }

    def to_config_string_for_display(self) -> str:
        """
        Display string for scenario configuration.

        Returns:
            Human-readable config summary
        """
        sentiment_line = (
            f'  Sentiment Source: {self.data_sentiment_type}\n'
            if self.data_sentiment_type else ''
        )
        return (
            f"Scenario: {self.name}\n"
            f"  Data Source: {self.data_broker_type}\n"
            f"  Symbol: {self.symbol}\n"
            f"{sentiment_line}"
            f"  Period: {self.start_date} → {self.end_date}\n"
            f"  Max Ticks: {self.max_ticks or 'unlimited'}\n"
            f"  Enabled: {self.enabled}"
        )

    def is_valid(self) -> bool:
        """
        Check if scenario passed validation.

        Returns:
            True if no validation result or validation passed
        """
        if not self.validation_result:
            return True

        # Check all ValidationResult objects
        return all(v.is_valid for v in self.validation_result)


@dataclass
class LoadedScenarioConfig:
    """Result of config loading - raw data before ScenarioSet creation"""
    scenario_set_name: str
    scenarios: List[SingleScenario]
    config_path: Path
    generator_profiles: Optional[List[WindowSet]] = None
    generator_profile_paths: Optional[List[Path]] = None
    # Set-wide robustness mode (#367); None → disabled (treated as RobustnessConfig()).
    robustness: Optional[RobustnessConfig] = None


@dataclass
class BrokerScenarioInfo:
    """Internal mapping of broker to scenarios (used for logging)."""
    config_path: str
    scenarios: List[str]
    symbols: Set[str]
    broker_config: BrokerConfig


@dataclass
class SignalScenarioUsage:
    """One scenario's use of a signal source — its data window (#433)."""
    scenario_name: str
    symbol: str
    window_start: datetime
    window_end: Optional[datetime] = None


@dataclass
class SignalScenarioInfo:
    """
    Internal mapping of a signal source/symbol to the scenarios using it (#433).

    The signal sibling of BrokerScenarioInfo: it carries the archive-side coverage
    (built once in the preparation Phase 1) plus every scenario window bound to it,
    so the report renders both planes without re-reading the parquet.
    """
    data_sentiment_type: str
    symbol: str
    coverage: SignalCoverageReport
    usages: List[SignalScenarioUsage] = field(default_factory=list)


@dataclass
class ScenarioSetMetadata:
    """
    Metadata about a scenario set config file

    Used for discovery and listing of available scenario sets
    """
    # Basic info
    filename: str
    scenario_set_name: str
    total_count: int
    enabled_count: int
    disabled_count: int
    symbols: list[str]
    config_path: Path

    # Time analysis
    timespan_scenario_count: int
    total_timespan_seconds: float
    tick_scenario_count: int
    total_ticks: int

    # Strategy info
    decision_logic_type: str | None
    is_mixed_decision_logic: bool
    worker_count: int | None
    is_mixed_workers: bool
