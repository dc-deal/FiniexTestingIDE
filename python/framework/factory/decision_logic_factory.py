"""
FiniexTestingIDE - Decision Logic Factory ()
Config-driven decision logic instantiation with namespace support

:
- DecisionTradingApi is injected later via set_trading_api()

The DecisionLogic Factory mirrors the Worker Factory pattern.
It resolves decision logic types from config strings and instantiates
the appropriate strategy classes.

Namespace System:
- CORE/logic_name → Framework decision logics (framework/decision_logic/core/)
- USER/logic_name → User custom decision logics (decision_logic/user/)
- BLACKBOX/logic_name → IP-protected decision logics (decision_logic/blackbox/)

Example Config:
{
    "decision_logic_type": "CORE/simple_consensus",
    "decision_logic_config": {
        "rsi_oversold": 25,
        "min_confidence": 0.7
    }
}
"""

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

from python.framework.decision_logic.core.backtesting.backtesting_deterministic import BacktestingDeterministic
from python.framework.decision_logic.core.cautious_macd import CautiousMacd
from python.framework.decision_logic.core.backtesting.backtesting_margin_stress import BacktestingMarginStress
from python.framework.decision_logic.core.backtesting.backtesting_multi_position import BacktestingMultiPosition
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.types.market_types.market_types import TradingContext
from python.framework.types.parameter_types import ValidatedParameters
from python.framework.validators.parameter_validator import apply_defaults


class DecisionLogicFactory:
    """
    Factory for creating decision logic instances from configuration.

    This factory provides the same config-driven instantiation pattern
    for decision logics as the Worker Factory does for workers.

    It handles:
    1. Namespace resolution (CORE/ → framework, USER/ → custom, etc.)
    2. Dynamic loading and registration
    3. Configuration injection
    4. Validation and error handling
    """

    def __init__(
        self,
        logger: AbstractLogger,
        strict_parameter_validation: bool = True
    ):
        """
        Initialize decision logic factory with empty registry.

        Args:
            logger: Logger instance
            strict_parameter_validation: True = raise on boundary violations, False = warn only
        """
        self.logger = logger
        self._strict_validation = strict_parameter_validation
        self._registry: Dict[str, Type[AbstractDecisionLogic]] = {}
        self._extra_dirs: List[str] = []
        self._load_core_logics()
        self._scan_user_namespace()

    def _load_core_logics(self):
        """
        Pre-register core framework decision logics.

        Core decision logics are part of the framework and always available.
        They live in python/framework/decision_logic/core/.

        This method is called during factory initialization to ensure
        core strategies are immediately available.
        """
        try:
            from python.framework.decision_logic.core.simple_consensus import \
                SimpleConsensus
            from python.framework.decision_logic.core.aggressive_trend import \
                AggressiveTrend

            # Register with CORE namespace
            self._registry["CORE/simple_consensus"] = SimpleConsensus
            self._registry["CORE/aggressive_trend"] = AggressiveTrend
            self._registry["CORE/cautious_macd"] = CautiousMacd

            # Backtesting decision logics
            self._registry["CORE/backtesting/backtesting_deterministic"] = BacktestingDeterministic
            self._registry["CORE/backtesting/backtesting_margin_stress"] = BacktestingMarginStress
            self._registry["CORE/backtesting/backtesting_multi_position"] = BacktestingMultiPosition

            self.logger.debug(
                f"Core decision logics registered: {list(self._registry.keys())}"
            )
        except ImportError as e:
            self.logger.warning(f"Failed to load core decision logics: {e}")

    def set_extra_dirs(self, extra_dirs: List[str]):
        """
        Set additional USER decision logic directories and re-scan.

        Args:
            extra_dirs: List of external directory paths to scan
        """
        self._extra_dirs = extra_dirs
        self._scan_user_namespace()

    def _scan_user_namespace(self):
        """
        Scan USER directories for decision logic modules and register them.

        Scans the default directory (python/decision_logic/user/) plus any
        extra directories configured via app_config.json paths.user_decision_logic_dirs.
        Broken modules are skipped with a warning log.
        """
        default_dir = Path('python/decision_logic/user')
        scan_dirs = [default_dir] + [Path(d) for d in self._extra_dirs]

        discovered = 0
        for scan_dir in scan_dirs:
            is_external = scan_dir != default_dir
            if not scan_dir.exists():
                if is_external:
                    self.logger.warning(
                        f"USER decision logic directory not found: {scan_dir}")
                return
            if not scan_dir.is_dir():
                continue

            for py_file in sorted(scan_dir.glob('*.py')):
                if py_file.name.startswith('TEMPLATE_'):
                    continue
                if py_file.name.startswith('__'):
                    continue

                stem = py_file.stem
                logic_type = f'USER/{stem}'

                # Derive class name: snake_case → PascalCase (no suffix)
                class_name = ''.join(
                    word.capitalize() for word in stem.split('_'))

                try:
                    if is_external:
                        # External dir: load by file location (no sys.path pollution)
                        spec = importlib.util.spec_from_file_location(
                            f'user_ext.decision_logic.{stem}', str(py_file))
                        module = importlib.util.module_from_spec(spec)
                        sys.modules[spec.name] = module
                        spec.loader.exec_module(module)
                    else:
                        # Default dir: standard import
                        module_path = f'python.decision_logic.user.{stem}'
                        module = importlib.import_module(module_path)

                    if not hasattr(module, class_name):
                        found = [n for n in dir(module) if not n.startswith('_')]
                        self.logger.warning(
                            f"Skipping USER/{stem}: expected class '{class_name}' "
                            f"(derived from filename '{py_file.name}'), "
                            f"but not found. Module contains: {found}. "
                            f"Rename the class or the file to match "
                            f"(see docs/user_guides/user_modules_and_hot_reload_mechanics.md)")
                        continue

                    logic_class = getattr(module, class_name)

                    if not issubclass(logic_class, AbstractDecisionLogic):
                        self.logger.warning(
                            f"Skipping USER/{stem}: {class_name} does not "
                            f"inherit from AbstractDecisionLogic")
                        continue

                    if logic_type in self._registry:
                        self.logger.warning(
                            f"USER/{stem} overrides previous registration "
                            f"(from {scan_dir})")

                    self._registry[logic_type] = logic_class
                    discovered += 1

                except (SyntaxError, ImportError) as e:
                    self.logger.warning(
                        f"Skipping USER/{stem}: {type(e).__name__}: {e}")
                except Exception as e:
                    self.logger.warning(
                        f"Skipping USER/{stem}: unexpected error: "
                        f"{type(e).__name__}: {e}")

        if discovered > 0:
            self.logger.debug(
                f"USER decision logics discovered: {discovered}")

    def rescan(self):
        """
        Hot-reload USER namespace: clear cached modules, re-scan directories.

        Removes all USER/ entries from the registry and invalidates
        Python's module cache for user modules, then re-scans.
        Prepared for Issue #21 REPL shell integration.
        """
        # Clear USER entries from registry
        self._registry = {
            k: v for k, v in self._registry.items()
            if not k.startswith('USER/')
        }

        # Invalidate sys.modules for user decision logic modules
        stale_keys = [
            key for key in sys.modules
            if key.startswith('python.decision_logic.user.')
            or key.startswith('user_ext.decision_logic.')
        ]
        for key in stale_keys:
            del sys.modules[key]

        # Re-scan
        self._scan_user_namespace()

    def register_logic(
        self,
        logic_type: str,
        logic_class: Type[AbstractDecisionLogic]
    ):
        """
        Manually register a decision logic class.

        This method allows runtime registration of custom decision logics.
        Useful for plugins or dynamically loaded strategies.

        Args:
            logic_type: Full logic type with namespace (e.g., "USER/my_strategy")
            logic_class: Logic class (must inherit from AbstractDecisionLogic)

        Raises:
            ValueError: If logic_class doesn't inherit from AbstractDecisionLogic
        """
        if not issubclass(logic_class, AbstractDecisionLogic):
            raise ValueError(
                f"Logic class {logic_class.__name__} must inherit from "
                f"AbstractDecisionLogic"
            )

        self._registry[logic_type] = logic_class
        self.logger.debug(
            f"Registered decision logic: {logic_type} → {logic_class.__name__}")

    def create_logic(
        self,
        logic_type: str,
        logger: ScenarioLogger,
        logic_config: Dict[str, Any] = None,
        trading_context: TradingContext = None
    ) -> AbstractDecisionLogic:
        """
        Create a decision logic instance from configuration.

        No longer accepts trading_env parameter.
        DecisionTradingApi is injected later via set_trading_api().

        This is the main entry point for decision logic creation. It:
        1. Resolves the logic type to a logic class
        2. Instantiates the logic with provided configuration
        3. Returns a ready-to-use decision logic instance

        Args:
            logic_type: Logic type with namespace (e.g., "CORE/simple_consensus")
            logic_config: Configuration dict for the logic

        Returns:
            Instantiated decision logic ready for use

        Raises:
            ValueError: If logic type not found
        """
        logic_config = logic_config or {}

        # Step 1: Resolve logic class
        logic_class = self._resolve_logic_class(logic_type)

        # Step 2: Validate parameters against schema
        warnings = logic_class.validate_parameter_schema(
            logic_config, strict=self._strict_validation
        )
        for warning in warnings:
            self.logger.warning(f"⚠️ {warning}")

        # Step 3: Apply schema defaults to config
        logic_config = apply_defaults(
            logic_config, logic_class.get_parameter_schema()
        )

        # Step 3.5: Inject decision_logic_type for performance tracking
        logic_config['decision_logic_type'] = logic_type

        # Step 4: Extract simple name for instance
        logic_name = self._extract_logic_name(logic_type)

        # Step 4.5: Wrap validated+defaulted config into ValidatedParameters
        validated_config = ValidatedParameters(logic_config)

        # Step 5: Instantiate logic with validated parameters
        logic_instance = logic_class(
            name=logic_name,
            logger=logger,
            config=validated_config,
            trading_context=trading_context
        )

        logger.debug(
            f"✅ Created decision logic: {logic_type} with {len(logic_config)} config values"
        )

        return logic_instance

    def _resolve_logic_class(self, logic_type: str) -> Type[AbstractDecisionLogic]:
        """
        Resolve logic type string to logic class.

        Supports:
        - CORE/simple_consensus → framework/decision_logic/core/simple_consensus.py
        - USER/my_strategy → decision_logic/user/my_strategy.py
        - BLACKBOX/secret → decision_logic/blackbox/secret.pyc (Post-MVP)

        Args:
            logic_type: Full logic type with namespace

        Returns:
            Logic class ready for instantiation

        Raises:
            ValueError: If logic type not found or invalid
        """
        # Check registry first (CORE logics pre-loaded)
        if logic_type in self._registry:
            return self._registry[logic_type]

        # Not in registry - try dynamic loading
        return self._load_custom_logic(logic_type)

    def _load_custom_logic(self, logic_type: str) -> Type[AbstractDecisionLogic]:
        """
        Dynamically load custom decision logic from USER namespace.

        BLACKBOX namespace is prepared but feature-gated for Post-MVP.

        Args:
            logic_type: Full logic type (e.g., "USER/my_strategy")

        Returns:
            Logic class

        Raises:
            ValueError: If logic cannot be loaded
        """
        # Parse namespace
        if "/" not in logic_type:
            raise ValueError(
                f"Invalid logic type format: {logic_type}. "
                f"Expected 'NAMESPACE/logic_name' (e.g., 'USER/my_strategy')"
            )

        namespace, logic_name = logic_type.split("/", 1)

        # ============================================
        # BLACKBOX: Feature-gated for Post-MVP
        # ============================================
        if namespace == "BLACKBOX":
            raise NotImplementedError(
                f"BLACKBOX decision logics are not yet implemented (Post-MVP). "
                f"Requested: {logic_type}"
            )

        # ============================================
        # USER: Active Loading
        # ============================================
        if namespace == "USER":
            module_path = f"python.decision_logic.user.{logic_name}"
        else:
            raise ValueError(f"Unknown namespace: {namespace}")

        # Try to import module
        try:
            module = importlib.import_module(module_path)

            # Find logic class in module (convention: capitalized name)
            class_name = "".join(word.capitalize()
                                 for word in logic_name.split("_"))

            if not hasattr(module, class_name):
                found = [n for n in dir(module) if not n.startswith('_')]
                raise AttributeError(
                    f"Expected class '{class_name}' in '{module_path}' "
                    f"(derived from filename). "
                    f"Module contains: {found}. "
                    f"Rename the class or the file to match "
                    f"(see docs/user_guides/user_modules_and_hot_reload_mechanics.md)")

            logic_class = getattr(module, class_name)

            # Register for future use
            self._registry[logic_type] = logic_class

            self.logger.info(
                f"Dynamically loaded decision logic: {logic_type}")
            return logic_class

        except (ImportError, AttributeError, SyntaxError) as e:
            raise ValueError(
                f"Failed to load custom decision logic {logic_type}: {e}"
            ) from e

    def _extract_logic_name(self, logic_type: str) -> str:
        """
        Extract simple logic name from full type.

        Examples:
            "CORE/simple_consensus" → "simple_consensus"
            "USER/my_custom_strategy" → "my_custom_strategy"

        Args:
            logic_type: Full logic type with namespace

        Returns:
            Simple logic name
        """
        _, logic_name = logic_type.split("/", 1)
        return logic_name

    def get_registered_logics(self) -> list:
        """
        Get list of all registered decision logic types.

        Useful for debugging and documentation.

        Returns:
            List of logic type strings
        """
        return list(self._registry.keys())
