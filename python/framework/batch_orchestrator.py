"""
FiniexTestingIDE - Batch Orchestrator (Refactored)
Universal entry point for 1-1000+ test scenarios
"""

import logging
from typing import List, Dict, Any
from concurrent.futures import ProcessPoolExecutor
import time
from datetime import datetime, timezone

from python.framework.types import TestScenario, GlobalContract, TickData
from python.framework.workers.preset_workers.heavy_workers import (
    HeavyEnvelopeWorker,
    HeavyMACDWorker,
    HeavyRSIWorker,
)
from python.framework.workers.preset_workers.rsi_worker import RSIWorker
from python.framework.workers.preset_workers.envelope_worker import EnvelopeWorker
from python.framework.workers.worker_coordinator import WorkerCoordinator
from python.framework.tick_data_preparator import TickDataPreparator
from python.framework.bars.bar_rendering_controller import BarRenderingController


logger = logging.getLogger(__name__)


class BatchOrchestrator:
    """
    Universal orchestrator for batch strategy testing
    Handles 1 to 1000+ scenarios with same code path
    """

    def __init__(self, scenarios: List[TestScenario], data_worker):
        """
        Initialize batch orchestrator

        Args:
            scenarios: List of test scenarios (can be 1 or 1000+)
            data_worker: TickDataLoader instance
        """
        self.scenarios = scenarios
        self.data_worker = data_worker
        self.global_contract = None
        self._last_orchestrator = None

        logger.info(
            f"📦 BatchOrchestrator initialized with {len(scenarios)} scenario(s)"
        )

    def run(self, parallel: bool = False, max_workers: int = 4) -> Dict[str, Any]:
        """
        Execute all scenarios

        Args:
            parallel: Run scenarios in parallel (default: False for debugging)
            max_workers: Max parallel workers if parallel=True (can be overridden by execution_config)

        Returns:
            Aggregated results from all scenarios
        """
        logger.info(
            f"🚀 Starting batch execution ({len(self.scenarios)} scenarios)")
        start_time = time.time()

        # 1. Aggregate global contract
        self.global_contract = self._aggregate_global_contract()

        # 2. Check if execution_config overrides max_workers
        if self.scenarios and self.scenarios[0].execution_config:
            config_max_scenarios = self.scenarios[0].execution_config.get(
                "max_parallel_scenarios"
            )
            if config_max_scenarios is not None:
                max_workers = config_max_scenarios
                logger.info(
                    f"📝 Using max_parallel_scenarios from config: {max_workers}"
                )

        # 3. Execute scenarios
        if parallel and len(self.scenarios) > 1:
            results = self._run_parallel(max_workers)
        else:
            results = self._run_sequential()

        # 3. Aggregate results
        execution_time = time.time() - start_time

        summary = {
            "success": True,
            "scenarios_count": len(self.scenarios),
            "execution_time": execution_time,
            "results": results,
            "global_contract": {
                "max_warmup_bars": self.global_contract.max_warmup_bars,
                "timeframes": self.global_contract.all_timeframes,
                "total_workers": self.global_contract.total_workers,
            },
        }

        logger.info(f"✅ Batch execution completed in {execution_time:.2f}s")
        return summary

    def _aggregate_global_contract(self) -> GlobalContract:
        """
        Aggregate requirements from all scenarios/workers
        Creates unified warmup and timeframe requirements

        REFACTORED: Creates orchestrator directly instead of adapter
        """
        # Create sample strategy to extract worker contracts
        sample_scenario = self.scenarios[0]
        orchestrator = self._create_orchestrator(sample_scenario)

        # Get worker contracts directly from orchestrator
        contracts = []
        for worker in orchestrator.workers.values():
            if hasattr(worker, "get_contract"):
                contracts.append(worker.get_contract())

        # Aggregate all Contracts
        max_warmup = max(
            [max(c.warmup_requirements.values()) for c in contracts], default=50
        )
        all_timeframes = list(
            set(tf for c in contracts for tf in c.required_timeframes)
        )

        warmup_by_tf = {}
        for contract in contracts:
            for tf, bars in contract.warmup_requirements.items():
                warmup_by_tf[tf] = max(warmup_by_tf.get(tf, 0), bars)

        all_params = {}
        for contract in contracts:
            all_params.update(contract.parameters)

        return GlobalContract(
            max_warmup_bars=max_warmup,
            all_timeframes=all_timeframes,
            warmup_by_timeframe=warmup_by_tf,
            total_workers=len(contracts),
            all_parameters=all_params,
        )

    def _run_sequential(self) -> List[Dict[str, Any]]:
        """Execute scenarios sequentially (easier debugging)"""
        results = []

        for i, scenario in enumerate(self.scenarios, 1):
            logger.info(
                f"📊 Running scenario {i}/{len(self.scenarios)}: {scenario.name}"
            )

            try:
                result = self._execute_single_scenario(scenario)
                results.append(result)
                logger.info(
                    f"✅ Scenario {i} completed: {result.get('signals_generated', 0)} signals"
                )
            except Exception as e:
                logger.error(f"❌ Scenario {i} failed: {e}", exc_info=True)
                results.append({"error": str(e), "scenario": scenario.name})

        return results

    def _run_parallel(self, max_workers: int) -> List[Dict[str, Any]]:
        """Execute scenarios in parallel"""
        logger.info(
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
                    logger.error(f"❌ Parallel scenario failed: {e}")
                    results.append({"error": str(e)})

        return results

    def _execute_single_scenario(self, scenario: TestScenario) -> Dict[str, Any]:
        """
        Execute single test scenario

        REFACTORED: Works directly with WorkerCoordinator, no adapter
        """

        # 1. Create WorkerCoordinator directly (no adapter!)
        orchestrator = self._create_orchestrator(scenario)
        orchestrator.initialize()

        self._last_orchestrator = orchestrator

        logger.info(
            f"✅ Orchestrator initialized with {len(orchestrator.workers)} workers"
        )

        # 2. Prepare data
        preparator = TickDataPreparator(self.data_worker)

        warmup_ticks, test_iterator = preparator.prepare_test_and_warmup_split(
            symbol=scenario.symbol,
            warmup_bars_needed=self.global_contract.max_warmup_bars,
            test_ticks_count=scenario.max_ticks or 1000,
            data_mode=scenario.data_mode,
            start_date=scenario.start_date,
            end_date=scenario.end_date,
        )

        # 3. Setup bar rendering
        bar_orchestrator = BarRenderingController(self.data_worker)

        # Get workers directly from orchestrator (no adapter.orchestrator!)
        workers = list(orchestrator.workers.values())
        bar_orchestrator.register_workers(workers)

        # Prepare bar warmup from pre-loaded ticks
        import pandas as pd

        first_test_time = pd.to_datetime(warmup_ticks[-1].timestamp)
        bar_orchestrator.prepare_warmup_from_ticks(
            symbol=scenario.symbol,
            warmup_ticks=warmup_ticks,
            test_start_time=first_test_time,
        )

        # 4. Execute test loop
        signals = []
        tick_count = 0

        # Statistics tracking (previously in adapter)
        ticks_processed = 0
        signals_generated = 0

        for tick in test_iterator:
            # Bar rendering
            current_bars = bar_orchestrator.process_tick(tick)
            bar_history = {
                tf: bar_orchestrator.get_bar_history(scenario.symbol, tf, 100)
                for tf in self.global_contract.all_timeframes
            }

            # Process decision DIRECTLY through orchestrator (no adapter!)
            decision = orchestrator.process_tick(
                tick=tick, current_bars=current_bars, bar_history=bar_history
            )

            ticks_processed += 1
            tick_count += 1

            if decision and decision["action"] != "FLAT":
                signals.append(decision)
                signals_generated += 1

        # 5. Return results
        worker_stats = (
            orchestrator.get_statistics()
            if hasattr(orchestrator, "get_statistics")
            else {}
        )

        return {
            "scenario_name": scenario.name,
            "symbol": scenario.symbol,
            "ticks_processed": tick_count,
            "signals_generated": len(signals),
            "signal_rate": len(signals) / tick_count if tick_count > 0 else 0,
            "signals": signals[:10],  # First 10 for inspection
            "success": True,
            "worker_statistics": worker_stats,
        }

    def _create_orchestrator(self, scenario: TestScenario) -> WorkerCoordinator:
        """
        Create WorkerCoordinator with workers based on scenario config
        NOW: Reads execution config from execution_config (not strategy_config!)
        """
        # Strategy-Config → Workers
        strategy_config = scenario.strategy_config

        # Execution-Config → Framework Optimization
        exec_config = scenario.execution_config

        parallel_workers = exec_config.get("parallel_workers")
        parallel_threshold = exec_config.get(
            "worker_parallel_threshold_ms", 1.0)
        log_stats = exec_config.get("log_performance_stats", False)

        # Create workers (strategy-specific)
        rsi_worker = RSIWorker(
            period=strategy_config.get("rsi_period", 14),
            timeframe=strategy_config.get("rsi_timeframe", "M5"),
        )

        envelope_worker = EnvelopeWorker(
            period=strategy_config.get("envelope_period", 20),
            deviation=strategy_config.get("envelope_deviation", 0.02),
            timeframe=strategy_config.get("envelope_timeframe", "M5"),
        )

        # Create orchestrator with config-based settings
        orchestrator = WorkerCoordinator(
            workers=[rsi_worker, envelope_worker],
            parallel_workers=parallel_workers,  # ← FROM EXECUTION CONFIG!
            parallel_threshold_ms=parallel_threshold,  # ← FROM EXECUTION CONFIG!
        )

        # Store config for later reference
        orchestrator._execution_config = exec_config

        return orchestrator

    # def _create_orchestrator(self, scenario: TestScenario) -> WorkerCoordinator:
    #     config = scenario.strategy_config

    #     exec_config = config.get("execution", {})
    #     parallel_workers = exec_config.get("parallel_workers", False)
    #     parallel_threshold = exec_config.get("worker_parallel_threshold_ms", 1.0)
    #     log_stats = exec_config.get("log_performance_stats", False)

    #     # Künstliche Last aus Config
    #     load_ms = config.get("artificial_load_ms", 5.0)

    #     # Heavy Workers statt normale Workers
    #     rsi_worker = HeavyRSIWorker(
    #         period=config.get("rsi_period", 14),
    #         timeframe=config.get("rsi_timeframe", "M5"),
    #         artificial_load_ms=load_ms,  # ← LAST!
    #     )

    #     envelope_worker = HeavyEnvelopeWorker(
    #         period=config.get("envelope_period", 20),
    #         deviation=config.get("envelope_deviation", 0.02),
    #         timeframe=config.get("envelope_timeframe", "M5"),
    #         artificial_load_ms=load_ms * 1.5,  # ← MEHR LAST!
    #     )

    #     macd_worker = HeavyMACDWorker(
    #         fast=12,
    #         slow=26,
    #         signal=9,
    #         timeframe="M5",
    #         artificial_load_ms=load_ms * 1.2,  # ← MITTLERE LAST
    #     )

    #     # WorkerCoordinator mit allen 3 Workers
    #     orchestrator = WorkerCoordinator(
    #         [rsi_worker, envelope_worker, macd_worker],
    #          parallel_workers=parallel_workers,  # ← FROM CONFIG!
    #          parallel_threshold_ms=parallel_threshold,  # ← FROM CONFIG!
    #     )

    #     orchestrator._execution_config = exec_config

    #     return orchestrator
