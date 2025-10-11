"""
FiniexTestingIDE - Batch Orchestrator (REFACTORED)
Universal entry point for 1-1000+ test scenarios

ARCHITECTURE CHANGE (Issue 2):
- Uses Worker Factory to create workers from config
- Uses DecisionLogic Factory to create strategy from config
- No more hardcoded workers or decision logic!
- Complete config-driven architecture
- Per-scenario requirements (no global contract)

This is the final integration point where all pieces come together:
Config → Factories → Workers + DecisionLogic → WorkerCoordinator → Results

ARCHITECTURE CHANGE (Parameter Inheritance Bug Fix):
- Removed global contract - each scenario calculates its own requirements
- This allows scenarios to have completely different worker configurations
- Scenario 1 can use M1 with period 10, while Scenario 2 uses M5 with period 14
- No more cross-contamination of requirements between scenarios

REFACTORED (Trade Simulation):
- Creates TradeSimulator from broker config (per scenario)
- Creates DecisionTradingAPI with order-type validation
- Injects DecisionTradingAPI into DecisionLogic after validation
- Decision Logic executes orders via DecisionTradingAPI
- Updates prices on each tick for realistic spread calculation
- Collects trading statistics (portfolio, execution, costs)
"""

import re
import threading
import pandas as pd
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime, timezone
import traceback
from typing import Any, Dict, List

from python.components.logger.bootstrap_logger import setup_logging
from python.data_worker.data_loader.core import TickDataLoader
from python.framework.bars.bar_rendering_controller import \
    BarRenderingController
from python.framework.tick_data_preparator import TickDataPreparator
from python.framework.types import TestScenario, TickData, TimeframeConfig
from python.framework.workers.worker_coordinator import WorkerCoordinator
from python.configuration import AppConfigLoader
from python.framework.trading_env.order_types import OrderStatus, OrderType, OrderDirection

# ============================================
# NEW (Issue 2): Factory Imports
# ============================================
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.factory.decision_logic_factory import DecisionLogicFactory

# ============================================
# REFACTORED: Trade Simulation Imports
# ============================================
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.trade_simulator import TradeSimulator
from python.framework.trading_env.decision_trading_api import DecisionTradingAPI
from python.framework.reporting.scenario_set_performance_manager import ScenarioSetPerformanceManager, ScenarioPerformanceStats

vLog = setup_logging(name="StrategyRunner")


class BatchOrchestrator:
    """
    Universal orchestrator for batch strategy testing.
    Handles 1 to 1000+ scenarios with same code path.

    Now fully config-driven thanks to Worker and DecisionLogic factories.
    Each scenario is completely independent with its own requirements.
    """

    def __init__(
        self,
        scenarios: List[TestScenario],
        data_worker: TickDataLoader,
        app_config: AppConfigLoader,
        performance_log: ScenarioSetPerformanceManager,
    ):
        """
        Initialize batch orchestrator.

        Args:
            scenarios: List of test scenarios
            data_worker: TickDataLoader instance
            app_config: Application configuration
            performance_log: Statistics collection container
        """
        self.scenarios = scenarios
        self.data_worker = data_worker
        self._last_orchestrator = None
        self.appConfig = app_config
        self.performance_log = performance_log

        # Initialize Factories
        self.worker_factory = WorkerFactory()
        self.decision_logic_factory = DecisionLogicFactory()

        vLog.debug(
            f"📦 BatchOrchestrator initialized with {len(scenarios)} scenario(s)")

    def run(self) -> Dict[str, Any]:
        """
        Execute all scenarios.

        Args:
            parallel: Run scenarios in parallel (default: False for debugging)
            max_workers: Max parallel workers if parallel=True (can be overridden by execution_config)

        Returns:
            Aggregated results from all scenarios
        """
        vLog.info(
            f"🚀 Starting batch execution ({len(self.scenarios)} scenarios)")
        vLog.section_separator()
        start_time = time.time()

        # Get batch mode from app_config.json
        run_parallel = self.appConfig.get_default_parallel_scenarios()

        # Execute scenarios
        if run_parallel and len(self.scenarios) > 1:
            results = self._run_parallel()
        else:
            results = self._run_sequential()

        # Aggregate results
        execution_time = time.time() - start_time

        # ============================================
        # NEU: Set metadata in ScenarioSetPerformanceManager
        # ============================================
        self.performance_log.set_metadata(
            execution_time=execution_time,
            success=True
        )

        summary = {
            "success": True,
            "scenarios_count": len(self.scenarios),
            "execution_time": execution_time,
            "scenario_results": results,
        }

        vLog.debug(f"✅ Batch execution completed in {execution_time:.2f}s")
        return summary

    def _run_sequential(self) -> List[Dict[str, Any]]:
        """Execute scenarios sequentially (easier debugging)"""
        results = []

        for scenario_index, scenario in enumerate(self.scenarios):

            readable_index = scenario_index+1
            vLog.info(
                f"📊 Running scenario {readable_index}/{len(self.scenarios)}: {scenario.name}"
            )
            try:
                result = self._execute_single_scenario(
                    scenario, scenario_index)
                results.append(result)
                vLog.info(
                    f"✅ Scenario {readable_index} completed"
                )
                vLog.section_separator()
            except Exception as e:
                vLog.error(
                    f"❌ Scenario {readable_index} failed: \n{traceback.format_exc()}")
                results.append({"error": str(e), "scenario": scenario.name})

        return results

    def _run_parallel(self) -> List[Dict[str, Any]]:
        """Execute scenarios in parallel using threads (not processes)."""
        max_parallel_scenarios = self.appConfig.get_default_max_parallel_scenarios()

        vLog.info(
            f"🔀 Running {len(self.scenarios)} scenarios in parallel "
            f"(max {max_parallel_scenarios} workers)"
        )

        # ThreadPoolExecutor instead of ProcessPoolExecutor
        # Reason: Shared state (TradeSimulator, ScenarioSetPerformanceManager) with threading.Lock
        with ThreadPoolExecutor(max_workers=max_parallel_scenarios) as executor:
            # Submit with scenario_index to maintain order
            futures = [
                executor.submit(self._execute_single_scenario, scenario, idx)
                for idx, scenario in enumerate(self.scenarios)
            ]

            results = []
            for future in futures:
                try:
                    result = future.result(timeout=300)
                    results.append(result)
                except Exception as e:
                    vLog.error(f"❌ Parallel scenario failed: {e}")
                    results.append({"error": str(e), "success": False})

        return results

    def _execute_single_scenario(
        self,
        scenario: TestScenario,
        scenario_index: int
    ) -> Dict[str, Any]:
        """
        Execute single test scenario.

        REFACTORED:
        - Creates scenario-specific TradeSimulator (thread-safe)
        - Creates DecisionTradingAPI with order-type validation
        - Injects API into DecisionLogic after validation
        - Decision Logic executes orders via API
        - Writes stats to ScenarioSetPerformanceManager including portfolio data
        """

        # Set thread name for debugging
        # ============================================
        current_thread = threading.current_thread()
        original_thread_name = current_thread.name

        # Sanitize scenario name (max 13 chars to leave room for prefix)
        safe_scenario_name = self._sanitize_thread_name(
            scenario.name, max_length=13)
        current_thread.name = f"Scen_{scenario_index}_{safe_scenario_name}"

        vLog.debug(
            f"🧵 Thread renamed: {original_thread_name} → {current_thread.name}")

        # 1. Create isolated TradeSimulator for THIS scenario
        scenario_simulator = self._create_trade_simulator_for_scenario(
            scenario)

        # 2. Create Workers using Worker Factory
        strategy_config = scenario.strategy_config

        try:
            workers_dict = self.worker_factory.create_workers_from_config(
                strategy_config)
            workers = list(workers_dict.values())
            vLog.debug(f"✓ Created {len(workers)} workers from config")
        except Exception as e:
            vLog.error(f"Failed to create workers: {e}")
            raise ValueError(f"Worker creation failed: {e}")

        # 3. Create DecisionLogic (WITHOUT trading API yet)
        # REFACTORED: No trading_env parameter, API injected after validation
        try:
            decision_logic = self.decision_logic_factory.create_logic_from_strategy_config(
                strategy_config
            )
            vLog.debug(f"✓ Created decision logic: {decision_logic.name}")
        except Exception as e:
            vLog.error(f"Failed to create decision logic: {e}")
            raise ValueError(f"Decision logic creation failed: {e}")

        # 4. Create and validate DecisionTradingAPI
        # This validates order types BEFORE scenario starts!
        try:
            required_order_types = decision_logic.get_required_order_types()
            trading_api = DecisionTradingAPI(
                trade_simulator=scenario_simulator,
                required_order_types=required_order_types
            )
            vLog.debug(
                f"✓ DecisionTradingAPI validated for order types: "
                f"{[t.value for t in required_order_types]}"
            )
        except ValueError as e:
            vLog.error(f"Order type validation failed: {e}")
            raise ValueError(
                f"Broker does not support required order types: {e}")

        # 5. Inject DecisionTradingAPI into Decision Logic
        decision_logic.set_trading_api(trading_api)
        vLog.debug("✓ DecisionTradingAPI injected into Decision Logic")

        # 6. Calculate per-scenario requirements
        scenario_contract = self._calculate_scenario_requirements(workers)

        # 4. Extract execution config
        exec_config = scenario.execution_config or {}
        parallel_workers = exec_config.get("parallel_workers")
        parallel_threshold = exec_config.get(
            "worker_parallel_threshold_ms", 1.0)

        # 5. Create WorkerCoordinator with injected dependencies
        orchestrator = WorkerCoordinator(
            workers=workers,
            decision_logic=decision_logic,
            parallel_workers=parallel_workers,
            parallel_threshold_ms=parallel_threshold,
            scenario_name=scenario.name,
        )
        orchestrator.initialize()

        self._last_orchestrator = orchestrator

        vLog.debug(
            f"✅ Orchestrator initialized: {len(workers)} workers + {decision_logic.name}"
        )

        # 6. Calculate per-scenario requirements
        scenario_contract = self._calculate_scenario_requirements(workers)

        # 7. Prepare data using timestamp-based warmup
        preparator = TickDataPreparator(self.data_worker)

        # Parse test period timestamps
        test_start = datetime.fromisoformat(scenario.start_date)
        test_end = datetime.fromisoformat(scenario.end_date)

        vLog.debug(
            f"📊 Scenario warmup bar requirements: {scenario_contract['warmup_by_timeframe']}"
        )

        # Preparator converts bars to minutes internally
        warmup_ticks, test_iterator = preparator.prepare_test_and_warmup_split(
            symbol=scenario.symbol,
            warmup_bar_requirements=scenario_contract["warmup_by_timeframe"],
            test_start=test_start,
            test_end=test_end,
            max_test_ticks=scenario.max_ticks,
            data_mode=scenario.data_mode,
            scenario_name=scenario.name
        )

        # 8. Setup bar rendering
        bar_orchestrator = BarRenderingController(self.data_worker)
        bar_orchestrator.register_workers(workers)

        first_test_time = pd.to_datetime(warmup_ticks[-1].timestamp)
        bar_orchestrator.prepare_warmup_from_ticks(
            symbol=scenario.symbol,
            warmup_ticks=warmup_ticks,
            test_start_time=first_test_time,
        )

        # 9. Execute test loop
        signals = []
        tick_count = 0
        ticks_processed = 0
        signals_generated = 0
        signals_gen_buy = 0
        signals_gen_sell = 0

        vLog.info(f"🚀 Starting Tick Loop")

        for tick in test_iterator:
            # Update scenario-specific TradeSimulator with current tick prices
            scenario_simulator.update_prices(tick)

            # Bar rendering
            current_bars = bar_orchestrator.process_tick(tick)
            bar_history = {
                tf: bar_orchestrator.get_bar_history(scenario.symbol, tf, 100)
                # Use scenario's timeframes!
                for tf in scenario_contract["all_timeframes"]
            }

            # Process decision through orchestrator
            decision = orchestrator.process_tick(
                tick=tick, current_bars=current_bars, bar_history=bar_history
            )

            ticks_processed += 1
            tick_count += 1

            # REFACTORED: Decision Logic executes orders via DecisionTradingAPI
            # Statistics are updated automatically inside execute_decision()
            order_result = None
            if decision and decision.action != "FLAT":
                try:
                    order_result = decision_logic.execute_decision(
                        decision, tick)

                    # Track successful orders as signals
                    if order_result and order_result.status == OrderStatus.PENDING:
                        signals.append({
                            **decision.to_dict(),
                            'order_id': order_result.order_id,
                            'executed_price': order_result.executed_price,
                            'lot_size': order_result.executed_lots
                        })
                        signals_generated += 1
                        if decision.action == "BUY":
                            signals_gen_buy += 1
                        if decision.action == "SELL":
                            signals_gen_sell += 1

                except Exception as e:
                    vLog.error(
                        f"Order execution failed: \n{traceback.format_exc()}")

        # BEFORE collecting statistics - cleanup pending orders.
        open_positions = scenario_simulator.get_open_positions()
        if open_positions:
            vLog.warning(
                f"⚠️ {len(open_positions)} positions remain open - auto-closing")
            for pos in open_positions:
                scenario_simulator.close_position(pos.position_id)

        # ============================================
        # Collect trading statistics
        # ============================================
        worker_stats = orchestrator.get_statistics()
        # 10. Collect portfolio stats from scenario-specific TradeSimulator
        portfolio_stats = scenario_simulator.get_portfolio_stats()
        execution_stats = scenario_simulator.get_execution_stats()
        cost_breakdown = scenario_simulator.get_cost_breakdown()

        # ============================================
        # Build ScenarioPerformanceStats object
        # ============================================
        stats = ScenarioPerformanceStats(
            scenario_index=scenario_index,
            scenario_name=scenario.name,
            symbol=scenario.symbol,
            ticks_processed=tick_count,
            signals_generated=len(signals),
            signals_gen_buy=signals_gen_buy,
            signals_gen_sell=signals_gen_sell,
            signal_rate=len(signals) / tick_count if tick_count > 0 else 0,
            success=True,
            worker_statistics=worker_stats,
            decision_logic_name=decision_logic.name,
            scenario_contract=scenario_contract,
            sample_signals=signals[:10],
            portfolio_stats=portfolio_stats,
            execution_stats=execution_stats,
            cost_breakdown=cost_breakdown
        )

        # Write to ScenarioSetPerformanceManager (thread-safe)
        self.performance_log.add_scenario_stats(scenario_index, stats)

        # Return minimal dict
        return {
            "success": True,
            "scenario_name": scenario.name
        }

    def _calculate_scenario_requirements(
        self, workers: List
    ) -> Dict[str, Any]:
        """
        Calculate requirements for THIS scenario based on its workers.

        NEW (Parameter Inheritance Fix): This replaces the global contract approach.
        Each scenario now calculates its own requirements independently, allowing
        different scenarios to use completely different worker configurations.

        Args:
            workers: List of worker instances for THIS scenario

        Returns:
            Dict with max_warmup_bars, all_timeframes, warmup_by_timeframe
        """
        # Get contracts from all workers
        contracts = [worker.get_contract() for worker in workers]

        # Calculate maximum warmup bars needed for this scenario
        max_warmup = max(
            [max(c.warmup_requirements.values())
             for c in contracts if c.warmup_requirements],
            default=50
        )

        # Collect all timeframes needed for this scenario
        all_timeframes = list(
            set(tf for c in contracts for tf in c.required_timeframes)
        )

        # Calculate warmup requirements per timeframe for this scenario
        warmup_by_tf = {}
        for contract in contracts:
            for tf, bars in contract.warmup_requirements.items():
                warmup_by_tf[tf] = max(warmup_by_tf.get(tf, 0), bars)

        return {
            "max_warmup_bars": max_warmup,
            "all_timeframes": all_timeframes,
            "warmup_by_timeframe": warmup_by_tf,
            "total_workers": len(workers),
        }

    def get_last_orchestrator(self):
        """Get last created orchestrator for debugging"""
        return self._last_orchestrator

    # Creacte TradeSimulator instance for a scenario
    def _create_trade_simulator_for_scenario(
        self,
        scenario: TestScenario
    ) -> TradeSimulator:
        """
        Create isolated TradeSimulator for this scenario.

        Each scenario gets its own TradeSimulator instance for:
        - Thread-safety in parallel execution
        - Independent balance/equity tracking
        - Clean statistics per scenario

        Merges global + scenario-specific trade_simulator_config.

        Args:
            scenario: TestScenario with optional trade_simulator_config

        Returns:
            TradeSimulator instance for this scenario
        """
        # Get global trade_simulator_config from scenario_set (if exists)
        # NOTE: This requires scenario_set to be passed to BatchOrchestrator
        # For now, use defaults (can be extended later)

        # Get scenario-specific config (can override global)
        ts_config = scenario.trade_simulator_config or {}

        # Defaults
        broker_config_path = ts_config.get("broker_config_path")
        if broker_config_path is None:
            raise ValueError(
                "No broker_config_path specified in strategy_config. "
                "Example: 'global.trade_simulator_config.broker_config_path': "
                "'./configs/brokers/mt5/ic_markets_demo.json'"
            )

        initial_balance = ts_config.get("initial_balance", 10000.0)
        currency = ts_config.get("currency", "EUR")

        # Create broker config
        broker_config = BrokerConfig.from_json(broker_config_path)

        # Create NEW TradeSimulator for this scenario
        return TradeSimulator(
            broker_config=broker_config,
            initial_balance=initial_balance,
            currency=currency
        )

    def _sanitize_thread_name(self, name: str, max_length: int = 20) -> str:
        """
        Sanitize name for thread naming.
        - Converts to snake_case
        - Removes special characters
        - Truncates to max_length

        Examples:
            "CORE/rsi" → "core_rsi"
            "eurusd_2024-06-01_window1" → "eurusd_2024_06_01_w"
            "My Strategy!" → "my_strategy"

        Args:
            name: Original name to sanitize
            max_length: Maximum length (default: 20)

        Returns:
            Sanitized thread-safe name
        """

        # Convert to lowercase
        name = name.lower()

        # Replace separators with underscore
        name = name.replace("/", "_")
        name = name.replace("-", "_")
        name = name.replace(" ", "_")

        # Remove special characters (keep only alphanumeric + underscore)
        name = re.sub(r'[^a-z0-9_]', '', name)

        # Remove consecutive underscores
        name = re.sub(r'_+', '_', name)

        # Truncate to max_length
        if len(name) > max_length:
            name = name[:max_length]

        # Remove trailing underscore if present after truncation
        name = name.rstrip('_')

        return name
