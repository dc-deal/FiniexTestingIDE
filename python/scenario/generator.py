"""
FiniexTestingIDE - Scenario Config System
Auto-Generator (REFACTORED for Worker Instance System)
"""

from typing import List, Dict, Any, Optional
from datetime import timedelta
import pandas as pd

from python.data_worker.data_loader.analytics import TickDataAnalyzer
from python.framework.types.global_types import TestScenario
from python.data_worker.data_loader.core import TickDataLoader

from python.components.logger.bootstrap_logger import get_logger
vLog = get_logger()


class ScenarioGenerator:
    """
    Generates test scenarios automatically from available data.

    REFACTORED (Worker Instance System): Now generates worker_instances
    with instance names and supports multiple instances per type.
    NEW (C#003): Supports trade_simulator_config for all generation strategies.
    """

    def __init__(self, data_loader: TickDataLoader):
        """
        Args:
            data_loader: TickDataLoader instance
        """
        self.data_loader = data_loader
        self.analyzer = TickDataAnalyzer(self.data_loader)

    def _generate_instance_name(self, worker_type: str, suffix: str = "main") -> str:
        """
        Generate instance name from worker type.

        Examples:
            "CORE/rsi" + "main" → "rsi_main"
            "CORE/envelope" + "fast" → "envelope_fast"
            "USER/custom_indicator" + "main" → "custom_indicator_main"

        Args:
            worker_type: Full worker type (e.g., "CORE/rsi")
            suffix: Instance suffix (e.g., "main", "fast", "slow")

        Returns:
            Instance name in snake_case
        """
        # Extract worker name from type
        _, worker_name = worker_type.split("/", 1)
        return f"{worker_name}_{suffix}"

    def generate_from_symbol(
        self,
        symbol: str,
        strategy: str = "time_windows",
        decision_logic_type: str = "CORE/aggressive_trend",
        worker_instances: Dict[str, str] = None,
        workers_config: Dict[str, Dict[str, Any]] = None,
        decision_logic_config: Optional[Dict[str, Any]] = None,
        execution_config: Optional[Dict[str, Any]] = None,
        trade_simulator_config: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> List[TestScenario]:
        """
        Generate scenarios for a symbol using different strategies.

        REFACTORED (Worker Instance System): Uses worker_instances dict.
        NEW (C#003): Added trade_simulator_config parameter.

        Args:
            symbol: Trading symbol
            strategy: Generation strategy ("time_windows", "volatility", "sessions")
            decision_logic_type: DecisionLogic to use (e.g., "CORE/aggressive_trend")
            worker_instances: Dict[instance_name, worker_type] (e.g., {"rsi_main": "CORE/rsi"})
            workers_config: Worker parameters indexed by instance name
            decision_logic_config: DecisionLogic-specific config
            execution_config: Execution-specific config (parallelization, etc.)
            trade_simulator_config: TradeSimulator config (balance, currency, broker)
            **kwargs: Strategy-specific parameters

        Returns:
            List of generated TestScenario objects
        """
        # Default to RSI + Envelope workers if not specified
        if worker_instances is None:
            worker_instances = {
                "rsi_main": "CORE/rsi",
                "envelope_main": "CORE/envelope"
            }

        # Build strategy config with new structure
        strategy_config = self._build_strategy_config(
            decision_logic_type=decision_logic_type,
            worker_instances=worker_instances,
            workers_config=workers_config,
            decision_logic_config=decision_logic_config
        )

        # Generate scenarios based on strategy
        if strategy == "time_windows":
            return self._generate_time_windows(
                symbol,
                strategy_config=strategy_config,
                execution_config=execution_config,
                trade_simulator_config=trade_simulator_config,
                **kwargs
            )
        elif strategy == "volatility":
            return self._generate_volatility_based(
                symbol,
                strategy_config=strategy_config,
                execution_config=execution_config,
                trade_simulator_config=trade_simulator_config,
                **kwargs
            )
        elif strategy == "sessions":
            return self._generate_session_based(
                symbol,
                strategy_config=strategy_config,
                execution_config=execution_config,
                trade_simulator_config=trade_simulator_config,
                **kwargs
            )
        else:
            raise ValueError(f"Unknown strategy: {strategy}")

    def _build_strategy_config(
        self,
        decision_logic_type: str,
        worker_instances: Dict[str, str],
        workers_config: Optional[Dict[str, Dict[str, Any]]] = None,
        decision_logic_config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Build strategy configuration with worker instance system.

        This method constructs the hierarchical config that factories need.
        It sets up sensible defaults for workers based on their type.

        Args:
            decision_logic_type: e.g., "CORE/aggressive_trend"
            worker_instances: e.g., {"rsi_main": "CORE/rsi", "envelope_main": "CORE/envelope"}
            workers_config: Worker configs indexed by instance name (optional)
            decision_logic_config: DecisionLogic config (optional)

        Returns:
            Strategy config dict ready for TestScenario
        """
        # Build workers config with defaults if not provided
        if workers_config is None:
            workers_config = {}
            for instance_name, worker_type in worker_instances.items():
                # Set sensible defaults based on worker type
                worker_name = worker_type.split("/")[1].lower()

                if "rsi" in worker_name:
                    workers_config[instance_name] = {
                        "period": 14,
                        "timeframe": "M5"
                    }
                elif "envelope" in worker_name:
                    workers_config[instance_name] = {
                        "period": 20,
                        "deviation": 0.02,
                        "timeframe": "M5"
                    }
                elif "macd" in worker_name:
                    workers_config[instance_name] = {
                        "fast": 12,
                        "slow": 26,
                        "signal": 9,
                        "timeframe": "M5"
                    }
                # Add more defaults as needed

        # Build complete strategy config
        config = {
            "decision_logic_type": decision_logic_type,
            "worker_instances": worker_instances,
            "workers": workers_config
        }

        # Add decision logic config if provided
        if decision_logic_config:
            config["decision_logic_config"] = decision_logic_config

        return config

    def generate_multi_symbol(
        self,
        symbols: List[str] = None,
        scenarios_per_symbol: int = 3,
        decision_logic_type: str = "CORE/aggressive_trend",
        worker_instances: Dict[str, str] = None,
        workers_config: Dict[str, Dict[str, Any]] = None,
        decision_logic_config: Optional[Dict[str, Any]] = None,
        execution_config: Optional[Dict[str, Any]] = None,
        trade_simulator_config: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> List[TestScenario]:
        """
        Generate scenarios for multiple symbols.

        REFACTORED (Worker Instance System): Uses worker_instances dict.
        NEW (C#003): Added trade_simulator_config parameter.

        Args:
            symbols: List of symbols (None = all available)
            scenarios_per_symbol: Number of scenarios per symbol
            decision_logic_type: DecisionLogic to use
            worker_instances: Dict[instance_name, worker_type]
            workers_config: Worker configs indexed by instance name
            decision_logic_config: DecisionLogic config
            execution_config: Execution config
            trade_simulator_config: TradeSimulator config
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
                worker_instances=worker_instances,
                workers_config=workers_config,
                decision_logic_config=decision_logic_config,
                execution_config=execution_config,
                trade_simulator_config=trade_simulator_config,
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
        session: str = "london_ny_overlap",
        strategy_config: Optional[Dict[str, Any]] = None,
        execution_config: Optional[Dict[str, Any]] = None,
        trade_simulator_config: Optional[Dict[str, Any]] = None,
    ) -> List[TestScenario]:
        """
        Generate scenarios by splitting data into time windows.

        REFACTORED (Worker Instance System): Uses worker_instances dict.
        NEW (C#003): Added trade_simulator_config parameter.

        Args:
            symbol: Trading symbol
            num_windows: Number of time windows
            window_days: Days per window
            ticks_per_window: Max ticks per window
            session: Trading session - 'london', 'ny', 'london_ny_overlap', 'asian', or 'full_day'
            strategy_config: Strategy parameters (new structure!)
            execution_config: Execution parameters
            trade_simulator_config: TradeSimulator config (balance, currency, broker)
        """
        # Define trading sessions (UTC times)
        SESSIONS = {
            'asian': (0, 8),           # 00:00 - 08:00 UTC (Tokyo)
            'london': (8, 16),         # 08:00 - 16:00 UTC
            'ny': (13, 21),            # 13:00 - 21:00 UTC
            # 13:00 - 16:00 UTC (best liquidity!)
            'london_ny_overlap': (13, 16),
            'full_day': (0, 23),       # 00:00 - 23:00 UTC
        }

        start_hour, end_hour = SESSIONS.get(
            session, (8, 16))  # Default: London

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
                decision_logic_type="CORE/aggressive_trend",
                worker_instances={
                    "rsi_main": "CORE/rsi",
                    "envelope_main": "CORE/envelope"
                },
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

        for i in range(num_windows):
            window_start = start_date + (i * window_duration)
            window_end = window_start + window_duration

            if window_end > end_date:
                window_end = end_date

            # Set realistic intraday times
            window_start = window_start.replace(
                hour=start_hour, minute=0, second=0, microsecond=0)
            window_end = window_end.replace(
                hour=end_hour, minute=0, second=0, microsecond=0)

            scenario = TestScenario(
                symbol=symbol,
                start_date=window_start.isoformat(),
                end_date=window_end.isoformat(),
                max_ticks=ticks_per_window,
                data_mode="realistic",
                strategy_config=strategy_config.copy(),
                execution_config=execution_config.copy(),
                trade_simulator_config=trade_simulator_config.copy(
                ) if trade_simulator_config else None,
                enabled=True,
                name=f"{symbol}_window_{i+1:02d}"
            )
            scenarios.append(scenario)

        vLog.info(
            f"Generated {len(scenarios)} {session} session scenarios for {symbol}"
        )
        return scenarios

    def _generate_volatility_based(
        self,
        symbol: str,
        high_vol_threshold: float = 0.02,
        max_scenarios: int = 10,
        strategy_config: Optional[Dict[str, Any]] = None,
        execution_config: Optional[Dict[str, Any]] = None,
        trade_simulator_config: Optional[Dict[str, Any]] = None,
    ) -> List[TestScenario]:
        """
        Generate scenarios based on volatility periods.

        TODO: Implement volatility detection.
        For now, falls back to time_windows.

        NEW (C#003): Added trade_simulator_config parameter.
        """
        vLog.warning(
            f"Volatility-based generation not yet implemented. Using time_windows.")
        return self._generate_time_windows(
            symbol,
            num_windows=max_scenarios,
            strategy_config=strategy_config,
            execution_config=execution_config,
            trade_simulator_config=trade_simulator_config,
        )

    def _generate_session_based(
        self,
        symbol: str,
        sessions: List[str] = None,
        strategy_config: Optional[Dict[str, Any]] = None,
        execution_config: Optional[Dict[str, Any]] = None,
        trade_simulator_config: Optional[Dict[str, Any]] = None,
    ) -> List[TestScenario]:
        """
        Generate scenarios based on trading sessions.

        TODO: Implement session-based generation.
        For now, falls back to time_windows.

        NEW (C#003): Added trade_simulator_config parameter.
        """
        vLog.warning(
            f"Session-based generation not yet implemented. Using time_windows.")
        return self._generate_time_windows(
            symbol,
            num_windows=3,
            strategy_config=strategy_config,
            execution_config=execution_config,
            trade_simulator_config=trade_simulator_config,
        )
