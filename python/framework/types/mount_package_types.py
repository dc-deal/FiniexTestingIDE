"""
Mount package types (#417) — the reusable data snapshot the prepare/execute seam produces.

A MountPackage is the data-identity-dependent result of BatchOrchestrator.prepare_mount():
the per-scenario loaded + packaged data plus the data identity that keys it. It carries
everything execute() and the run summary need and nothing strategy-specific, so it can be
held resident (the #418 Scenario Memory Manager) and fed new parameter sets (#419 sweep
reuse) via execute(mount, scenarios) without reloading. The seam is shaped this way on
purpose — a deliberate forward-preparation for #419/#418, not over-engineering.

DataIdentityKey is the per-scenario fingerprint of the loaded data: everything that
determines WHAT data was loaded (broker, symbol, tick window, warmup bars, tick budget),
deliberately excluding strategy_config. Two scenarios with an equal key share byte-identical
loaded data → the mount is reusable for both.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from python.framework.types.batch_execution_types import WarmupPhaseEntry
from python.framework.types.process_data_types import (
    BarRequirement,
    ClippingStats,
    ProcessDataPackage,
    RequirementsMap,
)
from python.framework.types.scenario_types.scenario_set_types import BrokerScenarioInfo, SingleScenario
from python.framework.types.trading_env_types.broker_types import BrokerType


@dataclass(frozen=True)
class DataIdentityKey:
    """
    Per-scenario fingerprint of the LOADED data — everything that determines which ticks and
    warmup bars were loaded, excluding strategy_config. Two scenarios with an equal key share
    byte-identical loaded data, so one mount serves both (#418/#419 lookup + #417 execute guard).

    Note: a swept worker parameter that changes the warmup window DOES change the key — correctly,
    since it changes the loaded data and the mount can no longer be reused.
    """
    data_broker_type: str
    symbol: str
    start: datetime
    end_date: Optional[datetime]
    max_ticks: Optional[int]
    warmup_bars: Tuple[Tuple[str, int], ...]
    tick_processing_budget_ms: float

    @classmethod
    def from_scenario(
        cls,
        scenario: SingleScenario,
        bar_requirements: List[BarRequirement],
    ) -> 'DataIdentityKey':
        """
        Build the data-identity key for one scenario from its data fields + warmup requirements.

        Args:
            scenario: The scenario whose loaded-data identity to fingerprint
            bar_requirements: All bar requirements (filtered by scenario name for the warmup window)

        Returns:
            The scenario's DataIdentityKey
        """
        warmup_bars = tuple(sorted(
            (req.timeframe, req.warmup_count)
            for req in bar_requirements
            if req.scenario_name == scenario.name
        ))
        budget_ms = 0.0
        if scenario.execution_config:
            budget_ms = scenario.execution_config.get('tick_processing_budget_ms', 0.0)
        return cls(
            data_broker_type=scenario.data_broker_type,
            symbol=scenario.symbol,
            start=scenario.start_date,
            end_date=scenario.end_date,
            max_ticks=scenario.max_ticks,
            warmup_bars=warmup_bars,
            tick_processing_budget_ms=budget_ms,
        )


@dataclass
class MountPackage:
    """
    Reusable data snapshot from BatchOrchestrator.prepare_mount() (#417).

    Holds the per-scenario loaded + packaged data and the data identity that keys it — nothing
    strategy-specific — so it can be held resident (#418) and fed a new parameter set via
    execute(mount, scenarios) (#419) without reloading. The per-run scenario objects (the
    "parameter package") are NOT part of the mount; they are passed to execute() separately.
    """
    # scenario_index → loaded data package
    scenario_packages: Dict[int, ProcessDataPackage]
    # scenario_index → tick-budget clipping stats
    clipping_stats_map: Dict[int, ClippingStats]
    # serialized broker configs (per broker type) for subprocess re-hydration
    broker_configs: Dict[BrokerType, Dict[str, Any]]
    # broker type → scenario info (consumed by the run summary)
    broker_scenario_map: Dict[BrokerType, BrokerScenarioInfo]
    # aggregated data requirements (data-level; the warmup source for the identity guard)
    requirements_map: RequirementsMap
    # per-phase warmup timing breakdown
    warmup_phases: List[WarmupPhaseEntry]
    # total warmup wall time (load + validation)
    batch_warmup_time: float
    # scenario_index → DataIdentityKey (the mount's data fingerprint)
    data_identity: Dict[int, DataIdentityKey]

    def data_identity_fingerprint(self) -> Tuple[DataIdentityKey, ...]:
        """
        Set-level data fingerprint: the per-scenario keys ordered by scenario_index.

        Returns:
            Tuple of DataIdentityKey ordered by scenario_index
        """
        return tuple(key for _, key in sorted(self.data_identity.items()))
