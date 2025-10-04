"""
FiniexTestingIDE - Decision Logic Factory
Config-driven decision logic instantiation with namespace support

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
from python.components.logger.bootstrap_logger import setup_logging
from typing import Any, Dict, Type

from python.framework.decision_logic.abstract_decision_logic import \
    AbstractDecisionLogic

vLog = setup_logging(name="StrategyRunner")


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

    def __init__(self):
        """
        Initialize decision logic factory with empty registry.

        The registry is populated lazily when decision logics are requested.
        This avoids import overhead for unused strategies.
        """
        self._registry: Dict[str, Type[AbstractDecisionLogic]] = {}
        self._load_core_logics()

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

            vLog.debug(
                f"Core decision logics registered: {list(self._registry.keys())}"
            )
        except ImportError as e:
            vLog.warning(f"Failed to load core decision logics: {e}")

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
        vLog.debug(
            f"Registered decision logic: {logic_type} → {logic_class.__name__}")

    def create_logic(
        self,
        logic_type: str,
        logic_config: Dict[str, Any] = None
    ) -> AbstractDecisionLogic:
        """
        Create a decision logic instance from configuration.

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

        # Step 2: Extract simple name for instance
        logic_name = self._extract_logic_name(logic_type)

        # Step 3: Instantiate logic with config
        logic_instance = logic_class(
            name=logic_name,
            config=logic_config
        )

        vLog.debug(
            f"✓ Created decision logic: {logic_type} with {len(logic_config)} config values"
        )

        return logic_instance

    def create_logic_from_strategy_config(
        self,
        strategy_config: Dict[str, Any]
    ) -> AbstractDecisionLogic:
        """
        Create decision logic from complete strategy configuration.

        This is the batch creation method used by orchestrator.
        It extracts the decision logic type and config from strategy_config.

        Expected config structure:
        {
            "decision_logic_type": "CORE/simple_consensus",
            "decision_logic_config": {
                "rsi_oversold": 30,
                "min_confidence": 0.6
            }
        }

        Args:
            strategy_config: Strategy configuration dict

        Returns:
            Instantiated decision logic

        Raises:
            ValueError: If decision_logic_type not specified or invalid
        """
        # Extract decision logic type
        logic_type = strategy_config.get("decision_logic_type")

        if not logic_type:
            raise ValueError(
                "No decision_logic_type specified in strategy_config. "
                "Example: 'decision_logic_type': 'CORE/simple_consensus'"
            )

        # Extract decision logic config (optional)
        logic_config = strategy_config.get("decision_logic_config", {})

        # Create logic instance
        try:
            logic_instance = self.create_logic(logic_type, logic_config)
            return logic_instance

        except Exception as e:
            vLog.error(f"Failed to create decision logic {logic_type}: {e}")
            raise ValueError(
                f"Decision logic creation failed for {logic_type}: {e}")

    def _resolve_logic_class(
        self,
        logic_type: str
    ) -> Type[AbstractDecisionLogic]:
        """
        Resolve logic type string to logic class.

        This method handles the namespace-to-class mapping.
        If logic is not in registry, attempts to load it dynamically.

        Args:
            logic_type: Full logic type (e.g., "CORE/simple_consensus")

        Returns:
            Decision logic class

        Raises:
            ValueError: If logic type not found or invalid
        """
        # Check if already registered
        if logic_type in self._registry:
            return self._registry[logic_type]

        # Attempt dynamic loading for USER/BLACKBOX logics
        if logic_type.startswith("USER/") or logic_type.startswith("BLACKBOX/"):
            return self._load_custom_logic(logic_type)

        # Logic not found
        raise ValueError(
            f"Unknown decision logic type: {logic_type}. "
            f"Available logics: {list(self._registry.keys())}"
        )

    def _load_custom_logic(
        self,
        logic_type: str
    ) -> Type[AbstractDecisionLogic]:
        """
        Dynamically load custom decision logic from USER or BLACKBOX namespace.

        This enables hot-loading of custom strategies without pre-registration.

        Args:
            logic_type: Logic type (e.g., "USER/my_custom_strategy")

        Returns:
            Decision logic class

        Raises:
            NotImplementedError: If BLACKBOX namespace (Post-MVP feature)
            ValueError: If logic cannot be loaded
        """
        namespace, logic_name = logic_type.split("/", 1)

        # ============================================
        # BLACKBOX: Post-MVP Feature Gate
        # ============================================
        if namespace == "BLACKBOX":
            raise NotImplementedError(
                f"BlackBox decision logics are a Post-MVP feature.\n"
                f"'{logic_type}' cannot be loaded yet.\n"
                f"BlackBox loading will support encrypted/compiled decision logics "
                f"in future releases.\n"
                f"For now, use CORE/ or USER/ namespace decision logics."
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

            logic_class = getattr(module, class_name)

            # Register for future use
            self._registry[logic_type] = logic_class

            vLog.info(f"Dynamically loaded decision logic: {logic_type}")
            return logic_class

        except (ImportError, AttributeError) as e:
            raise ValueError(
                f"Failed to load custom decision logic {logic_type}: {e}"
            )

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
