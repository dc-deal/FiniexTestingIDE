

from datetime import timezone
from typing import Dict, Tuple
from python.components.logger.scenario_logger import ScenarioLogger
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.factory.decision_logic_factory import DecisionLogicFactory
from python.framework.factory.trade_simulator_factory import prepare_trade_simulator_for_scenario
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.types.process_data_types import ProcessDataPackage, ProcessPreparedDataObjects, ProcessScenarioConfig
from python.framework.utils.process_debug_info_utils import debug_warmup_bars_check, log_trade_simulator_config
from python.framework.utils.process_serialization_utils import process_deserialize_ticks_batch
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


def process_startup_preparation(
    config: ProcessScenarioConfig,
    shared_data: ProcessDataPackage,
    scenario_logger: ScenarioLogger
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

    scenario_logger.info(f"🚀 Starting scenario: {config.name}")

    # === CREATE WORKERS ===
    worker_factory = WorkerFactory(logger=scenario_logger)
    workers_dict = worker_factory.create_workers_from_config(
        strategy_config=config.strategy_config
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
    worker_coordinator = WorkerOrchestrator(
        decision_logic=decision_logic,
        strategy_config=config.strategy_config,
        workers=workers,
        parallel_workers=config.parallel_workers,
        parallel_threshold_ms=config.parallel_threshold
    )
    worker_coordinator.initialize()

    scenario_logger.debug(
        f"✅ Orchestrator initialized: {len(workers)} workers + {decision_logic.name}"
    )
    # === CREATE TRADE SIMULATOR ===
    # 1. Create isolated TradeSimulator for THIS scenario
    # + set Trading capabilities on decision logic.
    trade_simulator = prepare_trade_simulator_for_scenario(
        config=config,
        logger=scenario_logger,
        decision_logic=decision_logic,
        shared_data=shared_data
    )

    scenario_logger.debug(
        f"✅ Created trade simulator: "
        f"{config.initial_balance} {config.account_currency}"
    )

    log_trade_simulator_config(scenario_logger, config, trade_simulator)

    # === CREATE BAR RENDERING CONTROLLER ===
    bar_rendering_controller = BarRenderingController(
        logger=scenario_logger)
    bar_rendering_controller.register_workers(workers)

    # === MATCH AND VALIDATE WARMUP BARS ===
    warmup_bars = _match_and_validate_warmup_bars(
        config=config,
        shared_data=shared_data,
        bar_rendering_controller=bar_rendering_controller,
        scenario_logger=scenario_logger
    )

    # Inject warmup bars (line 149, unchanged)
    bar_rendering_controller.inject_warmup_bars(
        symbol=config.symbol, warmup_bars=warmup_bars)

    # Debug bars from warmup (line 151, unchanged)
    debug_warmup_bars_check(
        warmup_bars=warmup_bars,
        config=config,
        logger=scenario_logger,
        bar_rendering_controller=bar_rendering_controller
    )

    scenario_logger.debug(
        f"✅ Injected warmup bars: "
        f"{', '.join(f'{tf}:{len(bars)}' for tf, bars in warmup_bars.items())}"
    )
    # Ticks deserialisieren
    ticks = process_deserialize_ticks_batch(
        scenario_name=config.name, scenario_symbol=config.symbol, ticks_tuple_list=shared_data.ticks)
    scenario_logger.debug(
        f"🔄 De-Serialization of {len(ticks):,} ticks finished")

    return ProcessPreparedDataObjects(
        worker_coordinator=worker_coordinator,
        trade_simulator=trade_simulator,
        bar_rendering_controller=bar_rendering_controller,
        decision_logic=decision_logic,
        scenario_logger=scenario_logger,
        ticks=ticks
    )


def _match_and_validate_warmup_bars(
    config: ProcessScenarioConfig,
    shared_data: ProcessDataPackage,
    bar_rendering_controller: BarRenderingController,
    scenario_logger: ScenarioLogger
) -> Dict[str, Tuple]:
    """
    Match warmup bars from shared_data to scenario and validate sufficiency.

    Logs ERROR if insufficient bars found but allows run to continue.
    This handles scenarios starting at data coverage boundaries.

    Args:
        config: Scenario configuration
        shared_data: Shared data package with warmup bars
        bar_rendering_controller: Controller with worker requirements
        scenario_logger: Logger for this scenario

    Returns:
        Dict[timeframe, bars_tuple] - Matched warmup bars
    """
    # UTC-AWARE COMPARISON FIX
    # shared_data keys use UTC-aware datetime (from SharedDataPreparator)
    # config.start_time may be naive (from JSON parsing)
    config_start = config.start_time
    if config_start.tzinfo is None:
        config_start = config_start.replace(tzinfo=timezone.utc)

    # Match bars from shared_data to this specific scenario
    # Keys in shared_data.bars: (symbol, timeframe, scenario_start_time)
    warmup_bars = {}
    for key, bars_tuple in shared_data.bars.items():
        symbol, timeframe, start_time = key

        # Match criteria: symbol AND start_time must match exactly
        symbol_match = symbol == config.symbol
        time_match = start_time == config_start

        if symbol_match and time_match:
            scenario_logger.debug(
                f"✅ MATCH: ({symbol}, {timeframe}, {start_time}) → {len(bars_tuple)} bars"
            )
            warmup_bars[timeframe] = bars_tuple

    # Validate bar count vs requirements
    required_timeframes = bar_rendering_controller._required_timeframes

    if len(required_timeframes) > 0:
        # Check each timeframe for sufficient bars
        insufficient_bars = []

        for timeframe in required_timeframes:
            bars_tuple = warmup_bars.get(timeframe)
            if bars_tuple is None:
                # No bars found for this timeframe at all
                insufficient_bars.append(
                    f"{timeframe}: 0 bars (missing completely)"
                )
            else:
                # Get required count from worker
                # Note: bars_tuple length is actual bar count
                actual_count = len(bars_tuple)

                # Find worker requiring this timeframe to get period requirement
                # This is imperfect but safe: we log if ANY bars are missing
                # The worker itself knows its exact requirement
                for worker in bar_rendering_controller._workers:
                    worker_requirements = worker.get_warmup_requirements()
                    if timeframe in worker_requirements:
                        required_count = worker_requirements[timeframe]

                        if actual_count < required_count:
                            insufficient_bars.append(
                                f"{timeframe}: {actual_count}/{required_count} bars"
                            )
                        break

        # Log ERROR if insufficient bars found
        if insufficient_bars:
            scenario_logger.error(
                f"⚠️  INSUFFICIENT WARMUP BARS for scenario '{config.name}'!\n"
                f"  This scenario likely starts at the edge of data coverage.\n"
                f"  Insufficient bars: {', '.join(insufficient_bars)}\n"
                f"  Config start_time: {config_start}\n"
                f"  Strategy will run with incomplete indicator warmup.\n"
                f"  Results may be unreliable until indicators have full history."
            )

        # Log result summary
        scenario_logger.debug(
            f"📊 Result: {len(warmup_bars)}/{len(required_timeframes)} timeframes collected"
        )

    return warmup_bars
