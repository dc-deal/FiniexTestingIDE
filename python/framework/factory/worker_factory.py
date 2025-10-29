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

Example Config:
{
    "worker_types": ["CORE/rsi", "CORE/envelope"],
    "workers": {
        "CORE/rsi": {
            "period": 14,        # Required parameter
            "timeframe": "M5"    # Required parameter
        },
        "CORE/envelope": {
            "period": 20,        # Optional (has default)
            "deviation": 0.02    # Optional (has default)
        }
    }
}
"""

import importlib
import json
from typing import Any, Dict, List, Type

from python.components.logger.scenario_logger import ScenarioLogger
from python.framework.workers.abstract_blackbox_worker import \
    AbstractBlackboxWorker

from python.components.logger.bootstrap_logger import get_logger
vLog = get_logger()


class WorkerFactory:
    """
    Factory for creating workers from configuration.

    This is the central registry and instantiation system for all workers.
    It maintains a mapping of worker names to worker classes and handles
    the complete lifecycle of worker creation.
    """

    def __init__(self):
        """
        Initialize worker factory with empty registry.

        The registry is populated on-demand when workers are requested.
        This lazy-loading approach avoids import overhead for unused workers.
        """
        self._registry: Dict[str, Type[AbstractBlackboxWorker]] = {}
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
            from python.framework.workers.core.rsi_worker import RSIWorker
            from python.framework.workers.core.envelope_worker import EnvelopeWorker
            from python.framework.workers.core.heavy_workers import HeavyEnvelopeWorker
            from python.framework.workers.core.heavy_workers import HeavyMACDWorker
            from python.framework.workers.core.heavy_workers import HeavyRSIWorker

            # Register with CORE namespace
            self._registry["CORE/rsi"] = RSIWorker
            self._registry["CORE/envelope"] = EnvelopeWorker
            self._registry["CORE/heavy_envelope"] = HeavyEnvelopeWorker
            self._registry["CORE/heavy_macd"] = HeavyMACDWorker
            self._registry["CORE/heavy_rsi"] = HeavyRSIWorker

            vLog.debug(
                f"Core workers registered: {list(self._registry.keys())}"
            )
        except ImportError as e:
            vLog.warning(f"Failed to load core workers: {e}")

    def register_worker(
        self,
        worker_type: str,
        worker_class: Type[AbstractBlackboxWorker]
    ):
        """
        Manually register a worker class.

        This method allows runtime registration of custom workers.
        Useful for plugins or dynamically loaded workers.

        Args:
            worker_type: Full worker type with namespace (e.g., "USER/my_worker")
            worker_class: Worker class (must inherit from AbstractBlackboxWorker)

        Raises:
            ValueError: If worker_class doesn't inherit from AbstractBlackboxWorker
        """
        if not issubclass(worker_class, AbstractBlackboxWorker):
            raise ValueError(
                f"Worker class {worker_class.__name__} must inherit from "
                f"AbstractBlackboxWorker"
            )

        self._registry[worker_type] = worker_class
        vLog.debug(
            f"Registered worker: {worker_type} → {worker_class.__name__}")

    def create_worker(
        self,
        instance_name: str,
        worker_type: str,
        logger: ScenarioLogger,
        worker_config: Dict[str, Any] = None,

    ) -> AbstractBlackboxWorker:
        """
        Create a worker instance with validation.

        Keine temp_instance mehr - nutzt classmethods!

        New Flow:
        1. Resolve worker class
        2. Get required/optional params via CLASSMETHODS (ohne Instanz!)
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

        # Step 2: Get parameter requirements via CLASSMETHODS
        required_params = worker_class.get_required_parameters()
        optional_params = worker_class.get_optional_parameters()

        # Step 3: Validate required parameters
        self._validate_required_parameters(
            worker_type,
            required_params,
            worker_config
        )

        # Step 4: Merge user config with optional defaults
        merged_params = self._merge_parameters(
            optional_params,
            worker_config
        )

        # Step 5: Instantiate worker ONCE with merged parameters
        worker_instance = worker_class(
            name=instance_name,
            logger=logger,
            parameters=merged_params,
        )

        logger.debug(
            f"✓ Created worker: {instance_name} ({worker_type}) "
            f"with {len(merged_params)} parameters"
        )

        return worker_instance

    def create_workers_from_config(
        self,
        strategy_config: Dict[str, Any],
        logger: ScenarioLogger
    ) -> Dict[str, AbstractBlackboxWorker]:
        """
        Create all workers from strategy configuration.

        This is the batch creation method used by orchestrator.
        It takes a complete strategy config and creates all declared workers.

        Expected config structure:
        {
            "worker_instances": {
                "rsi_main": "CORE/rsi",
                "envelope_main": "CORE/envelope"
            },
            "workers": {
                "rsi_main": {"period": 14, "timeframe": "M5"},
                "envelope_main": {"period": 20, "deviation": 0.02}
            }
        }

        Args:
            strategy_config: Strategy configuration dict

        Returns:
            Dict mapping worker instance names to worker instances

        Raises:
            ValueError: If config is invalid or worker creation fails
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
                    logger=logger,
                    worker_config=worker_config
                )

                created_workers[instance_name] = worker_instance

            except Exception as e:
                vLog.error(
                    f"Failed to create worker {instance_name} ({worker_type}): {e}")
                raise ValueError(
                    f"Worker creation failed for {instance_name} ({worker_type}): {e}")

        logger.debug(
            f"✓ Created {len(created_workers)} workers: "
            f"{list(created_workers.keys())}"
        )

        return created_workers

    def _resolve_worker_class(
        self,
        worker_type: str
    ) -> Type[AbstractBlackboxWorker]:
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
    ) -> Type[AbstractBlackboxWorker]:
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

            vLog.info(f"Dynamically loaded worker: {worker_type}")
            return worker_class

        except (ImportError, AttributeError) as e:
            raise ValueError(
                f"Failed to load custom worker {worker_type}: {e}"
            )

    def _validate_required_parameters(
        self,
        worker_type: str,
        required_params: Dict[str, type],
        provided_params: Dict[str, Any]
    ):
        """
        Validate that all required parameters are provided.

        This is a critical validation step that prevents runtime errors
        from missing parameters.

        Args:
            worker_type: Worker type (for error messages)
            required_params: Dict of required parameter names and types
            provided_params: User-provided parameters

        Raises:
            ValueError: If any required parameter is missing or wrong type
        """
        missing = []
        wrong_type = []

        for param_name, param_type in required_params.items():
            # Check if parameter exists
            if param_name not in provided_params:
                missing.append(param_name)
                continue

            # Check parameter type (optional but helpful)
            provided_value = provided_params[param_name]
            if not isinstance(provided_value, param_type):
                wrong_type.append(
                    f"{param_name} (expected {param_type.__name__}, "
                    f"got {type(provided_value).__name__})"
                )

        # Raise errors if validation failed
        if missing:
            raise ValueError(
                f"Worker '{worker_type}' missing required parameters: "
                f"{', '.join(missing)}, "
                f"required: "
                f"{json.dumps(required_params, indent=4, default=str)}"
                f"found: "
                f"{json.dumps(provided_params, indent=4, default=str)}"
            )

        if wrong_type:
            raise ValueError(
                f"Worker '{worker_type}' has wrong parameter types: "
                f"{', '.join(wrong_type)}"
            )

    def _merge_parameters(
        self,
        optional_defaults: Dict[str, Any],
        provided_params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Merge user-provided parameters with optional defaults.

        User parameters override defaults. This allows workers to have
        sensible defaults while still being fully configurable.

        Args:
            optional_defaults: Default values for optional parameters
            provided_params: User-provided parameters

        Returns:
            Merged parameter dict (defaults + overrides)
        """
        # Start with defaults
        merged = optional_defaults.copy()

        # Override with user params
        merged.update(provided_params)

        return merged

    def get_registered_workers(self) -> List[str]:
        """
        Get list of all registered worker types.

        Useful for debugging and documentation.

        Returns:
            List of worker type strings
        """
        return list(self._registry.keys())
