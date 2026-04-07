"""
FiniexTestingIDE - Decision Logic Factory
Config-driven decision logic instantiation with path-based loading

Reference System:
- CORE/logic_name → Framework decision logics (python/framework/decision_logic/core/)
- File path        → Any .py file containing exactly one AbstractDecisionLogic subclass
                     (absolute, or relative to project root)

Example Config:
{
    "decision_logic_type": "user_algos/my_algo/my_strategy.py",
    "decision_logic_config": {
        "lot_size": 0.01,
        "min_free_margin": 100.0
    }
}
"""

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Type

from python.framework.decision_logic.core.backtesting.backtesting_deterministic import BacktestingDeterministic
from python.framework.decision_logic.core.cautious_macd import CautiousMacd
from python.framework.decision_logic.core.backtesting.backtesting_margin_stress import BacktestingMarginStress
from python.framework.decision_logic.core.backtesting.backtesting_multi_position import BacktestingMultiPosition
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.types.market_types.market_types import TradingContext
from python.framework.types.parameter_types import InputParamDef, ValidatedParameters
from python.framework.validators.parameter_validator import apply_defaults, validate_parameters


class DecisionLogicFactory:
    """
    Factory for creating decision logic instances from configuration.

    CORE decision logics are pre-registered by name (e.g. "CORE/simple_consensus").
    User decision logics are loaded on-demand from file paths. The factory uses
    introspection to find the one AbstractDecisionLogic subclass in the file.
    """

    def __init__(
        self,
        logger: AbstractLogger,
        strict_parameter_validation: bool = True
    ):
        """
        Initialize decision logic factory with core registry.

        Args:
            logger: Logger instance
            strict_parameter_validation: True = raise on boundary violations, False = warn only
        """
        self.logger = logger
        self._strict_validation = strict_parameter_validation
        # Registry: type_string → (class, source_path_or_None)
        self._registry: Dict[str, Tuple[Type[AbstractDecisionLogic], Optional[Path]]] = {}
        self._load_core_logics()

    def _load_core_logics(self):
        """
        Pre-register core framework decision logics.

        Core decision logics are part of the framework and always available.
        They live in python/framework/decision_logic/core/.
        """
        try:
            from python.framework.decision_logic.core.simple_consensus import SimpleConsensus
            from python.framework.decision_logic.core.aggressive_trend import AggressiveTrend

            self._registry['CORE/simple_consensus'] = (SimpleConsensus, None)
            self._registry['CORE/aggressive_trend'] = (AggressiveTrend, None)
            self._registry['CORE/cautious_macd'] = (CautiousMacd, None)
            self._registry['CORE/backtesting/backtesting_deterministic'] = (BacktestingDeterministic, None)
            self._registry['CORE/backtesting/backtesting_margin_stress'] = (BacktestingMarginStress, None)
            self._registry['CORE/backtesting/backtesting_multi_position'] = (BacktestingMultiPosition, None)

            self.logger.debug(
                f"Core decision logics registered: {list(self._registry.keys())}"
            )
        except ImportError as e:
            self.logger.warning(f"Failed to load core decision logics: {e}")

    def rescan(self):
        """
        Clear all path-loaded decision logics from the registry and module cache.

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

    def register_logic(
        self,
        logic_type: str,
        logic_class: Type[AbstractDecisionLogic]
    ):
        """
        Manually register a decision logic class.

        Args:
            logic_type: Key for the registry (e.g. "CORE/my_logic" or a file path)
            logic_class: Logic class (must inherit from AbstractDecisionLogic)

        Raises:
            ValueError: If logic_class doesn't inherit from AbstractDecisionLogic
        """
        if not issubclass(logic_class, AbstractDecisionLogic):
            raise ValueError(
                f"Logic class {logic_class.__name__} must inherit from AbstractDecisionLogic"
            )
        self._registry[logic_type] = (logic_class, None)
        self.logger.debug(
            f"Registered decision logic: {logic_type} → {logic_class.__name__}")

    def create_logic(
        self,
        logic_type: str,
        logger: ScenarioLogger,
        logic_config: Dict[str, Any] = None,
        trading_context: TradingContext = None,
        base_path: Optional[Path] = None,
    ) -> AbstractDecisionLogic:
        """
        Create a decision logic instance from configuration.

        Args:
            logic_type: Logic reference — "CORE/simple_consensus" or a file path
            logger: ScenarioLogger instance
            logic_config: Configuration dict for the logic
            trading_context: Optional trading context
            base_path: Base directory for resolving relative file paths

        Returns:
            Instantiated decision logic ready for use

        Raises:
            ValueError: If logic type not found
        """
        logic_config = logic_config or {}

        logic_class, source_path = self.resolve_logic_class(logic_type, base_path)

        schema = logic_class.get_parameter_schema()
        if trading_context and trading_context.volume_min > 0 and 'lot_size' in schema:
            lot_def = schema['lot_size']
            schema = dict(schema)
            schema['lot_size'] = InputParamDef(
                param_type=lot_def.param_type,
                default=lot_def.default,
                min_val=trading_context.volume_min,
                max_val=lot_def.max_val,
                description=lot_def.description,
            )

        warnings = validate_parameters(
            logic_config, schema, self._strict_validation,
            context_name=logic_class.__name__
        )
        for warning in warnings:
            self.logger.warning(f"⚠️ {warning}")

        logic_config = apply_defaults(logic_config, schema)

        # Inject resolved logic_type for performance tracking
        resolved_key = self._resolve_key(logic_type, base_path)
        logic_config['decision_logic_type'] = resolved_key

        logic_name = self._extract_logic_name(logic_type)

        validated_config = ValidatedParameters(logic_config)

        logic_instance = logic_class(
            name=logic_name,
            logger=logger,
            config=validated_config,
            trading_context=trading_context
        )

        # Inject source path so WorkerOrchestrator can resolve relative worker refs
        if source_path is not None:
            logic_instance._source_path = source_path

        logger.debug(
            f"✅ Created decision logic: {logic_type} with {len(logic_config)} config values"
        )

        return logic_instance

    def _resolve_key(self, logic_type: str, base_path: Optional[Path] = None) -> str:
        """
        Normalize a logic type string to a canonical key.

        Args:
            logic_type: Logic reference string
            base_path: Base directory for relative paths

        Returns:
            Canonical string key
        """
        if logic_type.startswith('CORE/'):
            return logic_type
        p = Path(logic_type)
        if p.is_absolute():
            return str(p)
        if base_path:
            return str((base_path / p).resolve())
        return str((Path.cwd() / p).resolve())

    def resolve_logic_class(
        self,
        logic_type: str,
        base_path: Optional[Path] = None,
    ) -> Tuple[Type[AbstractDecisionLogic], Optional[Path]]:
        """
        Resolve logic type string to (class, source_path).

        Args:
            logic_type: "CORE/name" or file path
            base_path: Base directory for relative paths

        Returns:
            Tuple of (logic class, source file path or None)

        Raises:
            ValueError: If logic type not found or invalid
        """
        if logic_type in self._registry:
            return self._registry[logic_type]

        if logic_type.startswith('CORE/'):
            raise ValueError(
                f"Unknown CORE decision logic: '{logic_type}'. "
                f"Available: {[k for k in self._registry if k.startswith('CORE/')]}"
            )

        return self._load_path_logic(logic_type, base_path)

    def _load_path_logic(
        self,
        path_str: str,
        base_path: Optional[Path] = None,
    ) -> Tuple[Type[AbstractDecisionLogic], Path]:
        """
        Load a decision logic from a .py file via introspection.

        Finds exactly one AbstractDecisionLogic subclass in the file. Caches
        result in registry under the normalized absolute path string.

        Args:
            path_str: File path (absolute or relative to base_path / project root)
            base_path: Base directory for relative paths

        Returns:
            Tuple of (logic class, resolved source path)

        Raises:
            ValueError: If file not found, or not exactly one AbstractDecisionLogic subclass
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
                f"Decision logic file not found: '{p}' "
                f"(resolved from '{path_str}')"
            )

        module_name = f'user_loaded.logic.{p.stem}'
        try:
            spec = importlib.util.spec_from_file_location(module_name, str(p))
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except (SyntaxError, ImportError) as e:
            raise ValueError(f"Failed to load decision logic file '{p}': {e}") from e

        candidates = [
            cls for cls in vars(module).values()
            if isinstance(cls, type)
            and issubclass(cls, AbstractDecisionLogic)
            and cls is not AbstractDecisionLogic
        ]

        if len(candidates) != 1:
            raise ValueError(
                f"Expected exactly 1 AbstractDecisionLogic subclass in '{p}', "
                f"found {len(candidates)}: {[c.__name__ for c in candidates]}"
            )

        logic_class = candidates[0]
        self._registry[cache_key] = (logic_class, p)

        self.logger.debug(f"Loaded decision logic from path: {p} → {logic_class.__name__}")
        return logic_class, p

    def _extract_logic_name(self, logic_type: str) -> str:
        """
        Extract a simple name from the logic type string.

        Args:
            logic_type: Full logic type (CORE reference or file path)

        Returns:
            Simple name for the instance
        """
        if logic_type.startswith('CORE/'):
            return logic_type.split('/', 1)[1]
        return Path(logic_type).stem

    def get_registered_logics(self) -> list:
        """
        Get list of all registered decision logic types.

        Returns:
            List of logic type strings
        """
        return list(self._registry.keys())
