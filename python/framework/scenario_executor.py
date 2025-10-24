"""
FiniexTestingIDE - Scenario Executor
Executes a single test scenario with two-phase execution
"""

from collections import defaultdict
import threading
import time
import traceback
from datetime import datetime
from typing import Optional

from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.exceptions.data_validation_errors import DataValidationError
from python.framework.exceptions.scenario_execution_errors import (
    ScenarioExecutionError,
    ScenarioPreparationError,
    ScenarioStateError
)
from python.framework.reporting.warmup_quality_reporter import print_warmup_quality_metrics
from python.framework.tick_data_preparator import TickDataPreparator
from python.framework.trading_env.decision_trading_api import DecisionTradingAPI
from python.framework.types.live_stats_types import ScenarioStatus
from python.framework.types.order_types import OrderStatus
from python.framework.types.scenario_set_types import SingleScenario
from python.framework.types.batch_executor_types import (
    ScenarioExecutorDependencies,
    ScenarioExecutionResult
)
from python.framework.types.scenario_set_performance_types import ProfilingData, ScenarioPerformanceStats
from python.framework.utils.scenario_requirements import calculate_scenario_requirements
from python.framework.utils.thread_utils import sanitize_thread_name
from python.framework.utils.trade_simulator_creator import create_trade_simulator_for_scenario
from python.framework.workers.worker_coordinator import WorkerCoordinator


class ScenarioExecutor:
    """
    Executes a single test scenario in two phases.

    Phase 1: Warmup & Preparation (prepare_scenario)
    - Load data
    - Create workers & decision logic
    - Setup trade simulator
    - Prepare bar rendering

    Phase 2: Tick Loop Execution (execute_tick_loop)
    - Process all ticks
    - Generate signals
    - Update statistics

    Two-phase design enables:
    - Independent preparation in parallel mode
    - Synchronized tick loop start via barrier
    - Clean separation of concerns

    Thread-safe: Each scenario gets isolated TradeSimulator and workers.
    """

    def __init__(self, dependencies: ScenarioExecutorDependencies):
        """
        Initialize scenario executor with dependencies.

        Args:
            dependencies: ScenarioExecutorDependencies container
        """
        self.deps = dependencies

        # State - set during prepare_scenario()
        self.scenario: Optional[SingleScenario] = None
        self.scenario_index: Optional[int] = None
        self.orchestrator: Optional[WorkerCoordinator] = None
        self.trade_simulator = None
        self.decision_logic = None
        self.test_iterator = None
        self.bar_rendering_controller: Optional[BarRenderingController] = None
        self.total_test_ticks: int = 0
        self.scenario_requirements = None

        # Original thread name for restoration
        self.original_thread_name: Optional[str] = None

    def prepare_scenario(
        self,
        scenario: SingleScenario,
        scenario_index: int
    ) -> None:
        """
        Phase 1: Prepare scenario for execution.

        Performs all setup and warmup operations:
        1. Initialize logging and tracking
        2. Create trade simulator
        3. Create workers and decision logic
        4. Validate and inject trading API
        5. Load and prepare tick data
        6. Setup bar rendering
        7. Check warmup quality

        This phase runs independently for each scenario in parallel mode.
        All scenarios must complete preparation before tick loop starts.

        Args:
            scenario: SingleScenario to prepare
            scenario_index: Index in scenario list

        Raises:
            ScenarioPreparationError: If preparation fails
            DataValidationError: If data validation fails
        """
        try:
            # Store scenario info
            self.scenario = scenario
            self.scenario_index = scenario_index

            # start global Log
            self.scenario.logger.reset_start_time("Preperation")

            # Initialize live tracking
            self.deps.performance_log.start_scenario_tracking(
                scenario_index=scenario_index,
                scenario_name=scenario.name,
                symbol=scenario.symbol
            )

            # Set thread name for debugging
            current_thread = threading.current_thread()
            self.original_thread_name = current_thread.name
            safe_scenario_name = sanitize_thread_name(
                scenario.name, max_length=13)
            current_thread.name = f"Scen_{scenario_index}_{safe_scenario_name}"

            scenario.logger.debug(
                f"🧵 Thread renamed: {self.original_thread_name} → {current_thread.name}"
            )

            # 1. Create isolated TradeSimulator for THIS scenario
            self.trade_simulator = create_trade_simulator_for_scenario(
                scenario)
            self.deps.performance_log.set_trade_simulator(
                scenario_index, self.trade_simulator
            )

            # 2. Create Workers using Worker Factory
            strategy_config = scenario.strategy_config

            try:
                workers_dict = self.deps.worker_factory.create_workers_from_config(
                    strategy_config,
                    logger=scenario.logger  # Logger aus Scenario durchreichen!
                )
                workers = list(workers_dict.values())
                scenario.logger.debug(
                    f"✓ Created {len(workers)} workers from config")
            except Exception as e:
                scenario.logger.error(f"Failed to create workers: {e}")
                raise ValueError(f"Worker creation failed: {e}")

            # 3. Create DecisionLogic (WITHOUT trading API yet)
            try:
                self.decision_logic = self.deps.decision_logic_factory.create_logic_from_strategy_config(
                    strategy_config=strategy_config,
                    logger=scenario.logger  # Logger aus Scenario durchreichen!
                )
                scenario.logger.debug(
                    f"✓ Created decision logic: {self.decision_logic.name}")
            except Exception as e:
                scenario.logger.error(f"Failed to create decision logic: {e}")
                raise ValueError(f"Decision logic creation failed: {e}")

            # 4. Create and validate DecisionTradingAPI
            try:
                required_order_types = self.decision_logic.get_required_order_types()
                trading_api = DecisionTradingAPI(
                    trade_simulator=self.trade_simulator,
                    required_order_types=required_order_types
                )
                scenario.logger.debug(
                    f"✓ DecisionTradingAPI validated for order types: "
                    f"{[t.value for t in required_order_types]}"
                )
            except ValueError as e:
                scenario.logger.error(f"Order type validation failed: {e}")
                raise ValueError(
                    f"Broker does not support required order types: {e}"
                )

            # 5. Inject DecisionTradingAPI into Decision Logic
            self.decision_logic.set_trading_api(trading_api)
            scenario.logger.debug(
                "✓ DecisionTradingAPI injected into Decision Logic")

            # 6. Calculate per-scenario requirements
            self.scenario_requirements = calculate_scenario_requirements(
                workers)

            # 7. Extract execution config
            exec_config = scenario.execution_config or {}
            parallel_workers = exec_config.get("parallel_workers")
            parallel_threshold = exec_config.get(
                "worker_parallel_threshold_ms", 1.0
            )

            # 8. Create WorkerCoordinator with injected dependencies
            self.orchestrator = WorkerCoordinator(
                workers=workers,
                decision_logic=self.decision_logic,
                strategy_config=strategy_config,
                parallel_workers=parallel_workers,
                parallel_threshold_ms=parallel_threshold,
                scenario_name=scenario.name
            )
            self.orchestrator.initialize()

            scenario.logger.debug(
                f"✅ Orchestrator initialized: {len(workers)} workers + {self.decision_logic.name}"
            )

            # 9. Prepare data using timestamp-based warmup
            preparator = TickDataPreparator(
                self.deps.data_worker, scenario.logger)

            # Parse test period timestamps
            test_start = datetime.fromisoformat(scenario.start_date)
            test_end = datetime.fromisoformat(scenario.end_date)

            scenario.logger.debug(
                f"📊 Scenario warmup bar requirements: "
                f"{self.scenario_requirements.warmup_by_timeframe}"
            )
            self.deps.performance_log.set_live_status(
                scenario_index=scenario_index, status=ScenarioStatus.WARMUP
            )

            # Preparator converts bars to minutes internally
            try:
                self.test_iterator, self.total_test_ticks = preparator.prepare_test_and_warmup_split(
                    symbol=scenario.symbol,
                    warmup_bar_requirements=self.scenario_requirements.warmup_by_timeframe,
                    test_start=test_start,
                    test_end=test_end,
                    max_test_ticks=scenario.max_ticks,
                    data_mode=scenario.data_mode,
                    scenario_name=scenario.name
                )
            except DataValidationError as e:
                # Catch all data validation errors and format nicely
                scenario.logger.validation_error(
                    message=str(e),
                    context=e.get_context()
                )
                raise

            self.deps.performance_log.set_total_ticks(
                scenario_index, self.total_test_ticks
            )

            # 10. Setup bar rendering
            self.bar_rendering_controller = BarRenderingController(
                self.deps.data_worker, scenario.logger)
            self.bar_rendering_controller.register_workers(workers)
            self.bar_rendering_controller.prepare_warmup_from_parquet_bars(
                symbol=scenario.symbol,
                test_start_time=test_start
            )

            # Print Bar Quality Metrics - Synthetic bar impact on data
            print_warmup_quality_metrics(self.bar_rendering_controller)

            # Mark warmup complete
            self.deps.performance_log.set_live_status(
                scenario_index=scenario_index,
                status=ScenarioStatus.WARMUP_COMPLETE
            )

            scenario.logger.debug(
                f"✅ Scenario {scenario_index} preparation complete - ready for tick loop"
            )

        except Exception as e:
            # Wrap any error in ScenarioPreparationError
            error_msg = f"Scenario {scenario_index} preparation failed: {str(e)}"
            scenario.logger.error(f"❌ {error_msg}\n{traceback.format_exc()}")
            raise ScenarioPreparationError(error_msg) from e

    def execute_tick_loop(self) -> ScenarioExecutionResult:
        """
        Phase 2: Execute tick loop.

        Processes all ticks in the test period:
        1. Update trade simulator prices
        2. Render bars
        3. Process workers and generate decisions
        4. Execute orders
        5. Update statistics

        This phase starts synchronized after all scenarios complete preparation.

        Returns:
            ScenarioExecutionResult with execution status

        Raises:
            ScenarioStateError: If prepare_scenario() was not called first
            ScenarioExecutionError: If execution fails
        """
        # Guard: Ensure prepare_scenario was called
        if self.scenario is None or self.test_iterator is None:
            raise ScenarioStateError(
                "execute_tick_loop() called without prepare_scenario(). "
                "Must call prepare_scenario() first."
            )

        try:
            start_time = time.time()

            signals = []
            tick_count = 0
            signals_generated = 0
            signals_gen_buy = 0
            signals_gen_sell = 0

            self.scenario.logger.info(f"🚀 Starting Tick Loop")

            # Update status to running
            self.deps.performance_log.set_live_status(
                scenario_index=self.scenario_index,
                status=ScenarioStatus.RUNNING
            )

            # Profiling counters
            profile_times = defaultdict(float)
            profile_counts = defaultdict(int)

            for tick in self.test_iterator:
                tick_start = time.perf_counter()

                # === 1. Trade Simulator Update ===
                t1 = time.perf_counter()
                self.trade_simulator.update_prices(tick)
                t2 = time.perf_counter()
                profile_times['trade_simulator'] += (t2 - t1) * 1000
                profile_counts['trade_simulator'] += 1

                # === 2. Bar Rendering ===
                t3 = time.perf_counter()
                current_bars = self.bar_rendering_controller.process_tick(tick)
                t4 = time.perf_counter()
                profile_times['bar_rendering'] += (t4 - t3) * 1000
                profile_counts['bar_rendering'] += 1

                # === 3. Bar History Retrieval ===
                t5 = time.perf_counter()
                bar_history = self.bar_rendering_controller.get_all_bar_history(
                    self.scenario.symbol
                )
                t6 = time.perf_counter()
                profile_times['bar_history'] += (t6 - t5) * 1000
                profile_counts['bar_history'] += 1

                # === 4. Worker Processing + Decision ===
                t7 = time.perf_counter()
                decision = self.orchestrator.process_tick(
                    tick=tick,
                    current_bars=current_bars,
                    bar_history=bar_history
                )
                t8 = time.perf_counter()
                profile_times['worker_decision'] += (t8 - t7) * 1000
                profile_counts['worker_decision'] += 1

                # === 5. Order Execution (if any) ===
                if decision and decision.action != "FLAT":
                    t9 = time.perf_counter()
                    try:
                        order_result = self.decision_logic.execute_decision(
                            decision, tick
                        )

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

                            self.deps.performance_log.update_live_stats(
                                scenario_index=self.scenario_index,
                                ticks_processed=tick_count
                            )
                    except Exception as e:
                        self.scenario.logger.error(
                            f"Order execution failed: \n{traceback.format_exc()}"
                        )

                    t10 = time.perf_counter()
                    profile_times['order_execution'] += (t10 - t9) * 1000
                    profile_counts['order_execution'] += 1

                # === 6. Periodic Stats Update ===
                if tick_count % 500 == 0:
                    t11 = time.perf_counter()
                    self.deps.performance_log.update_live_stats(
                        scenario_index=self.scenario_index,
                        ticks_processed=tick_count
                    )
                    t12 = time.perf_counter()
                    profile_times['stats_update'] += (t12 - t11) * 1000
                    profile_counts['stats_update'] += 1

                # Total tick time
                tick_end = time.perf_counter()
                profile_times['total_per_tick'] += (
                    tick_end - tick_start) * 1000

                tick_count += 1

            # Build typed ProfilingData from raw dicts (after loop completes)
            profiling_data = ProfilingData.from_dicts(
                dict(profile_times),
                dict(profile_counts)
            )

            # BEFORE collecting statistics - cleanup pending orders
            open_positions = self.trade_simulator.get_open_positions()
            if open_positions:
                self.scenario.logger.warning(
                    f"⚠️ {len(open_positions)} positions remain open - auto-closing"
                )
                for pos in open_positions:
                    self.trade_simulator.close_position(pos.position_id)

            # ============================================
            # Collect statistics
            # ============================================
            worker_stats = self.orchestrator.performance_log.get_snapshot()
            portfolio_stats = self.trade_simulator.portfolio.get_portfolio_statistics()
            execution_stats = self.trade_simulator.get_execution_stats()
            cost_breakdown = self.trade_simulator.portfolio.get_cost_breakdown()

            # Final stats update
            self.deps.performance_log.update_live_stats(
                scenario_index=self.scenario_index,
                ticks_processed=tick_count
            )

            # ============================================
            # Build ScenarioPerformanceStats object
            # ============================================
            stats = ScenarioPerformanceStats(
                scenario_index=self.scenario_index,
                scenario_name=self.scenario.name,
                portfolio_value=self.trade_simulator.portfolio.balance,
                initial_balance=self.trade_simulator.portfolio.initial_balance,
                symbol=self.scenario.symbol,
                ticks_processed=tick_count,
                signals_generated=len(signals),
                signals_gen_buy=signals_gen_buy,
                signals_gen_sell=signals_gen_sell,
                signal_rate=len(signals) / tick_count if tick_count > 0 else 0,
                success=True,
                worker_statistics=worker_stats,
                decision_logic_name=self.decision_logic.name,
                scenario_requirement=self.scenario_requirements.__dict__,
                sample_signals=signals[:10],
                portfolio_stats=portfolio_stats,
                execution_stats=execution_stats,
                cost_breakdown=cost_breakdown,
                profiling_data=profiling_data
            )

            # Write to ScenarioSetPerformanceManager (thread-safe)
            self.deps.performance_log.add_scenario_stats(
                self.scenario_index, stats)

            # Restore thread name
            if self.original_thread_name:
                threading.current_thread().name = self.original_thread_name

            # Return result
            scenario_execution_time_ms = (time.time() - start_time) * 1000.0
            return ScenarioExecutionResult(
                success=True,
                scenario_name=self.scenario.name,
                scenario_index=self.scenario_index,
                scenario_execution_time_ms=scenario_execution_time_ms
            )

        except Exception as e:
            # Wrap any error in ScenarioExecutionError
            error_msg = f"Scenario {self.scenario_index} execution failed: {str(e)}"
            self.scenario.logger.error(
                f"❌ {error_msg}\n{traceback.format_exc()}")

            # Mark as failed in performance log
            self.deps.performance_log.set_live_status(
                scenario_index=self.scenario_index,
                status=ScenarioStatus.FINISHED_WITH_ERROR
            )

            raise ScenarioExecutionError(error_msg) from e

    def execute(
        self,
        scenario: SingleScenario,
        scenario_index: int,
        barrier: Optional[threading.Barrier] = None
    ) -> ScenarioExecutionResult:
        """
        Complete execution with optional barrier synchronization.

        Combines both phases:
        1. Prepare scenario (warmup)
        2. Wait at barrier (if provided)
        3. Execute tick loop (synchronized)

        This is the main entry point for executing a scenario.

        Args:
            scenario: SingleScenario to execute
            scenario_index: Index in scenario list
            barrier: Optional barrier for synchronized start in parallel mode

        Returns:
            ScenarioExecutionResult with execution status

        Raises:
            ScenarioPreparationError: If preparation fails
            ScenarioExecutionError: If execution fails
        """
        # Phase 1: Prepare
        self.prepare_scenario(scenario, scenario_index)

        # Barrier synchronization (if parallel mode)
        if barrier is not None:
            self.scenario.logger.debug(
                f"⏸️  Scenario {scenario_index} ready - waiting at barrier for other scenarios..."
            )

            try:
                # Wait for all threads to reach this point
                barrier.wait(timeout=300)  # 5 minute timeout for safety

                self.scenario.logger.debug(
                    f"🚀 Barrier released - starting tick loop for scenario {scenario_index}"
                )

            except threading.BrokenBarrierError:
                self.scenario.logger.error(
                    f"❌ Barrier broken - another scenario failed during preparation"
                )
                raise
            except Exception as e:
                self.scenario.logger.error(f"❌ Barrier wait failed: {e}")
                raise

        # Phase 2: Execute tick loop
        return self.execute_tick_loop()

    def get_orchestrator(self) -> Optional[WorkerCoordinator]:
        """
        Get WorkerCoordinator for debugging.

        Returns:
            WorkerCoordinator instance if prepared, None otherwise
        """
        return self.orchestrator
