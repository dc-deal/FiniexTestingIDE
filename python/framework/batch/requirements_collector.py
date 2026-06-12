"""
FiniexTestingIDE - Requirements Collector
Phase 3: Collects data requirements from all scenarios

Owns the shared WorkerFactory and MarketConfigManager for the batch run.
Both are built once at construction time and reused by:
- AggregateScenarioDataRequirements (class resolution for warmup calculation)
- ScenarioDataValidator.validate_worker_market_compatibility (pre-flight
  market/metric check)

Extracted from BatchOrchestrator to separate requirement collection logic.
"""
import json
import traceback
from typing import Dict, List, Optional

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.data_preparation.aggregate_scenario_data_requirements import AggregateScenarioDataRequirements
from python.framework.exceptions.persistence_errors import StatePersistenceError
from python.framework.factory.decision_logic_factory import DecisionLogicFactory
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.types.process_data_types import RequirementsMap
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.validation_types import ValidationResult
from python.framework.validators.algo_clock_validator import collect_algo_clock_violations
from python.framework.validators.algo_state_preflight import validate_state_snapshot_serializable
from python.framework.validators.scenario_data_validator import ScenarioDataValidator


class RequirementsCollector:
    """
    Collects and aggregates data requirements from multiple scenarios.

    Responsibilities:
    - Validate worker market compatibility (pre-flight)
    - Iterate through all scenarios
    - Collect warmup requirements per scenario
    - Aggregate requirements into unified map
    - Return finalized requirements for data preparation

    Owns the shared WorkerFactory and MarketConfigManager for the batch run.
    """

    def __init__(self, logger: AbstractLogger):
        """
        Initialize requirements collector.

        Args:
            logger: Logger instance for status messages
        """
        self._logger = logger

        # Shared factory — pre-flight class resolution only, no instantiation.
        # strict_parameter_validation is irrelevant here because the factory
        # is only used via resolve_worker_class(); per-scenario strict mode
        # is re-applied later via worker_class.validate_parameter_schema().
        self._worker_factory = WorkerFactory(
            logger=self._logger,
            strict_parameter_validation=False,
        )
        self._market_config_manager = MarketConfigManager()

        # Decision-logic factory for the #354 state-snapshot pre-flight. Unlike the
        # worker pre-flight (classmethod-based, instance-free), snapshot
        # serializability depends on the algo's RUNTIME state — it cannot be a
        # classmethod, so a bare instance is required. Cached per distinct
        # (decision_logic_type, config) so a single-logic set is checked exactly once.
        # strict_parameter_validation=False: parameter boundaries are validated
        # elsewhere; here a bare instance just needs to construct.
        self._decision_logic_factory = DecisionLogicFactory(
            logger=self._logger,
            strict_parameter_validation=False,
        )
        self._state_preflight_cache: Dict[str, Optional[str]] = {}
        # #359 algo-clock pre-flight cache. Violations are a property of the
        # SOURCE FILES (logic + workers), not of the config — keyed by
        # (decision_logic_type, sorted worker types) only.
        self._clock_preflight_cache: Dict[str, Optional[str]] = {}

        self._aggregate_requirements = AggregateScenarioDataRequirements(
            logger=self._logger,
            worker_factory=self._worker_factory,
        )
        self._warmup_requirements_by_scenario: Dict[int, Dict] = {}

    def collect_and_validate(
        self,
        scenarios: List[SingleScenario],
    ) -> RequirementsMap:
        """
        Validate market compatibility and collect requirements from all scenarios.

        Per-scenario loop:
        1. Market compatibility check — each worker's required activity metric
           must match the broker's primary_activity_metric. Incompatible
           scenarios get a ValidationResult(is_valid=False) and are skipped.
        2. Algo state snapshot pre-flight (#354) — serializability check.
        3. Algo clock pre-flight (#359) — wall-clock scan of algo sources.
        4. Requirements aggregation — classmethod-based warmup calculation.

        Args:
            scenarios: List of scenarios to analyze

        Returns:
            RequirementsMap ready for Phase 4 data loading
        """
        self._logger.info("📋 Phase 3: Collecting data requirements...")

        for idx, scenario in enumerate(scenarios):
            # === STEP 1: Market compatibility ===
            compat_errors = ScenarioDataValidator.validate_worker_market_compatibility(
                scenario,
                worker_factory=self._worker_factory,
                market_config_manager=self._market_config_manager,
            )
            if compat_errors:
                for error in compat_errors:
                    self._logger.error(f"❌ {scenario.name}: {error}")
                scenario.validation_result.append(ValidationResult(
                    is_valid=False,
                    scenario_name=scenario.name,
                    errors=compat_errors,
                    warnings=[],
                ))
                continue

            # === STEP 2: Algo state snapshot pre-flight (#354) ===
            # Runs BEFORE requirements aggregation so a non-serializable snapshot
            # excludes the scenario before its data is loaded (Phase 4). Centralized
            # here so a broken decision logic surfaces once — not as N failed runs.
            state_error = self._state_snapshot_preflight(scenario)
            if state_error:
                self._logger.error(f"❌ {scenario.name}: {state_error}")
                scenario.validation_result.append(ValidationResult(
                    is_valid=False,
                    scenario_name=scenario.name,
                    errors=[state_error],
                    warnings=[],
                ))
                continue

            # === STEP 3: Algo clock pre-flight (#359) ===
            # §9: decision logic & workers must never read wall-clock — the
            # canonical clock is get_current_time(). Source-level AST scan of
            # the loaded algo files (the only path that sees gitignored
            # user_algos/). A violation excludes the scenario, batch continues.
            clock_error = self._algo_clock_preflight(scenario)
            if clock_error:
                self._logger.error(f"❌ {scenario.name}: {clock_error}")
                scenario.validation_result.append(ValidationResult(
                    is_valid=False,
                    scenario_name=scenario.name,
                    errors=[clock_error],
                    warnings=[],
                ))
                continue

            # === STEP 4: Requirements aggregation ===
            try:
                warmup_reqs = self._aggregate_requirements.add_scenario(
                    scenario=scenario,
                    scenario_index=idx
                )
            except Exception as e:
                # Config error - don't proceed to availability check
                error_formatted = f"❌ {scenario.name}: Error - {e} \n{traceback.format_exc()}"
                self._logger.error(error_formatted)

                validation_result = ValidationResult(
                    is_valid=False,
                    scenario_name=scenario.name,
                    errors=[error_formatted],
                    warnings=[]
                )
                scenario.validation_result.append(validation_result)
                continue

            self._warmup_requirements_by_scenario[idx] = warmup_reqs

        # Finalize and return
        requirements_map = self._aggregate_requirements.finalize()

        return requirements_map

    def _algo_clock_preflight(self, scenario: SingleScenario) -> Optional[str]:
        """
        Pre-flight the scenario's algo sources for wall-clock reads (#359).

        Resolves the decision-logic class and every configured worker class
        (class resolution only — no instantiation) and AST-scans their source
        files for datetime.now() / datetime.utcnow() / time.time() calls (§9).
        Cached per distinct (decision_logic_type, worker types) — violations
        are a source-file property, independent of parameter config.

        A resolution failure (unknown type, missing file) is a best-effort
        skip — the regular pipeline validation reports those with full
        context, so no false exclusion is produced here.

        Args:
            scenario: The scenario whose algo sources to pre-flight

        Returns:
            Error message if a wall-clock violation was found, else None
        """
        strategy = scenario.strategy_config or {}
        logic_type = strategy.get('decision_logic_type', '')
        worker_instances = strategy.get('worker_instances', {})
        if not logic_type:
            return None

        cache_key = f"{logic_type}|{','.join(sorted(set(worker_instances.values())))}"
        if cache_key in self._clock_preflight_cache:
            return self._clock_preflight_cache[cache_key]

        classes = []
        try:
            logic_class, logic_source = self._decision_logic_factory.resolve_logic_class(logic_type)
            classes.append(logic_class)
            # Relative USER worker refs resolve against the logic's source dir
            # (same semantics as WorkerOrchestrator).
            worker_base_path = logic_source.parent if logic_source else None
            for worker_type in worker_instances.values():
                worker_class, _ = self._worker_factory.resolve_worker_class(
                    worker_type, base_path=worker_base_path)
                classes.append(worker_class)
        except Exception as e:
            self._logger.debug(
                f"Algo clock pre-flight skipped for '{logic_type}' "
                f"(class resolution failed: {e})"
            )
            self._clock_preflight_cache[cache_key] = None
            return None

        violations = collect_algo_clock_violations(classes)
        result: Optional[str] = None
        if violations:
            result = (
                'Wall-clock read in decision logic / worker code — use '
                'self.trading_api.get_current_time() (§9): ' + '; '.join(violations)
            )

        self._clock_preflight_cache[cache_key] = result
        return result

    def _state_snapshot_preflight(self, scenario: SingleScenario) -> Optional[str]:
        """
        Pre-flight the scenario's decision logic state snapshot (#354).

        Instantiates a bare decision logic (no workers/orchestrator/trading_api) and
        checks that get_state_snapshot() is JSON-serializable — but only for logics
        that opt into persistence (uses_state_persistence()). Cached per distinct
        (decision_logic_type, config) so a single-logic set is checked exactly once.

        A non-serializable snapshot is a real bug that would fail live, so it returns
        an error (→ scenario excluded). Any OTHER instantiation problem (e.g. a logic
        that needs a full trading context to construct) is a best-effort skip — the
        real run / AutoTrader boot validates with full context, so we never produce a
        false exclusion here.

        Args:
            scenario: The scenario whose decision logic to pre-flight

        Returns:
            Error message if the snapshot is not serializable, else None
        """
        strategy = scenario.strategy_config or {}
        logic_type = strategy.get('decision_logic_type', '')
        logic_config = strategy.get('decision_logic_config', {})
        if not logic_type:
            return None

        cache_key = f"{logic_type}|{json.dumps(logic_config, sort_keys=True, default=str)}"
        if cache_key in self._state_preflight_cache:
            return self._state_preflight_cache[cache_key]

        try:
            # Bare instance: no trading_context — a logic that reads context in
            # __init__ raises here and is skipped (best-effort), not excluded.
            logic = self._decision_logic_factory.create_logic(
                logic_type=logic_type,
                logger=self._logger,
                logic_config=logic_config,
            )
        except Exception as e:
            self._logger.debug(
                f"State pre-flight skipped for '{logic_type}' "
                f"(standalone instantiation failed: {e})"
            )
            self._state_preflight_cache[cache_key] = None
            return None

        result: Optional[str] = None
        try:
            validate_state_snapshot_serializable(logic)
        except StatePersistenceError as e:
            result = str(e)

        self._state_preflight_cache[cache_key] = result
        return result
