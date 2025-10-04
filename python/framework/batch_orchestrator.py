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
"""

import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List

from python.components.logger.bootstrap_logger import setup_logging
from python.framework.bars.bar_rendering_controller import \
    BarRenderingController
from python.framework.tick_data_preparator import TickDataPreparator
from python.framework.types import TestScenario, TickData
from python.framework.workers.worker_coordinator import WorkerCoordinator

# ============================================
# NEW (Issue 2): Factory Imports
# ============================================
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.factory.decision_logic_factory import DecisionLogicFactory

vLog = setup_logging(name="StrategyRunner")


class BatchOrchestrator:
    """
    Universal orchestrator for batch strategy testing.
    Handles 1 to 1000+ scenarios with same code path.

    Now fully config-driven thanks to Worker and DecisionLogic factories.
    Each scenario is completely independent with its own requirements.
    """

    def __init__(self, scenarios: List[TestScenario], data_worker):
        """
        Initialize batch orchestrator.

        Args:
            scenarios: List of test scenarios (can be 1 or 1000+)
            data_worker: TickDataLoader instance
        """
        self.scenarios = scenarios
        self.data_worker = data_worker
        # REMOVED: self.global_contract - no longer needed with per-scenario requirements
        self._last_orchestrator = None

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

    def run(self, parallel: bool = False, max_workers: int = 4) -> Dict[str, Any]:
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

        # REMOVED: Global contract aggregation
        # Each scenario now calculates its own requirements in _execute_single_scenario()

        # Check if execution_config overrides max_workers
        if self.scenarios and self.scenarios[0].execution_config:
            config_max_scenarios = self.scenarios[0].execution_config.get(
                "max_parallel_scenarios"
            )
            if config_max_scenarios is not None:
                max_workers = config_max_scenarios
                vLog.info(
                    f"📝 Using max_parallel_scenarios from config: {max_workers}"
                )

        # Execute scenarios
        if parallel and len(self.scenarios) > 1:
            results = self._run_parallel(max_workers)
        else:
            results = self._run_sequential()

        # Aggregate results
        execution_time = time.time() - start_time

        summary = {
            "success": True,
            "scenarios_count": len(self.scenarios),
            "execution_time": execution_time,
            "results": results,
            # REMOVED: global_contract - each scenario has its own requirements now
        }

        vLog.info(f"✅ Batch execution completed in {execution_time:.2f}s")
        return summary

    def _run_sequential(self) -> List[Dict[str, Any]]:
        """Execute scenarios sequentially (easier debugging)"""
        results = []

        for i, scenario in enumerate(self.scenarios, 1):
            vLog.section_separator()
            vLog.info(
                f"📊 Running scenario {i}/{len(self.scenarios)}: {scenario.name}"
            )

            try:
                result = self._execute_single_scenario(scenario)
                results.append(result)
                vLog.info(
                    f"✅ Scenario {i} completed: {result.get('signals_generated', 0)} signals"
                )
            except Exception as e:
                vLog.error(f"❌ Scenario {i} failed: {e}", exc_info=True)
                results.append({"error": str(e), "scenario": scenario.name})

        return results

    def _run_parallel(self, max_workers: int) -> List[Dict[str, Any]]:
        """Execute scenarios in parallel"""
        vLog.info(
            f"🔀 Running {len(self.scenarios)} scenarios in parallel (max {max_workers} workers)"
        )

        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(self._execute_single_scenario, scenario)
                for scenario in self.scenarios
            ]

            results = []
            for future in futures:
                try:
                    # 5min timeout per scenario
                    result = future.result(timeout=300)
                    results.append(result)
                except Exception as e:
                    vLog.error(f"❌ Parallel scenario failed: {e}")
                    results.append({"error": str(e)})

        return results

    def _execute_single_scenario(self, scenario: TestScenario) -> Dict[str, Any]:
        """
        Execute single test scenario.

        REFACTORED (Issue 2): Now uses both factories to create components.
        REFACTORED (Parameter Inheritance Fix): Each scenario calculates its own requirements.
        """
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

        # 2. Create DecisionLogic using DecisionLogic Factory
        try:
            decision_logic = self.decision_logic_factory.create_logic_from_strategy_config(
                strategy_config
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

            if decision and decision.action != "FLAT":
                signals.append(decision.to_dict())  # Convert Decision to dict
                signals_generated += 1

        # 9. Return results (enhanced with scenario-specific contract info)
        worker_stats = orchestrator.get_statistics()

        return {
            "scenario_set_name": scenario.name,
            "symbol": scenario.symbol,
            "ticks_processed": tick_count,
            "signals_generated": len(signals),
            "signal_rate": len(signals) / tick_count if tick_count > 0 else 0,
            "signals": signals[:10],  # First 10 for inspection
            "success": True,
            "worker_statistics": worker_stats,
            "decision_logic": decision_logic.name,  # Track which logic was used
            # NEW: Include scenario's own requirements
            "scenario_contract": scenario_contract,
        }

    def _calculate_scenario_requirements(self, workers: List) -> Dict[str, Any]:
        """
        Calculate requirements for a single scenario based on its workers.

        NEW (Parameter Inheritance Fix): This replaces the global contract approach.
        Each scenario calculates its own requirements independently, preventing
        cross-contamination between scenarios with different worker configurations.

        For example:
        - Scenario 1 with M1/period=10 will have M1 requirements
        - Scenario 2 with M5/period=14 will have M5 requirements
        - They don't interfere with each other

        Args:
            workers: List of worker instances for this specific scenario

        Returns:
            Dict with max_warmup_bars, all_timeframes, warmup_by_timeframe, total_workers
        """
        contracts = []
        for worker in workers:
            if hasattr(worker, "get_contract"):
                contracts.append(worker.get_contract())

        # Calculate maximum warmup bars needed for this scenario
        max_warmup = max(
            [max(c.warmup_requirements.values()) for c in contracts],
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
