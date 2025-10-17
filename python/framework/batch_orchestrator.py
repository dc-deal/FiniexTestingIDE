"""
FiniexTestingIDE - Batch Orchestrator (REFACTORED)
Universal entry point for 1-1000+ test scenarios

EXTENDED (Phase 1a):
- Live progress display integration
- Buffered logging for clean output
- Live scenario tracking with real-time updates
"""

from collections import defaultdict
import re
import threading
import pandas as pd
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import traceback
from typing import Any, Dict, List

from python.components.logger.bootstrap_logger import get_logger
from python.data_worker.data_loader.core import TickDataLoader
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.tick_data_preparator import TickDataPreparator
from python.framework.types import TestScenario, TickData, TimeframeConfig
from python.framework.workers.worker_coordinator import WorkerCoordinator
from python.configuration import AppConfigLoader
from python.framework.trading_env.order_types import OrderStatus, OrderType, OrderDirection

# Factory Imports
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.factory.decision_logic_factory import DecisionLogicFactory

# Trade Simulation Imports
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.trade_simulator import TradeSimulator
from python.framework.trading_env.decision_trading_api import DecisionTradingAPI
from python.framework.reporting.scenario_set_performance_manager import (
    ScenarioSetPerformanceManager,
    ScenarioPerformanceStats
)

from python.framework.exceptions.data_validation_errors import (
    DataValidationError,
    InsufficientTickDataError,
    CriticalGapError,
    NoDataAvailableError,
    InvalidDateRangeError
)

# NEW (Phase 1a): Live Progress Display
from python.components.display.live_progress_display import LiveProgressDisplay

vLog = get_logger()


class BatchOrchestrator:
    """
    Universal orchestrator for batch strategy testing.
    Handles 1 to 1000+ scenarios with same code path.

    Now fully config-driven thanks to Worker and DecisionLogic factories.
    Each scenario is completely independent with its own requirements.

    EXTENDED (Phase 1a):
    - Live progress display during execution
    - Buffered logging for clean output
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

        # Set metadata in ScenarioSetPerformanceManager
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
        """
        Execute scenarios sequentially (easier debugging).

        EXTENDED (Phase 1a):
        - Enable buffered logging
        - Start live progress display
        - Flush logs after completion
        """
        # ===== Phase 1a: Setup Live Display =====
        vLog.enable_buffering()
        live_display = LiveProgressDisplay(
            self.performance_log,
            self.scenarios
        )
        live_display.start()

        # ===== Execute Scenarios =====
        results = []

        for scenario_index, scenario in enumerate(self.scenarios):
            readable_index = scenario_index + 1

            try:
                result = self._execute_single_scenario(
                    scenario, scenario_index)
                results.append(result)

            except Exception as e:
                vLog.error(
                    f"❌ Scenario {readable_index} failed: \n{traceback.format_exc()}")
                results.append({"error": str(e), "scenario": scenario.name})

        # ===== Phase 1a: Cleanup =====
        live_display.stop()
        vLog.flush_buffer()

        return results

    def _run_parallel(self) -> List[Dict[str, Any]]:
        """
        Execute scenarios in parallel using threads (not processes).

        EXTENDED (Phase 1a):
        - Enable buffered logging
        - Start live progress display
        - Flush logs after completion
        """
        # ===== Phase 1a: Setup Live Display =====
        vLog.enable_buffering()
        live_display = LiveProgressDisplay(
            self.performance_log,
            self.scenarios
        )
        live_display.start()

        # ===== Execute Scenarios in Parallel =====
        max_workers = self.appConfig.get_default_max_parallel_scenarios()

        results = [None] * len(self.scenarios)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_index = {
                executor.submit(
                    self._execute_single_scenario,
                    scenario,
                    idx
                ): idx
                for idx, scenario in enumerate(self.scenarios)
            }

            for future in future_to_index:
                idx = future_to_index[future]
                readable_index = idx + 1

                try:
                    result = future.result()
                    results[idx] = result

                except Exception as e:
                    vLog.error(
                        f"❌ Scenario {readable_index} failed: \n{traceback.format_exc()}")
                    results[idx] = {
                        "error": str(e),
                        "scenario": self.scenarios[idx].name
                    }

        # ===== Phase 1a: Cleanup =====
        live_display.stop()
        vLog.flush_buffer()

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

        # Setup Log for this scenario
        vLog.start_scenario_logging(scenario_index, scenario.name)

        # Set thread name for debugging
        current_thread = threading.current_thread()
        original_thread_name = current_thread.name

        # Sanitize scenario name (max 13 chars to leave room for prefix)
        safe_scenario_name = self._sanitize_thread_name(
            scenario.name, max_length=13)
        current_thread.name = f"Scen_{scenario_index}_{safe_scenario_name}"

        vLog.debug(
            f"🧵 Thread renamed: {original_thread_name} → {current_thread.name}")

        # 1. Create isolated TradeSimulator for THIS scenario
        scenario_trade_simulator = self._create_trade_simulator_for_scenario(
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
        try:
            decision_logic = self.decision_logic_factory.create_logic_from_strategy_config(
                strategy_config
            )
            vLog.debug(f"✓ Created decision logic: {decision_logic.name}")
        except Exception as e:
            vLog.error(f"Failed to create decision logic: {e}")
            raise ValueError(f"Decision logic creation failed: {e}")

        # 4. Create and validate DecisionTradingAPI
        try:
            required_order_types = decision_logic.get_required_order_types()
            trading_api = DecisionTradingAPI(
                trade_simulator=scenario_trade_simulator,
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
        scenario_requirement = self._calculate_scenario_requirements(workers)

        # 7. Extract execution config
        exec_config = scenario.execution_config or {}
        parallel_workers = exec_config.get("parallel_workers")
        parallel_threshold = exec_config.get(
            "worker_parallel_threshold_ms", 1.0)

        # 8. Create WorkerCoordinator with injected dependencies
        orchestrator = WorkerCoordinator(
            workers=workers,
            decision_logic=decision_logic,
            strategy_config=strategy_config,
            parallel_workers=parallel_workers,
            parallel_threshold_ms=parallel_threshold,
            scenario_name=scenario.name
        )
        orchestrator.initialize()

        self._last_orchestrator = orchestrator

        vLog.debug(
            f"✅ Orchestrator initialized: {len(workers)} workers + {decision_logic.name}"
        )

        # 9. Calculate per-scenario requirements
        scenario_requirement = self._calculate_scenario_requirements(workers)
        # 10. Prepare data using timestamp-based warmup
        preparator = TickDataPreparator(self.data_worker)

        # Parse test period timestamps
        test_start = datetime.fromisoformat(scenario.start_date)
        test_end = datetime.fromisoformat(scenario.end_date)

        vLog.debug(
            f"📊 Scenario warmup bar requirements: {scenario_requirement['warmup_by_timeframe']}"
        )

        # Preparator converts bars to minutes internally
        # read tick count of data, because you can't rely on max_ticks (gaps, timespan)
        try:
            test_iterator, total_test_ticks = preparator.prepare_test_and_warmup_split(
                symbol=scenario.symbol,
                warmup_bar_requirements=scenario_requirement["warmup_by_timeframe"],
                test_start=test_start,
                test_end=test_end,
                max_test_ticks=scenario.max_ticks,
                data_mode=scenario.data_mode,
                scenario_name=scenario.name
            )
        except DataValidationError as e:
            # Catch all data validation errors and format nicely
            vLog.validation_error(
                message=str(e),
                context=e.get_context()
            )

        # ===== LIVE STATS: Update total ticks after ticks are known. =====
        # as early as possible.
        self.performance_log.start_scenario_tracking(
            scenario_index=scenario_index,
            scenario_name=scenario.name,
            total_ticks=total_test_ticks,
            initial_balance=scenario_trade_simulator.portfolio.initial_balance,
            symbol=scenario.symbol
        )

        # 10. Setup bar rendering
        bar_orchestrator = BarRenderingController(self.data_worker)
        bar_orchestrator.register_workers(workers)
        bar_orchestrator.prepare_warmup_from_parquet_bars(
            symbol=scenario.symbol,
            test_start_time=test_start
        )

        # Print Bar Quality Metrics - Synthetic bar impact on data
        self._print_warmup_quality_metrics(bar_orchestrator)

        # Last startup live log after warmup phase.
        self.performance_log.set_live_status(
            scenario_index=scenario_index, status="running")

        # 11. Execute test loop
        signals = []
        tick_count = 0
        ticks_processed = 0
        signals_generated = 0
        signals_gen_buy = 0
        signals_gen_sell = 0

        vLog.info(f"🚀 Starting Tick Loop")

        # Insert BEFORE the tick loop (line ~398):

        # Profiling counters
        profile_times = defaultdict(float)
        profile_counts = defaultdict(int)
        tick_count = 0

        # Replace the existing tick loop with this:
        for tick in test_iterator:
            tick_start = time.perf_counter()

            # === 1. Trade Simulator Update ===
            t1 = time.perf_counter()
            scenario_trade_simulator.update_prices(tick)
            t2 = time.perf_counter()
            profile_times['trade_simulator'] += (t2 - t1) * 1000
            profile_counts['trade_simulator'] += 1

            # === 2. Bar Rendering ===
            t3 = time.perf_counter()
            current_bars = bar_orchestrator.process_tick(tick)
            t4 = time.perf_counter()
            profile_times['bar_rendering'] += (t4 - t3) * 1000
            profile_counts['bar_rendering'] += 1

            # === 3. Bar History Retrieval ===
            t5 = time.perf_counter()
            bar_history = bar_orchestrator.get_all_bar_history(scenario.symbol)
            t6 = time.perf_counter()
            profile_times['bar_history'] += (t6 - t5) * 1000
            profile_counts['bar_history'] += 1

            # === 4. Worker Processing + Decision ===
            t7 = time.perf_counter()
            decision = orchestrator.process_tick(
                tick=tick, current_bars=current_bars, bar_history=bar_history
            )
            t8 = time.perf_counter()
            profile_times['worker_decision'] += (t8 - t7) * 1000
            profile_counts['worker_decision'] += 1

            # === 5. Order Execution (if any) ===
            order_result = None
            if decision and decision.action != "FLAT":
                t9 = time.perf_counter()
                try:
                    order_result = decision_logic.execute_decision(
                        decision, tick)

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

                        self.performance_log.update_live_stats(
                            scenario_index=scenario_index,
                            ticks_processed=tick_count,
                            portfolio_stats=scenario_trade_simulator.get_portfolio_stats(),
                            account_info=scenario_trade_simulator.get_account_info()
                        )
                except Exception as e:
                    vLog.error(
                        f"Order execution failed: \n{traceback.format_exc()}")

                t10 = time.perf_counter()
                profile_times['order_execution'] += (t10 - t9) * 1000
                profile_counts['order_execution'] += 1

            # === 6. Periodic Stats Update ===
            if tick_count % 100 == 0:
                t11 = time.perf_counter()
                self.performance_log.update_live_stats(
                    scenario_index=scenario_index,
                    ticks_processed=tick_count,
                    portfolio_stats=scenario_trade_simulator.get_portfolio_stats(),
                    account_info=scenario_trade_simulator.get_account_info()
                )
                t12 = time.perf_counter()
                profile_times['stats_update'] += (t12 - t11) * 1000
                profile_counts['stats_update'] += 1

            # Total tick time
            tick_end = time.perf_counter()
            profile_times['total_per_tick'] += (tick_end - tick_start) * 1000

            ticks_processed += 1
            tick_count += 1

        # === PROFILING REPORT ===
        vLog.info("\n" + "=" * 80)
        vLog.info("🔬 PROFILING REPORT (100 TICKS)")
        vLog.info("=" * 80)

        total_time = profile_times['total_per_tick']

        for operation in ['trade_simulator', 'bar_rendering', 'bar_history',
                          'worker_decision', 'order_execution', 'stats_update']:
            if operation in profile_times and profile_times[operation] > 0:
                op_time = profile_times[operation]
                op_count = profile_counts[operation]
                avg_time = op_time / op_count if op_count > 0 else 0
                percentage = (op_time / total_time *
                              100) if total_time > 0 else 0

                vLog.info(
                    f"{operation:20s}: {op_time:8.2f}ms total  |  "
                    f"{avg_time:6.3f}ms avg  |  "
                    f"{op_count:4d} calls  |  "
                    f"{percentage:5.1f}%"
                )

        vLog.info("-" * 80)
        vLog.info(
            f"{'TOTAL':20s}: {total_time:8.2f}ms  |  {total_time/100:6.3f}ms per tick")
        vLog.info("=" * 80 + "\n")

        # BEFORE collecting statistics - cleanup pending orders
        open_positions = scenario_trade_simulator.get_open_positions()
        if open_positions:
            vLog.warning(
                f"⚠️ {len(open_positions)} positions remain open - auto-closing")
            for pos in open_positions:
                scenario_trade_simulator.close_position(pos.position_id)

        # ============================================
        # Collect statistics
        # ============================================
        worker_stats = orchestrator.get_statistics()
        portfolio_stats = scenario_trade_simulator.get_portfolio_stats()
        execution_stats = scenario_trade_simulator.get_execution_stats()
        cost_breakdown = scenario_trade_simulator.get_cost_breakdown()

        # ===== LIVE STATS: Final update with completed stats =====
        self.performance_log.update_live_stats(
            scenario_index=scenario_index,
            ticks_processed=tick_count,
            portfolio_stats=scenario_trade_simulator.get_portfolio_stats(),
            account_info=scenario_trade_simulator.get_account_info()
        )
        elapsed = vLog.get_scenario_elapsed_time(scenario_index)

        # ============================================
        # Build ScenarioPerformanceStats object
        # ============================================
        stats = ScenarioPerformanceStats(
            scenario_index=scenario_index,
            scenario_name=scenario.name,
            portfolio_value=scenario_trade_simulator.portfolio.balance,
            initial_balance=scenario_trade_simulator.portfolio.initial_balance,
            symbol=scenario.symbol,
            ticks_processed=tick_count,
            elapsed_time=elapsed,
            signals_generated=len(signals),
            signals_gen_buy=signals_gen_buy,
            signals_gen_sell=signals_gen_sell,
            signal_rate=len(signals) / tick_count if tick_count > 0 else 0,
            success=True,
            worker_statistics=worker_stats,
            decision_logic_name=decision_logic.name,
            scenario_requirement=scenario_requirement,
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

        Each scenario calculates its own requirements independently, allowing
        different scenarios to use completely different worker configurations.

        Args:
            workers: List of worker instances for THIS scenario

        Returns:
            Dict with max_warmup_bars, all_timeframes, warmup_by_timeframe
        """
        # Collect warmup requirements and timeframes directly from workers
        all_warmup_reqs = []
        all_timeframes = set()
        warmup_by_tf = {}

        for worker in workers:
            # Get warmup requirements from worker instance
            warmup_reqs = worker.get_warmup_requirements()
            all_warmup_reqs.append(warmup_reqs)

            # Get required timeframes from worker instance
            timeframes = worker.get_required_timeframes()
            all_timeframes.update(timeframes)

            # Track max warmup per timeframe
            for tf, bars in warmup_reqs.items():
                warmup_by_tf[tf] = max(warmup_by_tf.get(tf, 0), bars)

        # Calculate maximum warmup bars needed for this scenario
        max_warmup = max(
            [max(reqs.values()) for reqs in all_warmup_reqs if reqs],
            default=50
        )

        return {
            "max_warmup_bars": max_warmup,
            "all_timeframes": list(all_timeframes),
            "warmup_by_timeframe": warmup_by_tf,
            "total_workers": len(workers),
        }

    def get_last_orchestrator(self):
        """Get last created orchestrator for debugging"""
        return self._last_orchestrator

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

    def _print_warmup_quality_metrics(self, bar_orchestrator: BarRenderingController):
        # === NEW: Warmup Quality Check ===
        warmup_quality = bar_orchestrator.get_warmup_quality_metrics()

        has_quality_issues = False
        for timeframe, metrics in warmup_quality.items():
            synthetic = metrics['synthetic']
            hybrid = metrics['hybrid']
            total = metrics['total']

            if synthetic > 0 or hybrid > 0:
                has_quality_issues = True

                # Build warning message
                issues = []
                if synthetic > 0:
                    issues.append(
                        f"{synthetic} synthetic ({metrics['synthetic_pct']:.1f}%)")
                if hybrid > 0:
                    issues.append(
                        f"{hybrid} hybrid ({metrics['hybrid_pct']:.1f}%)")

                vLog.warning(
                    f"⚠️  {timeframe} warmup quality: {', '.join(issues)} of {total} bars"
                )

        if has_quality_issues:
            vLog.warning(
                f"⚠️  Warmup contains synthetic/hybrid bars - indicator warmup may be unrealistic!"
            )
            vLog.warning(
                f"   This typically happens when warmup period spans weekends/holidays."
            )
