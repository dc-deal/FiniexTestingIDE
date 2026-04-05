"""
FiniexTestingIDE - Worker Factory
Config-driven worker instantiation with path-based loading

The Factory is responsible for:
1. Resolving worker types from config (e.g., "CORE/rsi" → RsiWorker class)
2. Validating required parameters are provided
3. Merging user config with worker defaults
4. Instantiating workers with correct parameters

Reference System:
- CORE/worker_name → Framework workers (python/framework/workers/core/)
- File path         → Any .py file containing exactly one AbstractWorker subclass
                      (absolute, or relative to project root)
"""

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Type

from python.configuration.app_config_manager import AppConfigManager
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.market_types.market_types import TradingContext
from python.framework.types.parameter_types import ValidatedParameters
from python.framework.validators.parameter_validator import apply_defaults
from python.framework.workers.abstract_worker import AbstractWorker
from python.framework.workers.core.backtesting.backtesting_sample_worker import BacktestingSampleWorker
from python.framework.workers.core.macd_worker import MacdWorker
from python.framework.workers.core.rsi_worker import RsiWorker
from python.framework.workers.core.envelope_worker import EnvelopeWorker
from python.framework.workers.core.backtesting.heavy_rsi_worker import HeavyRsiWorker
from python.framework.workers.core.obv_worker import ObvWorker


class WorkerFactory:
    """
    Factory for creating workers from configuration.

    This is the central registry and instantiation system for all workers.
    It maintains a mapping of worker names to worker classes and handles
    the complete lifecycle of worker creation.

    CORE workers are pre-registered by name (e.g. "CORE/rsi").
    User workers are loaded on-demand from file paths. The factory uses
    introspection to find the one AbstractWorker subclass in the file.
    """

    def __init__(
        self,
        logger: ScenarioLogger,
        strict_parameter_validation: bool = True
    ):
        """
        Initialize worker factory with core worker registry.

        Args:
            logger: ScenarioLogger instance
            strict_parameter_validation: True = raise on boundary violations, False = warn only
        """
        self._logger = logger
        self._strict_validation = strict_parameter_validation
        # Registry: type_string → (class, source_path_or_None)
        self._registry: Dict[str, Tuple[Type[AbstractWorker], Optional[Path]]] = {}
        self._load_core_workers()

    def _load_core_workers(self):
        """
        Pre-register core framework workers.

        Core workers are part of the framework and always available.
        They live in python/framework/workers/core/.
        """
        try:
            self._registry['CORE/rsi'] = (RsiWorker, None)
            self._registry['CORE/envelope'] = (EnvelopeWorker, None)
            self._registry['CORE/heavy_rsi'] = (HeavyRsiWorker, None)
            self._registry['CORE/macd'] = (MacdWorker, None)
            self._registry['CORE/obv'] = (ObvWorker, None)
            self._registry['CORE/backtesting/backtesting_sample_worker'] = (BacktestingSampleWorker, None)

            self._logger.debug(
                f"Core workers registered: {list(self._registry.keys())}"
            )
        except ImportError as e:
            self._logger.warning(f"Failed to load core workers: {e}")

    def rescan(self):
        """
        Clear all path-loaded workers from the registry and module cache.

        Keeps CORE entries intact. Re-loading happens on next access.
        Used for hot-reload in development / REPL scenarios.
        """
        self._registry = {
            k: v for k, v in self._registry.items()
            if k.startswith('CORE/')
        }
        stale_keys = [k for k in sys.modules if k.startswith('user_loaded.')]
        for key in stale_keys:
            del sys.modules[key]

    def register_worker(
        self,
        worker_type: str,
        worker_class: Type[AbstractWorker]
    ):
        """
        Manually register a worker class.

        Args:
            worker_type: Key for the registry (e.g. "CORE/my_worker" or a file path)
            worker_class: Worker class (must inherit from AbstractWorker)

        Raises:
            ValueError: If worker_class doesn't inherit from AbstractWorker
        """
        if not issubclass(worker_class, AbstractWorker):
            raise ValueError(
                f"Worker class {worker_class.__name__} must inherit from AbstractWorker"
            )
        self._registry[worker_type] = (worker_class, None)
        self._logger.debug(
            f"Registered worker: {worker_type} → {worker_class.__name__}")

    def create_worker(
        self,
        instance_name: str,
        worker_type: str,
        worker_config: Dict[str, Any] = None,
        trading_context: TradingContext = None,
        base_path: Optional[Path] = None,
    ) -> AbstractWorker:
        """
        Create a worker instance with validation.

        Args:
            instance_name: User-defined instance name (e.g., "rsi_main")
            worker_type: Worker reference — "CORE/rsi" or a file path
            worker_config: User-provided parameters for this worker
            trading_context: Optional trading context
            base_path: Base directory for resolving relative file paths

        Returns:
            Instantiated worker ready for use

        Raises:
            ValueError: If worker type not found or required parameters missing
        """
        worker_config = worker_config or {}

        worker_class, _ = self._resolve_worker_class(worker_type, base_path)

        worker_class.validate_config(worker_config)

        warnings = worker_class.validate_parameter_schema(
            worker_config, strict=self._strict_validation
        )
        for warning in warnings:
            self._logger.warning(f"⚠️ {warning}")

        merged_params = apply_defaults(
            worker_config, worker_class.get_parameter_schema()
        )

        # Inject resolved worker_type for performance tracking
        resolved_key = self._resolve_key(worker_type, base_path)
        merged_params['worker_type'] = resolved_key

        validated_params = ValidatedParameters(merged_params)

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
        trading_context: TradingContext = None,
        base_path: Optional[Path] = None,
    ) -> Dict[str, AbstractWorker]:
        """
        Create all workers from strategy configuration.

        Args:
            strategy_config: Strategy configuration dict
            trading_context: Optional trading context
            base_path: Base directory for resolving relative file paths

        Returns:
            Dict mapping worker instance names to worker instances
        """
        worker_instances = strategy_config.get('worker_instances', {})
        workers_config = strategy_config.get('workers', {})

        if not worker_instances:
            raise ValueError('No worker_instances specified in strategy_config')

        created_workers = {}

        for instance_name, worker_type in worker_instances.items():
            worker_config = workers_config.get(instance_name, {})

            try:
                worker_instance = self.create_worker(
                    instance_name=instance_name,
                    worker_type=worker_type,
                    worker_config=worker_config,
                    trading_context=trading_context,
                    base_path=base_path,
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

    def _resolve_key(self, worker_type: str, base_path: Optional[Path] = None) -> str:
        """
        Normalize a worker type string to a canonical key.

        CORE references are returned as-is. File paths are resolved to
        absolute strings.

        Args:
            worker_type: Worker reference string
            base_path: Base directory for relative paths

        Returns:
            Canonical string key for registry/tracking
        """
        if worker_type.startswith('CORE/'):
            return worker_type
        p = Path(worker_type)
        if p.is_absolute():
            return str(p)
        if base_path:
            return str((base_path / p).resolve())
        return str((Path.cwd() / p).resolve())

    def _resolve_worker_class(
        self,
        worker_type: str,
        base_path: Optional[Path] = None,
    ) -> Tuple[Type[AbstractWorker], Optional[Path]]:
        """
        Resolve worker type string to (class, source_path).

        Args:
            worker_type: "CORE/name" or file path
            base_path: Base directory for relative paths

        Returns:
            Tuple of (worker class, source file path or None)

        Raises:
            ValueError: If worker type not found or invalid
        """
        if worker_type in self._registry:
            return self._registry[worker_type]

        if worker_type.startswith('CORE/'):
            raise ValueError(
                f"Unknown CORE worker: '{worker_type}'. "
                f"Available: {[k for k in self._registry if k.startswith('CORE/')]}"
            )

        # File path reference — load on demand
        return self._load_path_worker(worker_type, base_path)

    def _load_path_worker(
        self,
        path_str: str,
        base_path: Optional[Path] = None,
    ) -> Tuple[Type[AbstractWorker], Path]:
        """
        Load a worker from a .py file via introspection.

        Finds exactly one AbstractWorker subclass in the file. Caches result
        in registry under the normalized absolute path string.

        Args:
            path_str: File path (absolute or relative to base_path / project root)
            base_path: Base directory for relative paths

        Returns:
            Tuple of (worker class, resolved source path)

        Raises:
            ValueError: If file not found, or not exactly one AbstractWorker subclass
        """
        p = Path(path_str)
        if not p.is_absolute():
            if base_path:
                p = (base_path / p).resolve()
            else:
                p = (Path.cwd() / p).resolve()

        cache_key = str(p)
        if cache_key in self._registry:
            return self._registry[cache_key]

        if not p.exists():
            raise ValueError(
                f"Worker file not found: '{p}' "
                f"(resolved from '{path_str}')"
            )

        module_name = f'user_loaded.worker.{p.stem}'
        try:
            spec = importlib.util.spec_from_file_location(module_name, str(p))
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except (SyntaxError, ImportError) as e:
            raise ValueError(f"Failed to load worker file '{p}': {e}") from e

        candidates = [
            cls for cls in vars(module).values()
            if isinstance(cls, type)
            and issubclass(cls, AbstractWorker)
            and cls is not AbstractWorker
        ]

        if len(candidates) != 1:
            raise ValueError(
                f"Expected exactly 1 AbstractWorker subclass in '{p}', "
                f"found {len(candidates)}: {[c.__name__ for c in candidates]}"
            )

        worker_class = candidates[0]
        self._registry[cache_key] = (worker_class, p)

        self._logger.debug(f"Loaded worker from path: {p} → {worker_class.__name__}")
        return worker_class, p
