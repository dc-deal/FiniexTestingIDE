"""
FiniexTestingIDE - Mount Preparer (#438)

The data-heavy + validation half of a batch, extracted so BOTH pipelines reuse it: the sim
BatchOrchestrator delegates prepare_scenarios()/prepare_mount() here, and the AutoTrader-mock
prepares a single scenario's data through the same index/validation stack. Loaders only — the
sim subprocess/mount-reuse model and the AutoTrader session model stay uncoupled.
"""

import time
from typing import Any, Dict, List, Optional, Tuple

from python.configuration.app_config_manager import AppConfigManager
from python.framework.batch.data_preparation_coordinator import (
    DataPreparationCoordinator,
    StatusBroadcaster,
)
from python.framework.batch.requirements_collector import RequirementsCollector
from python.framework.data_preparation.broker_data_preparator import BrokerDataPreparator
from python.framework.discoveries.data_coverage.data_coverage_report_manager import DataCoverageReportManager
from python.framework.discoveries.signal_coverage.signal_coverage_report_manager import SignalCoverageReportManager
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.types.batch_execution_types import WarmupPhaseEntry
from python.framework.types.live_types.live_stats_config_types import ScenarioStatus
from python.framework.types.mount_package_types import DataIdentityKey, MountPackage
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.validators.scenario_validator import ScenarioValidator


class MountPreparer:
    """
    Prepares the reusable data mount (Phase 0 validation + Phases 1–5 load) for a scenario list.

    Extracted from BatchOrchestrator so the AutoTrader-mock reuses the identical index/validation
    stack for a single scenario. Operates on the SingleScenario objects by reference — the
    validators mutate their validation_result exactly as in the batch, so a config/data error
    excludes that scenario (§33 in the sim; the single-session AutoTrader turns it into a hard abort).
    """

    def __init__(
        self,
        logger: AbstractLogger,
        app_config: AppConfigManager,
        requirements_collector: RequirementsCollector,
        live_stats: Optional[StatusBroadcaster] = None,
    ):
        """
        Initialize mount preparer.

        Args:
            logger: Logger for phase progress
            app_config: Application configuration manager
            requirements_collector: Collector for data requirements (Phase 3)
            live_stats: Optional status broadcaster (None → no live-stats broadcasts, e.g. AutoTrader)
        """
        self._logger = logger
        self._app_config = app_config
        self._requirements_collector = requirements_collector
        self._live_stats = live_stats

    @staticmethod
    def _valid(scenarios: List[SingleScenario]) -> List[SingleScenario]:
        """Scenarios that passed validation so far (validation_result mutates across phases)."""
        return [scenario for scenario in scenarios if scenario.is_valid()]

    @staticmethod
    def _failed(scenarios: List[SingleScenario]) -> List[SingleScenario]:
        """Scenarios that failed validation."""
        return [scenario for scenario in scenarios if not scenario.is_valid()]

    def _broadcast(self, status: ScenarioStatus) -> None:
        """Broadcast a batch status if a broadcaster is wired (no-op for the AutoTrader)."""
        if self._live_stats is not None:
            self._live_stats.broadcast_status(status)

    def prepare_scenarios(
        self,
        scenarios: List[SingleScenario],
    ) -> Tuple[Dict[BrokerType, Dict[str, Any]], BrokerDataPreparator, WarmupPhaseEntry]:
        """
        Phase 0 — data-identity validation: load broker configs (which set scenario.broker_type and
        the account currency) and run the five data-identity validators.

        This is the cheap per-run scenario preparation, separate from the expensive data load — so a
        sweep can prepare a combination's scenarios and reuse a mount without reloading (#419).
        prepare_mount runs it first; the sweep runner calls it directly per combination.

        Args:
            scenarios: The scenarios to validate (mutated in place: broker_type + validation_result)

        Returns:
            (broker_configs, broker_preparator, the 'Config Validation' warmup-phase timing)
        """
        self._logger.info("🔍 Phase 0: Validating configuration...")
        _phase_t = time.time()

        # Load broker configs first — sets scenario.broker_type, needed by validators.
        self._broadcast(ScenarioStatus.WARMUP_TRADER)
        _broker_preparator = BrokerDataPreparator(
            self._valid(scenarios), self._logger)
        _broker_configs = _broker_preparator.prepare()
        _broker_scenario_map = _broker_preparator.get_broker_scenario_map()

        # 1. Validate scenario names (unique, non-empty)
        ScenarioValidator.validate_scenario_names(
            scenarios=self._valid(scenarios),
            logger=self._logger
        )

        # 2. Validate scenario boundaries (end_date or max_ticks required)
        ScenarioValidator.validate_scenario_boundaries(
            scenarios=self._valid(scenarios),
            logger=self._logger
        )

        # 3. Validate each symbol is registered in its broker config
        ScenarioValidator.validate_scenario_symbols(
            scenarios=self._valid(scenarios),
            logger=self._logger,
            broker_scenario_map=_broker_scenario_map,
        )

        # 3b. Validate each symbol's swap_mode is modeled by the swap engine (#407)
        ScenarioValidator.validate_swap_modes(
            scenarios=self._valid(scenarios),
            logger=self._logger,
            broker_scenario_map=_broker_scenario_map,
        )

        # 4. Validate account_currency compatibility with symbols
        ScenarioValidator.validate_account_currencies(
            scenarios=self._valid(scenarios),
            logger=self._logger,
            broker_scenario_map=_broker_scenario_map,
        )

        # set scenario final currencies.
        ScenarioValidator.set_scenario_account_currency(
            scenarios=self._valid(scenarios),
            logger=self._logger,
            broker_scenario_map=_broker_scenario_map,
        )

        return _broker_configs, _broker_preparator, WarmupPhaseEntry(
            'Config Validation', time.time() - _phase_t)

    def prepare_mount(
        self,
        scenarios: List[SingleScenario],
        include_warmup_bars: bool = True,
    ) -> MountPackage:
        """
        Prepare the reusable data mount: data-identity validation + data load + packaging.

        The data-identity-dependent half of a batch (Phase 0 data validators + Phases 1–5).
        Produces a self-contained MountPackage keyed by the data identity, so it can be held
        resident (#418) and fed a new parameter set via execute(mount, scenarios) (#419)
        without reloading. Parameter validation is intentionally NOT here — it is the per-run
        check owned by the caller (run() / the sweep runner).

        Args:
            scenarios: The scenarios to prepare data for (mutated in place: validation_result)
            include_warmup_bars: Prepare + validate warmup bars (sim default). The AutoTrader-mock
                (#438) passes False: its adapter loads warmup bars itself (mock from the bar index,
                live from the API), so the shared prepare skips bar preparation — ticks + signals only

        Returns:
            MountPackage with the loaded per-scenario data and the data identity that keys it
        """
        start_time = time.time()
        warmup_phases = []

        # Phase 0 — data-identity validation (broker prep + validators), extracted so the sweep
        # runner can prep a combination's scenarios without reloading (#419).
        _broker_configs, _broker_preparator, _config_validation_phase = self.prepare_scenarios(scenarios)
        warmup_phases.append(_config_validation_phase)

        # ========================================================================
        # PHASE 1: INDEX & COVERAGE SETUP
        # ========================================================================
        self._logger.info("📊 Phase 1: Index & coverage setup...")
        _phase_t = time.time()

        data_coordinator = DataPreparationCoordinator(
            scenarios=self._valid(scenarios),
            logger=self._logger,
            app_config=self._app_config
        )

        # Build tick index and generate coverage reports
        tick_index_manager = data_coordinator.get_tick_index_manager()

        # Signal coverage (#429 sources) — the sibling report the validator reads
        # alongside the tick one. Empty when no scenario binds a signal source.
        signal_coverage_manager = SignalCoverageReportManager(
            logger=self._logger,
            scenarios=self._valid(scenarios),
            signal_index_manager=data_coordinator.get_signal_index_manager(),
        )
        signal_coverage_manager.generate_reports()

        coverage_report_manager = DataCoverageReportManager(
            logger=self._logger,
            scenarios=self._valid(scenarios),
            tick_index_manager=tick_index_manager,
            app_config=self._app_config,
            signal_coverage_reports=signal_coverage_manager.get_reports(),
        )
        coverage_report_manager.generate_reports()
        warmup_phases.append(WarmupPhaseEntry('Index & Coverage', time.time() - _phase_t))

        # ========================================================================
        # PHASE 2: AVAILABILITY VALIDATION
        # ========================================================================
        self._logger.info("🔍 Phase 2: Validating data availability...")
        _phase_t = time.time()

        # Validate that all scenarios have data available
        # IMPORTANT: Initializes validation_result for ALL SingleScenario objects
        coverage_report_manager.validate_availability(
            scenarios=self._valid(scenarios)
        )
        warmup_phases.append(WarmupPhaseEntry('Availability Check', time.time() - _phase_t))

        # ========================================================================
        # PHASE 3: REQUIREMENTS COLLECTION
        # ========================================================================
        self._logger.info("📋 Phase 3: Collecting data requirements...")
        _phase_t = time.time()

        # Collect requirements from valid scenarios only
        requirements_map = self._requirements_collector.collect_and_validate(
            self._valid(scenarios))

        # AutoTrader-mock path (#438): the adapter loads warmup bars itself (mock from the bar
        # index, live from the API), so the shared prepare skips bar preparation entirely — no bar
        # load (Phase 4) and no window-based warmup validation (Phase 5). Ticks + signals only.
        if not include_warmup_bars:
            requirements_map.bar_requirements = []
        warmup_phases.append(WarmupPhaseEntry('Requirements', time.time() - _phase_t))

        # ========================================================================
        # PHASE 4: DATA LOADING
        # ========================================================================
        self._logger.info("📦 Phase 4: Loading data...")

        # Prepare data only for scenarios in requirements_map
        scenario_packages, clipping_stats_map, load_timings = data_coordinator.prepare(
            requirements_map=requirements_map,
            broker_configs=_broker_configs,
            status_broadcaster=self._live_stats
        )
        warmup_phases.append(WarmupPhaseEntry('Data Loading → Ticks (parquet)', load_timings.ticks_s))
        warmup_phases.append(WarmupPhaseEntry('Data Loading → Bars (parquet)', load_timings.bars_s))
        warmup_phases.append(WarmupPhaseEntry('Data Loading → Packaging', load_timings.packaging_s))

        # ========================================================================
        # PHASE 5: QUALITY VALIDATION
        # ========================================================================
        self._logger.info("🔬 Phase 5: Validating data quality...")
        _phase_t = time.time()

        self._broadcast(ScenarioStatus.WARMUP_COVERAGE)

        coverage_report_manager.validate_after_load(
            scenarios=self._valid(scenarios),
            scenario_packages=scenario_packages,  # Dict of packages
            requirements_map=requirements_map
        )
        warmup_phases.append(WarmupPhaseEntry('Quality Validation', time.time() - _phase_t))

        # Calculate total invalid scenarios
        scenario_count = len(scenarios)
        total_invalid = len(self._failed(scenarios))
        valid_scenario_count = len(self._valid(scenarios))

        self._logger.info(
            f"✅ Continuing with {valid_scenario_count}/{scenario_count} "
            f"invalid scenario(s) ({total_invalid} filtered out)"
        )

        # Data identity — fingerprint each loaded scenario's data (broker / symbol / window /
        # warmup / tick budget, NOT strategy_config). The key #418/#419 reuse a mount on and the
        # execute() guard checks each fed scenario against.
        data_identity = {}
        for scenario in self._valid(scenarios):
            if scenario.scenario_index in scenario_packages:
                data_identity[scenario.scenario_index] = DataIdentityKey.from_scenario(
                    scenario, requirements_map.bar_requirements)

        batch_warmup_time = time.time() - start_time

        return MountPackage(
            scenario_packages=scenario_packages,
            clipping_stats_map=clipping_stats_map,
            broker_configs=_broker_configs,
            broker_scenario_map=_broker_preparator.get_valid_broker_scenario_map(
                self._valid(scenarios)
            ),
            requirements_map=requirements_map,
            warmup_phases=warmup_phases,
            batch_warmup_time=batch_warmup_time,
            data_identity=data_identity,
        )
