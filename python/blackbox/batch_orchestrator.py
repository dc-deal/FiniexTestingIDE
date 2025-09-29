"""
FiniexTestingIDE - Batch Orchestrator
Universal entry point for 1-1000+ test scenarios
"""

import logging
from typing import List, Dict, Any
from concurrent.futures import ProcessPoolExecutor
import time

from python.blackbox.types import TestScenario, GlobalContract
from python.blackbox.blackbox_adapter import BlackboxAdapter
from python.blackbox.workers.rsi_worker import RSIWorker
from python.blackbox.workers.envelope_worker import EnvelopeWorker
from python.blackbox.decision_orchestrator import DecisionOrchestrator
from python.blackbox.tick_data_preparator import TickDataPreparator
from python.blackbox.bar_rendering_orchestrator import BarRenderingOrchestrator


logger = logging.getLogger(__name__)


class BatchOrchestrator:
    """
    Universal orchestrator for batch strategy testing
    Handles 1 to 1000+ scenarios with same code path
    """

    def __init__(self, scenarios: List[TestScenario], data_loader):
        """
        Initialize batch orchestrator

        Args:
            scenarios: List of test scenarios (can be 1 or 1000+)
            data_loader: TickDataLoader instance
        """
        self.scenarios = scenarios
        self.data_loader = data_loader
        self.global_contract = None

        logger.info(
            f"📦 BatchOrchestrator initialized with {len(scenarios)} scenario(s)"
        )

    def run(self, parallel: bool = False, max_workers: int = 4) -> Dict[str, Any]:
        """
        Execute all scenarios

        Args:
            parallel: Run scenarios in parallel (default: False for debugging)
            max_workers: Max parallel workers if parallel=True

        Returns:
            Aggregated results from all scenarios
        """
        logger.info(f"🚀 Starting batch execution ({len(self.scenarios)} scenarios)")
        start_time = time.time()

        # 1. Aggregate global contract from all scenarios
        self.global_contract = self._aggregate_global_contract()
        logger.info(
            f"✅ Global contract: {self.global_contract.max_warmup_bars} bars, "
            f"{len(self.global_contract.all_timeframes)} timeframes"
        )

        # 2. Execute scenarios
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
        """
        # Create sample strategy to extract worker contracts
        sample_scenario = self.scenarios[0]
        adapter = self._create_adapter(sample_scenario)

        # Get worker contracts
        contracts = []
        for worker in adapter.orchestrator.workers.values():
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
                    result = future.result(timeout=300)  # 5min timeout per scenario
                    results.append(result)
                except Exception as e:
                    logger.error(f"❌ Parallel scenario failed: {e}")
                    results.append({"error": str(e)})

        return results

    def _execute_single_scenario(self, scenario: TestScenario) -> Dict[str, Any]:
        """
        Execute single test scenario

        This is the core execution logic that works the same for
        1 scenario or 1000+ scenarios
        """

        # 1. Create strategy adapter
        adapter = self._create_adapter(scenario)

        # 2. Prepare data
        preparator = TickDataPreparator(self.data_loader)

        warmup_ticks, test_iterator = preparator.prepare_test_and_warmup_split(
            symbol=scenario.symbol,
            warmup_bars_needed=self.global_contract.max_warmup_bars,
            test_ticks_count=scenario.max_ticks or 1000,
            data_mode=scenario.data_mode,
            start_date=scenario.start_date,
            end_date=scenario.end_date,
        )

        # 3. Setup bar rendering
        bar_orchestrator = BarRenderingOrchestrator(self.data_loader)
        workers = list(adapter.orchestrator.workers.values())
        bar_orchestrator.register_workers(workers)

        # Prepare bar warmup
        import pandas as pd

        first_test_time = pd.to_datetime(warmup_ticks[-1].timestamp)
        bar_orchestrator.prepare_warmup(scenario.symbol, first_test_time)
        # FIX: Initialize bar_history with warmup bars BEFORE test loop
        initial_bar_history = {
            tf: bar_orchestrator.get_warmup_bars(tf)  # ← Diese Methode existiert
            for tf in self.global_contract.all_timeframes
        }
        # Set initial warmup in adapter
        adapter.set_bar_data({}, initial_bar_history)  # ← Warmup bars als Start
        # bar_orchestrator.set_bar_history(initial_bar_history) TOD <------------

        # 4. Execute test
        signals = []
        tick_count = 0

        for tick in test_iterator:
            # Bar rendering
            current_bars = bar_orchestrator.process_tick(tick)
            bar_history = {
                tf: bar_orchestrator.get_bar_history(scenario.symbol, tf, 100)
                for tf in self.global_contract.all_timeframes
            }

            # Set bar data in adapter
            adapter.set_bar_data(current_bars, bar_history)

            # Process decision
            decision = adapter.process_tick(tick, current_bars)
            tick_count += 1

            if decision and decision["action"] != "FLAT":
                signals.append(decision)

        # 5. Return results
        return {
            "scenario_name": scenario.name,
            "symbol": scenario.symbol,
            "ticks_processed": tick_count,
            "signals_generated": len(signals),
            "signal_rate": len(signals) / tick_count if tick_count > 0 else 0,
            "signals": signals[:10],  # First 10 for inspection
            "success": True,
        }

    def _create_adapter(self, scenario: TestScenario) -> BlackboxAdapter:
        """
        Create strategy adapter based on scenario config

        TODO: Make this configurable via scenario.strategy_config
        """
        config = scenario.strategy_config

        # Create workers
        rsi_worker = RSIWorker(
            period=config.get("rsi_period", 14),
            timeframe=config.get("rsi_timeframe", "M5"),
        )

        envelope_worker = EnvelopeWorker(
            period=config.get("envelope_period", 20),
            deviation=config.get("envelope_deviation", 0.02),
            timeframe=config.get("envelope_timeframe", "M5"),
        )

        # Create orchestrator
        orchestrator = DecisionOrchestrator([rsi_worker, envelope_worker])

        # Create adapter
        adapter = BlackboxAdapter(orchestrator)
        adapter.initialize()

        return adapter
