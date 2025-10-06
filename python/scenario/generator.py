"""
FiniexTestingIDE - Scenario Config System
Auto-Generator (REFACTORED for Issue 2)

ARCHITECTURE CHANGE:
- Generates new config structure with decision_logic_type and worker_types
- Explicit worker configuration with namespaces (CORE/, USER/, BLACKBOX/)
- Separates strategy logic from execution settings
- Supports custom decision logics and workers

FIXED (Config Structure):
- strategy_config stays in global section (not duplicated per scenario)
- Scenarios only contain worker parameter overrides in strategy_config
- Only workers dict gets saved on scenario level (if different from global)
"""

from python.components.logger.bootstrap_logger import setup_logging
from typing import List, Dict, Any, Optional
from datetime import timedelta
import pandas as pd

from python.data_worker.data_loader.analytics import TickDataAnalyzer
from python.framework.types import TestScenario
from python.data_worker.data_loader.core import TickDataLoader
from python.components.logger.bootstrap_logger import setup_logging

setup_logging(name="ScenarioGenerator")
vLog = setup_logging(name="StrategyRunner")


class ScenarioGenerator:
    """
    Generates test scenarios automatically from available data.

    REFACTORED (Issue 2): Now generates new config structure for factory system.
    """

    def __init__(self, data_loader: TickDataLoader):
        """
        Args:
            data_loader: TickDataLoader instance
        """
        self.data_loader = data_loader
        self.analyzer = TickDataAnalyzer(self.data_loader)

    def generate_from_symbol(
        self,
        symbol: str,
        strategy: str = "time_windows",
        decision_logic_type: str = "CORE/simple_consensus",
        worker_types: List[str] = None,
        workers_config: Dict[str, Dict[str, Any]] = None,
        decision_logic_config: Optional[Dict[str, Any]] = None,
        execution_config: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> List[TestScenario]:
        """
        Generate scenarios for a symbol using different strategies.

        REFACTORED (Issue 2): New parameters for factory-driven config.

        Args:
            symbol: Trading symbol
            strategy: Generation strategy ("time_windows", "volatility", "sessions")
            decision_logic_type: DecisionLogic to use (e.g., "CORE/simple_consensus")
            worker_types: List of workers to use (e.g., ["CORE/rsi", "CORE/envelope"])
            workers_config: Explicit worker configurations (overrides defaults)
            decision_logic_config: DecisionLogic-specific config
            execution_config: Execution-specific config (parallelization, etc.)
            **kwargs: Strategy-specific parameters

        Returns:
            List of generated TestScenario objects
        """
        # Default to RSI + Envelope workers if not specified
        if worker_types is None:
            worker_types = ["CORE/rsi", "CORE/envelope"]

        # Build strategy config with new structure
        strategy_config = self._build_strategy_config(
            decision_logic_type=decision_logic_type,
            worker_types=worker_types,
            workers_config=workers_config,
            decision_logic_config=decision_logic_config
        )

        # Generate scenarios based on strategy
        if strategy == "time_windows":
            return self._generate_time_windows(
                symbol,
                strategy_config=strategy_config,
                execution_config=execution_config,
                **kwargs
            )
        elif strategy == "volatility":
            return self._generate_volatility_based(
                symbol,
                strategy_config=strategy_config,
                execution_config=execution_config,
                **kwargs
            )
        elif strategy == "sessions":
            return self._generate_session_based(
                symbol,
                strategy_config=strategy_config,
                execution_config=execution_config,
                **kwargs
            )
        else:
            raise ValueError(f"Unknown strategy: {strategy}")

    def _build_strategy_config(
        self,
        decision_logic_type: str,
        worker_types: List[str],
        workers_config: Optional[Dict[str, Dict[str, Any]]] = None,
        decision_logic_config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Build strategy configuration with new factory-compatible structure.

        This method constructs the hierarchical config that the factories need.
        It sets up sensible defaults for workers and decision logic.

        Args:
            decision_logic_type: Type of decision logic (e.g., "CORE/simple_consensus")
            worker_types: List of worker types to use
            workers_config: Explicit worker configurations (optional)
            decision_logic_config: Decision logic configuration (optional)

        Returns:
            Complete strategy_config dict
        """
        # Start with decision logic type and worker types
        config = {
            "decision_logic_type": decision_logic_type,
            "worker_types": worker_types,
        }

        # Build workers config with defaults
        if workers_config is None:
            workers_config = self._build_default_workers_config(worker_types)

        config["workers"] = workers_config

        # Add decision logic config if provided
        if decision_logic_config:
            config["decision_logic_config"] = decision_logic_config

        return config

    def _build_default_workers_config(
        self,
        worker_types: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Build default worker configurations based on worker types.

        This provides sensible defaults for common workers so users don't
        have to specify every parameter. Users can override these in their
        own configs if needed.

        Args:
            worker_types: List of worker types

        Returns:
            Dict mapping worker types to their default configs
        """
        default_configs = {}

        for worker_type in worker_types:
            # Extract worker name from namespaced type
            _, worker_name = worker_type.split("/", 1)

            # Set defaults based on worker name
            if worker_name == "RSI":
                default_configs[worker_type] = {
                    "period": 14,
                    "timeframe": "M5"
                }
            elif worker_name == "Envelope":
                default_configs[worker_type] = {
                    "period": 20,
                    "deviation": 0.02,
                    "timeframe": "M5"
                }
            elif worker_name == "heavy_rsi":
                default_configs[worker_type] = {
                    "period": 14,
                    "timeframe": "M5",
                    "artificial_load_ms": 5.0
                }
            elif worker_name == "heavy_envelope":
                default_configs[worker_type] = {
                    "period": 20,
                    "deviation": 0.02,
                    "timeframe": "M5",
                    "artificial_load_ms": 8.0
                }
            elif worker_name == "heavy_macd":
                default_configs[worker_type] = {
                    "fast": 12,
                    "slow": 26,
                    "signal": 9,
                    "timeframe": "M5",
                    "artificial_load_ms": 6.0
                }
            else:
                # Unknown worker - leave empty, let factory validation catch it
                vLog.warning(
                    f"No default config for worker '{worker_name}', "
                    f"you may need to provide explicit config"
                )
                default_configs[worker_type] = {}

        return default_configs

    def generate_multi_symbol(
        self,
        symbols: List[str] = None,
        scenarios_per_symbol: int = 3,
        decision_logic_type: str = "CORE/simple_consensus",
        worker_types: List[str] = None,
        workers_config: Dict[str, Dict[str, Any]] = None,
        decision_logic_config: Optional[Dict[str, Any]] = None,
        execution_config: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> List[TestScenario]:
        """
        Generate scenarios for multiple symbols.

        REFACTORED (Issue 2): Uses new config structure.

        Args:
            symbols: List of symbols (None = all available)
            scenarios_per_symbol: Number of scenarios per symbol
            decision_logic_type: DecisionLogic to use
            worker_types: List of workers to use
            workers_config: Explicit worker configurations
            decision_logic_config: DecisionLogic config
            execution_config: Execution config
            **kwargs: Passed to generation strategy

        Returns:
            List of TestScenario objects
        """
        if symbols is None:
            symbols = self.data_loader.list_available_symbols()

        all_scenarios = []

        for symbol in symbols:
            vLog.info(
                f"Generating {scenarios_per_symbol} scenarios for {symbol}")
            scenarios = self.generate_from_symbol(
                symbol,
                strategy="time_windows",
                decision_logic_type=decision_logic_type,
                worker_types=worker_types,
                workers_config=workers_config,
                decision_logic_config=decision_logic_config,
                execution_config=execution_config,
                num_windows=scenarios_per_symbol,
                **kwargs
            )
            all_scenarios.extend(scenarios)

        vLog.info(f"Generated {len(all_scenarios)} scenarios total")
        return all_scenarios

    def _generate_time_windows(
        self,
        symbol: str,
        num_windows: int = 5,
        window_days: int = 2,
        ticks_per_window: int = 1000,
        strategy_config: Optional[Dict[str, Any]] = None,
        execution_config: Optional[Dict[str, Any]] = None,
    ) -> List[TestScenario]:
        """
        Generate scenarios by splitting data into time windows.

        REFACTORED (Issue 2): Uses new config structure.

        Args:
            symbol: Trading symbol
            num_windows: Number of time windows
            window_days: Days per window
            ticks_per_window: Max ticks per window
            strategy_config: Strategy parameters (NEW structure!)
            execution_config: Execution parameters
        """
        # Get available date range
        symbol_info = self.analyzer.get_symbol_info(symbol)

        if "error" in symbol_info:
            vLog.error(
                f"Cannot generate for {symbol}: {symbol_info['error']}")
            return []

        start_date = pd.to_datetime(symbol_info["date_range"]["start"])
        end_date = pd.to_datetime(symbol_info["date_range"]["end"])

        total_days = (end_date - start_date).days

        if total_days < window_days * num_windows:
            vLog.warning(
                f"Not enough data for {num_windows} windows of {window_days} days. "
                f"Reducing to {total_days // window_days} windows."
            )
            num_windows = max(1, total_days // window_days)

        scenarios = []
        window_duration = timedelta(days=window_days)

        # Use provided configs or build defaults
        if strategy_config is None:
            strategy_config = self._build_strategy_config(
                decision_logic_type="CORE/simple_consensus",
                worker_types=["CORE/rsi", "CORE/envelope"],
                workers_config=None,
                decision_logic_config=None
            )

        if execution_config is None:
            execution_config = {
                "parallel_workers": None,  # Auto-detect
                "worker_parallel_threshold_ms": 1.0,
                "adaptive_parallelization": True,
                "log_performance_stats": True,
            }

        # Generate window scenarios
        for i in range(num_windows):
            window_start = start_date + (i * window_duration)
            window_end = window_start + window_duration

            if window_end > end_date:
                window_end = end_date

            # ============================================
            # IMPORTANT: Create TestScenario with FULL strategy_config
            # The ConfigLoader.save_config() will handle extracting
            # only the overrides when writing to JSON
            # ============================================
            scenario = TestScenario(
                symbol=symbol,
                start_date=window_start.strftime("%Y-%m-%d"),
                end_date=window_end.strftime("%Y-%m-%d"),
                max_ticks=ticks_per_window,
                data_mode="realistic",

                # Full config here - save_config() will extract overrides
                strategy_config=strategy_config.copy(),
                execution_config=execution_config.copy(),

                name=f"{symbol}_window_{i+1:02d}"
            )
            scenarios.append(scenario)

        vLog.info(
            f"Generated {len(scenarios)} time window scenarios for {symbol}")
        return scenarios

    def _generate_volatility_based(
        self,
        symbol: str,
        high_vol_threshold: float = 0.02,
        max_scenarios: int = 10,
        strategy_config: Optional[Dict[str, Any]] = None,
        execution_config: Optional[Dict[str, Any]] = None,
    ) -> List[TestScenario]:
        """
        Generate scenarios based on volatility periods.

        TODO: Implement volatility detection.
        For now, falls back to time_windows.
        """
        vLog.warning(
            f"Volatility-based generation not yet implemented. Using time_windows.")
        return self._generate_time_windows(
            symbol,
            num_windows=max_scenarios,
            strategy_config=strategy_config,
            execution_config=execution_config,
        )

    def _generate_session_based(
        self,
        symbol: str,
        sessions: List[str] = None,
        strategy_config: Optional[Dict[str, Any]] = None,
        execution_config: Optional[Dict[str, Any]] = None,
    ) -> List[TestScenario]:
        """
        Generate scenarios based on trading sessions.

        TODO: Implement session-based generation.
        For now, falls back to time_windows.
        """
        vLog.warning(
            f"Session-based generation not yet implemented. Using time_windows.")
        return self._generate_time_windows(
            symbol,
            num_windows=3,
            strategy_config=strategy_config,
            execution_config=execution_config,
        )
