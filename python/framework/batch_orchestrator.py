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

EXTENDED (C#003 - Trade Simulation):
- Creates TradeSimulator from broker config
- Injects TradeSimulator into DecisionLogic via factory
- Updates prices on each tick for realistic spread calculation
- Collects trading statistics (portfolio, execution, costs)
"""

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
from python.framework.types import TestScenario, TickData
from python.framework.workers.worker_coordinator import WorkerCoordinator
from python.configuration import AppConfigLoader
from python.framework.trading_env.order_types import OrderType, OrderDirection

# ============================================
# NEW (Issue 2): Factory Imports
# ============================================
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.factory.decision_logic_factory import DecisionLogicFactory

# ============================================
# NEW (C#003): Trade Simulation Imports
# ============================================
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.trade_simulator import TradeSimulator
from python.components.logger.scenario_performance_stats import PerformanceSummaryLog

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
        trade_simulator: TradeSimulator,           # NEW (C#003)
        performance_log: PerformanceSummaryLog     # NEW (C#003)
    ):
        """
        Initialize batch orchestrator.

        Args:
            scenarios: List of test scenarios
            data_worker: TickDataLoader instance
            app_config: Application configuration
            trade_simulator: Shared TradeSimulator instance (NEW C#003)
            performance_log: Statistics collection container (NEW C#003)
        """
        self.scenarios = scenarios
        self.data_worker = data_worker
        # REMOVED: self.global_contract - no longer needed with per-scenario requirements
        self._last_orchestrator = None
        self.appConfig = app_config
        self.trade_simulator = trade_simulator      # NEW (C#003)
        self.performance_log = performance_log      # NEW (C#003)

        # ============================================
        # NEW (Issue 2): Initialize Factories
        # ============================================
        self.worker_factory = WorkerFactory()
        self.decision_logic_factory = DecisionLogicFactory()

        vLog.debug(
            f"📦 BatchOrchestrator initialized with {len(scenarios)} scenario(s)"
        )
        vLog.debug(
            f"Available workers: {self.worker_factory.get_registered_workers()}"
        )
        vLog.debug(
            f"Available decision logics: {self.decision_logic_factory.get_registered_logics()}"
        )

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
        start_time = time.time()

        # Each scenario now calculates its own requirements in _execute_single_scenario()
        # FIXED (V0.7): Get batch mode from app_config.json!+
        # parallel
        run_parallel = self.appConfig.get_default_parallel_scenarios()

        # Execute scenarios
        if run_parallel and len(self.scenarios) > 1:
            results = self._run_parallel()
        else:
            results = self._run_sequential()

        # Aggregate results
        execution_time = time.time() - start_time

        summary = {
            "success": True,
            "scenarios_count": len(self.scenarios),
            "execution_time": execution_time,
            "scenario_results": results,
        }

        vLog.info(f"✅ Batch execution completed in {execution_time:.2f}s")
        return summary

    def _run_sequential(self) -> List[Dict[str, Any]]:
        """Execute scenarios sequentially (easier debugging)"""
        results = []

        for scenario_index, scenario in enumerate(self.scenarios):
            vLog.section_separator()
            vLog.info(
                f"📊 Running scenario {scenario_index}/{len(self.scenarios)}: {scenario.name}"
            )

            try:
                result = self._execute_single_scenario(
                    scenario, scenario_index)
                results.append(result)
                vLog.info(
                    f"✅ Scenario {scenario_index} completed"
                )
            except Exception as e:
                vLog.error(
                    f"❌ Scenario {scenario_index} failed: {e}", exc_info=True)
                results.append({"error": str(e), "scenario": scenario.name})

        return results

    def _run_parallel(self) -> List[Dict[str, Any]]:
        """Execute scenarios in parallel using threads (not processes)."""
        max_parallel_scenarios = self.appConfig.get_default_max_parallel_scenarios()

        vLog.info(
            f"🔀 Running {len(self.scenarios)} scenarios in parallel "
            f"(max {max_parallel_scenarios} workers)"
        )

        # CHANGED (C#003): ThreadPoolExecutor instead of ProcessPoolExecutor
        # Reason: Shared state (TradeSimulator, PerformanceSummaryLog) with threading.Lock
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
        scenario_index: int  # NEW: Critical for maintaining order!
    ) -> Dict[str, Any]:
        """
        Execute single test scenario.

        REFACTORED (C#003):
        - Uses shared TradeSimulator (with reset)
        - Writes stats to PerformanceSummaryLog
        - Returns minimal dict

        Args:
            scenario: TestScenario to execute
            scenario_index: Original position in scenario array

        Returns:
            Minimal result dict
        """
        # Reset TradeSimulator for this scenario
        self.trade_simulator.reset()
        # ============================================
        # NEW (Issue 2): Factory-driven component creation
        # ============================================

        # 1. Create Workers using Worker Factory
        strategy_config = scenario.strategy_config

        try:
            workers_dict = self.worker_factory.create_workers_from_config(
                strategy_config)
            workers = list(workers_dict.values())
            vLog.info(f"✓ Created {len(workers)} workers from config")
        except Exception as e:
            vLog.error(f"Failed to create workers: {e}")
            raise ValueError(f"Worker creation failed: {e}")

        # 2. Create DecisionLogic (with TradeSimulator)
        # NEW (C#003): Pass trade_simulator to factory for injection
        try:
            decision_logic = self.decision_logic_factory.create_logic_from_strategy_config(
                strategy_config,
                trading_env=self.trade_simulator  # NEW (C#003)
            )
            vLog.info(f"✓ Created decision logic: {decision_logic.name}")
        except Exception as e:
            vLog.error(f"Failed to create decision logic: {e}")
            raise ValueError(f"Decision logic creation failed: {e}")

        # ============================================
        # NEW (Parameter Inheritance Fix): Calculate per-scenario requirements
        # ============================================
        # 3. Calculate THIS scenario's specific requirements
        # No longer uses a global contract - each scenario is independent
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
            scenario_name=scenario.name,  # NEW: Pass scenario name for performance logging
        )
        orchestrator.initialize()

        self._last_orchestrator = orchestrator

        vLog.info(
            f"✅ Orchestrator initialized: {len(workers)} workers + {decision_logic.name}"
        )

        # 6. Prepare data using THIS scenario's requirements
        preparator = TickDataPreparator(self.data_worker)

        warmup_ticks, test_iterator = preparator.prepare_test_and_warmup_split(
            symbol=scenario.symbol,
            # Use scenario's own requirements!
            warmup_bars_needed=scenario_contract["max_warmup_bars"],
            test_ticks_count=scenario.max_ticks or 1000,
            data_mode=scenario.data_mode,
            start_date=scenario.start_date,
            end_date=scenario.end_date,
        )

        # 7. Setup bar rendering
        bar_orchestrator = BarRenderingController(self.data_worker)
        bar_orchestrator.register_workers(workers)

        import pandas as pd
        first_test_time = pd.to_datetime(warmup_ticks[-1].timestamp)
        bar_orchestrator.prepare_warmup_from_ticks(
            symbol=scenario.symbol,
            warmup_ticks=warmup_ticks,
            test_start_time=first_test_time,
        )

        # 8. Execute test loop
        signals = []
        tick_count = 0
        ticks_processed = 0
        signals_generated = 0

        for tick in test_iterator:
            # ============================================
            # NEW (C#003): Update TradeSimulator with current tick prices
            # This enables realistic spread calculation from LIVE data
            # ============================================
            self.trade_simulator.update_prices(
                symbol=tick.symbol,
                bid=tick.bid,
                ask=tick.ask
            )

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

            # NEW: Execute orders based on decision
            if decision and decision.action != "FLAT":
                # Convert decision to order
                direction = OrderDirection.BUY if decision.action == "BUY" else OrderDirection.SELL

                # TODO: Position sizing logic (currently hardcoded)
                lot_size = 0.1

                # Send order
                order_result = self.trade_simulator.send_order(
                    symbol=tick.symbol,
                    order_type=OrderType.MARKET,
                    direction=direction,
                    lots=lot_size,
                    stop_loss=None,  # TODO: Get from decision or risk management
                    take_profit=None,
                    comment=f"Signal: {decision.reason}"
                )

                # Track only successful orders as signals
                if order_result.is_success:
                    signals.append({
                        **decision.to_dict(),
                        'order_id': order_result.order_id,
                        'executed_price': order_result.executed_price,
                        'lot_size': lot_size
                    })
                    signals_generated += 1

        # ============================================
        # NEW (C#003): Collect trading statistics from TradeSimulator
        # ============================================
        # 10. Return results (enhanced with scenario-specific contract info)
        worker_stats = orchestrator.get_statistics()

        # Write to PerformanceSummaryLog
        self.performance_log.add_scenario_stats(
            scenario_index=scenario_index,  # Critical for order!
            scenario_name=scenario.name,
            symbol=scenario.symbol,
            ticks_processed=tick_count,
            signals_generated=len(signals),
            signal_rate=len(signals) / tick_count if tick_count > 0 else 0,
            worker_statistics=worker_stats,
            decision_logic_name=decision_logic.name,
            scenario_contract=scenario_contract,
            sample_signals=signals[:10],
            success=True
        )

        # Return minimal dict
        return {
            "success": True,
            "scenario_name": scenario.name
        }

    def _calculate_scenario_requirements(self, workers: List) -> Dict[str, Any]:
        """
        Calculate requirements for a single scenario based on its workers.

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
