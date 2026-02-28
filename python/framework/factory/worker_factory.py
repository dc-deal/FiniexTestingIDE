"""
FiniexTestingIDE - Worker Factory
Config-driven worker instantiation with namespace support

The Factory is responsible for:
1. Resolving worker types from config (e.g., "CORE/rsi" → RSIWorker class)
2. Validating required parameters are provided
3. Merging user config with worker defaults
4. Instantiating workers with correct parameters

Namespace System:
- CORE/worker_name → Framework workers (framework/workers/core/)
- USER/worker_name → User custom workers (workers/user/)
- BLACKBOX/worker_name → IP-protected workers (workers/blackbox/)
"""

import importlib
import json
from typing import Any, Dict, List, Type

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.market_types import TradingContext
from python.framework.types.parameter_types import ValidatedParameters
from python.framework.validators.parameter_validator import apply_defaults
from python.framework.workers.abstract_worker import AbstractWorker
from python.framework.workers.core.backtesting.backtesting_sample_worker import BacktestingSampleWorker
from python.framework.workers.core.macd_worker import MACDWorker
from python.framework.workers.core.rsi_worker import RSIWorker
from python.framework.workers.core.envelope_worker import EnvelopeWorker
from python.framework.workers.core.backtesting.heavy_rsi_worker import HeavyRSIWorker
from python.framework.workers.core.obv_worker import OBVWorker


class WorkerFactory:
    """
    Factory for creating workers from configuration.

    This is the central registry and instantiation system for all workers.
    It maintains a mapping of worker names to worker classes and handles
    the complete lifecycle of worker creation.
    """

    def __init__(
        self,
        logger: ScenarioLogger,
        strict_parameter_validation: bool = True
    ):
        """
        Initialize worker factory with empty registry.

        Args:
            logger: ScenarioLogger instance
            strict_parameter_validation: True = raise on boundary violations, False = warn only
        """
        self._logger = logger
        self._strict_validation = strict_parameter_validation
        self._registry: Dict[str, Type[AbstractWorker]] = {}
        self._load_core_workers()

    def _load_core_workers(self):
        """
        Pre-register core framework workers.

        Core workers are part of the framework and always available.
        They live in python/framework/workers/core/.

        This method is called during factory initialization to ensure
        core workers are immediately available.
        """
        # Import core workers
        try:
            # Register with CORE namespace
            self._registry["CORE/rsi"] = RSIWorker
            self._registry["CORE/envelope"] = EnvelopeWorker
            self._registry["CORE/heavy_rsi"] = HeavyRSIWorker
            self._registry["CORE/macd"] = MACDWorker
            self._registry["CORE/obv"] = OBVWorker

            # Backtesting workers
            self._registry["CORE/backtesting/backtesting_sample_worker"] = BacktestingSampleWorker

            self._logger.debug(
                f"Core workers registered: {list(self._registry.keys())}"
            )
        except ImportError as e:
            self._logger.warning(f"Failed to load core workers: {e}")

    def register_worker(
        self,
        worker_type: str,
        worker_class: Type[AbstractWorker]
    ):
        """
        Manually register a worker class.

        This method allows runtime registration of custom workers.
        Useful for plugins or dynamically loaded workers.

        Args:
            worker_type: Full worker type with namespace (e.g., "USER/my_worker")
            worker_class: Worker class (must inherit from AbstractWorker)

        Raises:
            ValueError: If worker_class doesn't inherit from AbstractWorker
        """
        if not issubclass(worker_class, AbstractWorker):
            raise ValueError(
                f"Worker class {worker_class.__name__} must inherit from "
                f"AbstractWorker"
            )

        self._registry[worker_type] = worker_class
        self._logger.debug(
            f"Registered worker: {worker_type} → {worker_class.__name__}")

    def create_worker(
        self,
        instance_name: str,
        worker_type: str,
        worker_config: Dict[str, Any] = None,
        trading_context: TradingContext = None,
    ) -> AbstractWorker:
        """
        Create a worker instance with validation.

        New Flow:
        1. Resolve worker class
        2. Get required/optional params via CLASSMETHODS
        3. Validate required parameters
        4. Merge user config with optional defaults
        5. Instantiate worker ONCE with merged parameters

        Args:
            instance_name: User-defined instance name (e.g., "rsi_main")
            worker_type: Worker type with namespace (e.g., "CORE/rsi")
            worker_config: User-provided parameters for this worker

        Returns:
            Instantiated worker ready for use

        Raises:
            ValueError: If worker type not found or required parameters missing
        """
        worker_config = worker_config or {}

        # Step 1: Resolve worker class
        worker_class = self._resolve_worker_class(worker_type)

        # Step 1.5: Validate type-specific config (is also checked in batch before run)
        worker_class.validate_config(worker_config)

        # Step 2: Validate parameters against schema
        warnings = worker_class.validate_parameter_schema(
            worker_config, strict=self._strict_validation
        )
        for warning in warnings:
            self._logger.warning(f"⚠️ {warning}")

        # Step 3: Apply schema defaults to config
        merged_params = apply_defaults(
            worker_config, worker_class.get_parameter_schema()
        )

        # Step 4: Wrap validated+defaulted config into ValidatedParameters
        validated_params = ValidatedParameters(merged_params)

        # Step 5: Instantiate worker ONCE with validated parameters
        worker_instance = worker_class(
            name=instance_name,
            logger=self._logger,
            parameters=validated_params,
            trading_context=trading_context,
        )

        self._logger.debug(
            f"✅ Created worker: {instance_name} ({worker_type}) "
            f"with {len(merged_params)} parameters"
        )

        return worker_instance

    def create_workers_from_config(
        self,
        strategy_config: Dict[str, Any],
        trading_context: TradingContext = None
    ) -> Dict[str, AbstractWorker]:
        """
        Create all workers from strategy configuration.

        This is the batch creation method used by orchestrator.
        It takes a complete strategy config and creates all declared workers.

        Args:
            strategy_config: Strategy configuration dict

        Returns:
            Dict mapping worker instance names to worker instances
        """
        # Extract worker instances mapping and configs
        worker_instances = strategy_config.get("worker_instances", {})
        workers_config = strategy_config.get("workers", {})

        if not worker_instances:
            raise ValueError(
                "No worker_instances specified in strategy_config")

        # Create each worker instance
        created_workers = {}

        for instance_name, worker_type in worker_instances.items():
            # Get config for this worker instance (may be empty dict)
            worker_config = workers_config.get(instance_name, {})

            # Create worker instance
            try:
                worker_instance = self.create_worker(
                    instance_name=instance_name,
                    worker_type=worker_type,
                    worker_config=worker_config,
                    trading_context=trading_context
                )

                created_workers[instance_name] = worker_instance

            except Exception as e:
                self._logger.error(
                    f"Failed to create worker {instance_name} ({worker_type}): {e}")
                raise ValueError(
                    f"Worker creation failed for {instance_name} ({worker_type}): {e}")

        self._logger.debug(
            f"✅ Created {len(created_workers)} workers: "
            f"{list(created_workers.keys())}"
        )

        return created_workers

    def _resolve_worker_class(
        self,
        worker_type: str
    ) -> Type[AbstractWorker]:
        """
        Resolve worker type string to worker class.

        This method handles the namespace-to-class mapping.
        If worker is not in registry, attempts to load it dynamically.

        Args:
            worker_type: Full worker type (e.g., "CORE/rsi")

        Returns:
            Worker class

        Raises:
            ValueError: If worker type not found or invalid
        """
        # Check if already registered
        if worker_type in self._registry:
            return self._registry[worker_type]

        # Attempt dynamic loading for USER/BLACKBOX workers
        if worker_type.startswith("USER/") or worker_type.startswith("BLACKBOX/"):
            return self._load_custom_worker(worker_type)

        # Worker not found
        raise ValueError(
            f"Unknown worker type: {worker_type}. "
            f"Available workers: {list(self._registry.keys())}"
        )

    def _load_custom_worker(
        self,
        worker_type: str
    ) -> Type[AbstractWorker]:
        """
        Dynamically load custom worker from USER or BLACKBOX namespace.

        This enables hot-loading of custom workers without pre-registration.

        Args:
            worker_type: Worker type (e.g., "USER/my_custom_rsi")

        Returns:
            Worker class

        Raises:
            NotImplementedError: If BLACKBOX namespace (Post-MVP feature)
            ValueError: If worker cannot be loaded
        """
        namespace, worker_name = worker_type.split("/", 1)

        # ============================================
        # BLACKBOX: Post-MVP Feature Gate
        # ============================================
        if namespace == "BLACKBOX":
            raise NotImplementedError(
                f"BlackBox workers are a Post-MVP feature.\n"
                f"'{worker_type}' cannot be loaded yet.\n"
                f"BlackBox loading will support encrypted/compiled workers "
                f"in future releases.\n"
                f"For now, use CORE/ or USER/ namespace workers."
            )

        # ============================================
        # USER: Active Loading
        # ============================================
        if namespace == "USER":
            module_path = f"python.workers.user.{worker_name}"
        else:
            raise ValueError(f"Unknown namespace: {namespace}")

        # Try to import module
        try:
            module = importlib.import_module(module_path)

            # Find worker class in module (convention: capitalized name)
            class_name = "".join(word.capitalize()
                                 for word in worker_name.split("_"))
            if not class_name.endswith("Worker"):
                class_name += "Worker"

            worker_class = getattr(module, class_name)

            # Register for future use
            self._registry[worker_type] = worker_class

            self._logger.info(f"Dynamically loaded worker: {worker_type}")
            return worker_class

        except (ImportError, AttributeError) as e:
            raise ValueError(
                f"Failed to load custom worker {worker_type}: {e}"
            )
