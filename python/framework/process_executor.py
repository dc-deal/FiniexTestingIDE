"""
FiniexTestingIDE - Process Executor (CORRECTED)
Process-based scenario execution with ProcessPool support
"""
from datetime import datetime
import time
import traceback
from collections import defaultdict
from typing import Any, Dict, List, Tuple

from python.configuration.app_config_loader import AppConfigLoader
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.trading_env.decision_trading_api import DecisionTradingAPI
from python.framework.types.order_types import OrderStatus
from python.framework.types.process_data_types import (
    ProcessPreparedDataObjects,
    ProcessDataPackage,
    ProcessProfileData,
    ProcessTickLoopResult,
    ProcessScenarioConfig,
    ProcessResult
)
from python.framework.types.scenario_set_types import SingleScenario
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.factory.decision_logic_factory import DecisionLogicFactory
from python.framework.trading_env.trade_simulator import TradeSimulator
from python.framework.types.market_data_types import TickData
from python.framework.workers.worker_coordinator import WorkerCoordinator
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.components.logger.scenario_logger import ScenarioLogger
from python.framework.trading_env.broker_config import BrokerConfig

# ============================================================================
# TOP-LEVEL FUNCTIONS (ProcessPool-compatible)
# ============================================================================


def process_main(
    config: ProcessScenarioConfig,
    shared_data: ProcessDataPackage
) -> ProcessResult:
    """
    Main process entry point.

    TOP-LEVEL FUNCTION: Can be called by ProcessPoolExecutor.
    Creates all objects, runs tick loop, returns results.

    Args:
        config: Serializable scenario configuration
        shared_data: Shared read-only data (CoW)

    Returns:
        ProcessResult with execution results or error details
    """
    try:
        start_time = time.time()
        # === STARTUP PREPARATION ===
        prepared_objects = process_startup_preparation(config, shared_data)
        scenario_logger = prepared_objects.scenario_logger
        scenario_logger.debug(
            f"🔄 Process preperation finished")

        # === TICK LOOP EXECUTION ===
        tick_loop_results = execute_tick_loop(
            config, prepared_objects)
        scenario_logger.debug(
            f"🔄 Execute tick loop finished")

        # === BUILD RESULT ===
        # logger.run_timestamp - start

        result = ProcessResult(
            success=True,
            scenario_name=config.name,
            symbol=config.symbol,
            scenario_index=config.scenario_index,
            execution_time_ms=time.time() - start_time,
            tick_loop_results=tick_loop_results,
        )

        scenario_logger.info(f"🕐 Before flush: {time.time()}")
        scenario_logger.flush_buffer()
        scenario_logger.close()

        scenario_logger.info(
            f"🕐 {config.name} returning at {time.time()}")
        return result

    except Exception as e:
        # Error handling: Return error details for logging
        return ProcessResult(
            success=False,
            scenario_name=config.name,
            symbol=config.symbol,
            scenario_index=config.scenario_index,
            error_type=type(e).__name__,
            error_message=str(e),
            traceback=traceback.format_exc()
        )


def process_startup_preparation(
    config: ProcessScenarioConfig,
    shared_data: ProcessDataPackage
) -> ProcessPreparedDataObjects:
    """
    Create all objects needed in subprocess.

    CRITICAL: Everything created HERE, nothing passed from main process.

    Created objects:
    - ScenarioLogger (with shared run_timestamp)
    - Workers (from strategy_config)
    - Decision logic
    - Worker coordinator
    - Trade simulator
    - Bar rendering controller

    Args:
        config: Scenario configuration
        shared_data: Shared data package

    Returns:
        Dictionary with all prepared objects
    """
    # === CREATE SCENARIO LOGGER ===
    # CORRECTED: Use shared run_timestamp from BatchOrchestrator
    scenario_logger = ScenarioLogger(
        scenario_set_name=config.scenario_set_name,
        scenario_name=config.name,
        run_timestamp=config.run_timestamp
    )

    scenario_logger.info(f"🚀 Starting scenario: {config.name}")

    # === CREATE WORKERS ===
    worker_factory = WorkerFactory()
    workers_dict = worker_factory.create_workers_from_config(
        strategy_config=config.strategy_config,
        logger=scenario_logger
    )
    workers = list(workers_dict.values())

    scenario_logger.debug(f"✅ Created {len(workers)} workers")

    # === CREATE DECISION LOGIC ===
    decision_logic_factory = DecisionLogicFactory(logger=scenario_logger)
    decision_logic = decision_logic_factory.create_logic(
        logic_type=config.decision_logic_type,
        logic_config=config.decision_logic_config,
        logger=scenario_logger
    )

    scenario_logger.debug(
        f"✅ Created decision logic: {config.decision_logic_type}")

    # === CREATE WORKER COORDINATOR ===
    coordinator = WorkerCoordinator(
        decision_logic=decision_logic,
        strategy_config=config.strategy_config,
        workers=workers,
        parallel_workers=config.parallel_workers,
        parallel_threshold_ms=config.parallel_threshold
    )
    coordinator.initialize()

    scenario_logger.debug(
        f"✅ Orchestrator initialized: {len(workers)} workers + {decision_logic.name}"
    )
    # === CREATE TRADE SIMULATOR ===
    # 1. Create isolated TradeSimulator for THIS scenario
    # + set Trading capabilities on decision logic.
    trade_simulator = prepare_trade_simulator_for_scenario(
        config=config,
        logger=scenario_logger,
        decision_logic=decision_logic
    )

    scenario_logger.debug(
        f"✅ Created trade simulator: "
        f"{config.initial_balance} {config.currency}"
    )

    # === CREATE BAR RENDERING CONTROLLER ===
    bar_rendering_controller = BarRenderingController(
        logger=scenario_logger)
    bar_rendering_controller.register_workers(workers)

    # Get warmup bars from shared_data
    # CORRECTED: No validation - trusts SharedDataPreparator filtering
    warmup_bars = {}
    for key, bars_tuple in shared_data.bars.items():
        symbol, timeframe, start_time = key

        # Only inject bars matching this scenario
        if symbol == config.symbol and start_time == config.start_time:
            warmup_bars[timeframe] = bars_tuple

    # Inject warmup bars (no validation)
    bar_rendering_controller.inject_warmup_bars(
        symbol=config.symbol, warmup_bars=warmup_bars)

    scenario_logger.debug(
        f"✅ Injected warmup bars: "
        f"{', '.join(f'{tf}:{len(bars)}' for tf, bars in warmup_bars.items())}"
    )

    # Ticks deserialisieren
    ticks = deserialize_ticks_batch(symbol, shared_data.ticks[symbol])
    scenario_logger.debug(
        f"🔄 De-Serialization of {len(ticks):,} ticks finished")

    return ProcessPreparedDataObjects(
        coordinator=coordinator,
        trade_simulator=trade_simulator,
        bar_rendering_controller=bar_rendering_controller,
        decision_logic=decision_logic,
        scenario_logger=scenario_logger,
        ticks=ticks
    )


def execute_tick_loop(
    config: ProcessScenarioConfig,
    prepared_objects: ProcessPreparedDataObjects
) -> ProcessTickLoopResult:
    """
    Execute tick processing loop.

    MAIN PROCESSING: Iterate through ticks, process each.

    Args:
        config: Scenario configuration
        shared_data: Shared data package
        prepared_objects: Objects from startup_preparation

    Returns:
        Dictionary with loop results
    """
    coordinator = prepared_objects.coordinator
    trade_simulator = prepared_objects.trade_simulator
    bar_rendering_controller = prepared_objects.bar_rendering_controller
    scenario_logger = prepared_objects.scenario_logger
    decision_logic = prepared_objects.decision_logic

    # Get ticks from shared_data (CoW!)
    # ticks = shared_data.ticks[config.symbol]
    # ToDo unoptimized, no CoW -
    ticks = prepared_objects.ticks

    # Performance profiling
    profile_times = defaultdict(float)
    profile_counts = defaultdict(int)

    signals = []
    tick_count = 0

    scenario_logger.info(f"🔄 Starting tick loop ({len(ticks):,} ticks)")

    # === TICK LOOP ===
    for tick in ticks:
        tick_count += 1
        tick_start = time.perf_counter()

        # === 1. Trade Simulator ===
        t5 = time.perf_counter()
        trade_simulator.update_prices(tick)
        t6 = time.perf_counter()
        profile_times['trade_simulator'] += (t6 - t5) * 1000
        profile_counts['trade_simulator'] += 1

        # === 2. Bar Rendering ===
        t3 = time.perf_counter()
        current_bars = bar_rendering_controller.process_tick(tick)
        t4 = time.perf_counter()
        profile_times['bar_rendering'] += (t4 - t3) * 1000
        profile_counts['bar_rendering'] += 1

        # === 3. Bar History Retrieval ===
        t5 = time.perf_counter()
        bar_history = bar_rendering_controller.get_all_bar_history(
            symbol=config.symbol
        )
        t6 = time.perf_counter()
        profile_times['bar_history'] += (t6 - t5) * 1000
        profile_counts['bar_history'] += 1

        # === 4. Worker Processing + Decision ===
        t7 = time.perf_counter()
        decision = coordinator.process_tick(
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
                order_result = decision_logic.execute_decision(
                    decision, tick
                )

                if order_result and order_result.status == OrderStatus.PENDING:
                    signals.append({
                        **decision.to_dict(),
                        'order_id': order_result.order_id,
                        'executed_price': order_result.executed_price,
                        'lot_size': order_result.executed_lots
                    })

                    # self.deps.performance_log.update_live_stats(
                    #     scenario_index=self.scenario_index,
                    #     ticks_processed=tick_count
                    # )
            except Exception as e:
                scenario_logger.error(
                    f"Order execution failed: \n{traceback.format_exc()}"
                )

            t10 = time.perf_counter()
            profile_times['order_execution'] += (t10 - t9) * 1000
            profile_counts['order_execution'] += 1

        # === 6. Periodic Stats Update ===
        # if tick_count % 500 == 0:
        #     t11 = time.perf_counter()
        #     self.deps.performance_log.update_live_stats(
        #         scenario_index=self.scenario_index,
        #         ticks_processed=tick_count
        #     )
        #     t12 = time.perf_counter()
        #     profile_times['stats_update'] += (t12 - t11) * 1000
        #     profile_counts['stats_update'] += 1

        # Total tick time
        tick_end = time.perf_counter()
        profile_times['total_per_tick'] += (
            tick_end - tick_start) * 1000

    scenario_logger.info(
        f"✅ Tick loop completed: {tick_count:,} ticks, {len(signals)} signals")

    # close opten trades
    trade_simulator.close_all_remaining_orders()

    scenario_logger.info(
        f"✅ Tick loop completed: {tick_count:,} ticks, {len(signals)} signals")

    # === CLEANUP COORDINATOR ===
    scenario_logger.info(
        f"[CLEANUP] {config.name} calling cleanup at {time.time()}")
    coordinator.cleanup()
    scenario_logger.debug("✅ Coordinator cleanup completed")

    # === GET RESULTS ===
    performance_stats = coordinator.performance_log.get_snapshot()
    portfolio_stats = trade_simulator.portfolio.get_portfolio_statistics()
    execution_stats = trade_simulator.get_execution_stats()
    cost_breakdown = trade_simulator.portfolio.get_cost_breakdown()

    return ProcessTickLoopResult(
        performance_stats=performance_stats,
        portfolio_stats=portfolio_stats,
        execution_stats=execution_stats,
        cost_breakdown=cost_breakdown,
        profiling_data=ProcessProfileData(
            profile_times=profile_times, profile_counts=profile_counts)
    )


def prepare_trade_simulator_for_scenario(logger: ScenarioLogger, config: ProcessScenarioConfig, decision_logic: AbstractDecisionLogic) -> TradeSimulator:
    """
    Create isolated TradeSimulator for a scenario.

    Each scenario gets its own TradeSimulator instance for:
    - Thread-safety in parallel execution
    - Independent balance/equity tracking
    - Clean statistics per scenario
    """

    # Extract configuration
    broker_config_path = config.broker_config_path
    if broker_config_path is None:
        raise ValueError(
            "No broker_config_path specified in strategy_config. "
            "Example: 'global.trade_simulator_config.broker_config_path': "
            "'./configs/brokers/mt5/ic_markets_demo.json'"
        )

    # Create broker config
    broker_config = BrokerConfig.from_json(broker_config_path)
    # Create NEW TradeSimulator for this scenario
    trade_simulator = TradeSimulator(
        broker_config=broker_config,
        initial_balance=config.initial_balance,
        currency=config.currency,
        logger=logger
    )

    # Create and validate DecisionTradingAPI
    # Interface for Decision Logic to interact with trading environment
    # why? Decision logic may not acess all of Trading Simulator, so
    # it will only exposed what's nessecary - and - aviable (order types).
    try:
        required_order_types = decision_logic.get_required_order_types()
        trading_api = DecisionTradingAPI(
            trade_simulator=trade_simulator,
            required_order_types=required_order_types
        )
        logger.debug(
            f"✅ DecisionTradingAPI validated for order types: "
            f"{[t.value for t in required_order_types]}"
        )
    except ValueError as e:
        logger.error(f"Order type validation failed: {e}")
        raise ValueError(
            f"Broker does not support required order types: {e}"
        )

    # 5. Inject DecisionTradingAPI into Decision Logic
    decision_logic.set_trading_api(trading_api)
    logger.debug(
        "✅ DecisionTradingAPI injected into Decision Logic")

    return trade_simulator


def deserialize_ticks_batch(symbol: str, ticks_tuple: Tuple[Any, ...]) -> List[TickData]:
    """
    Optimierte Batch-Deserialisierung für große Tick-Mengen.

    Nutzt list comprehension für bessere Performance.
    """
    result = []
    for tick_data in ticks_tuple:
        if isinstance(tick_data, TickData):
            result.append(tick_data)
        elif isinstance(tick_data, dict):
            ts = tick_data['timestamp']
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)

            result.append(TickData(
                timestamp=ts,
                symbol=symbol,
                bid=float(tick_data['bid']),
                ask=float(tick_data['ask']),
                volume=float(tick_data.get('volume', 0.0))
            ))
    return result

# ============================================================================
# PROCESS EXECUTOR CLASS (Orchestration Wrapper)
# ============================================================================


class ProcessExecutor:
    """
    Orchestrates scenario execution.

    Wrapper around top-level process functions.
    Provides clean interface for BatchOrchestrator.

    DESIGN:
    - Holds scenario and config
    - Calls process_main() (top-level function)
    - Compatible with ThreadPoolExecutor and ProcessPoolExecutor
    """

    def __init__(
        self,
        scenario: SingleScenario,
        app_config: AppConfigLoader,
        scenario_index: int,
        scenario_set_name: str,
        run_timestamp: str
    ):
        """
        Initialize process executor.

        CORRECTED: Added scenario_set_name and run_timestamp

        Args:
            scenario: Scenario to execute
            app_config: Application configuration
            scenario_index: Index in scenario list
            scenario_set_name: Name of scenario set (for logger)
            run_timestamp: Shared timestamp (for logger)
        """
        self.scenario = scenario
        self.app_config = app_config
        self.scenario_index = scenario_index
        self.scenario_set_name = scenario_set_name
        self.run_timestamp = run_timestamp

        # Create config (serializable)
        self.config = ProcessScenarioConfig.from_scenario(
            scenario=scenario,
            app_config=app_config,
            scenario_index=scenario_index,
            scenario_set_name=scenario_set_name,
            run_timestamp=run_timestamp
        )

    def run(self, shared_data: ProcessDataPackage) -> ProcessResult:
        """
        Execute scenario with shared data.

        Entry point for executor. Calls process_main().

        Args:
            shared_data: Prepared shared data

        Returns:
            ProcessResult with execution results
        """
        return process_main(self.config, shared_data)
