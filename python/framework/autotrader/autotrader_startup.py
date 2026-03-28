"""
FiniexTestingIDE - AutoTrader Startup Preparation
Pipeline object creation for live AutoTrader sessions.

Mirrors process_startup_preparation.py for backtesting.
"""

import queue
import threading
from pathlib import Path

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.autotrader.tick_sources.abstract_tick_source import AbstractTickSource
from python.framework.autotrader.live_clipping_monitor import LiveClippingMonitor
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.factory.decision_logic_factory import DecisionLogicFactory
from python.framework.factory.live_trade_executor_factory import build_live_executor
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.testing.mock_adapter import MockBrokerAdapter
from python.framework.autotrader.tick_sources.mock_tick_source import MockTickSource
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.decision_trading_api import DecisionTradingApi
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.market_types.market_types import TradingContext
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


def create_autotrader_logger(
    config: AutoTraderConfig,
    run_timestamp: 'datetime'
) -> ScenarioLogger:
    """
    Create logger for AutoTrader session.

    Uses ScenarioLogger with custom log root: logs/autotrader/<name>/<timestamp>/
    Separate from backtesting log tree (logs/scenario_sets/).

    Args:
        config: AutoTrader configuration
        run_timestamp: Session start timestamp

    Returns:
        ScenarioLogger instance
    """
    session_name = config.name or f'{config.symbol}_{config.adapter_type}'
    return ScenarioLogger(
        scenario_set_name=session_name,
        scenario_name=session_name,
        run_timestamp=run_timestamp,
        log_root_override=Path('logs/autotrader'),
        file_name_prefix_override='autotrader'
    )


def setup_pipeline(
    config: AutoTraderConfig,
    logger: ScenarioLogger
) -> tuple:
    """
    Create all pipeline objects for AutoTrader session.

    Mirrors process_startup_preparation phases:
    1. Load broker config from JSON
    2. Get DecisionLogic requirements
    3. Create LiveTradeExecutor via factory
    4. Create TradingContext
    5. Create Workers
    6. Create DecisionLogic
    7. Create WorkerOrchestrator + wire DecisionTradingApi
    8. Create BarRenderingController
    9. Create LiveClippingMonitor

    Args:
        config: AutoTrader configuration
        logger: ScenarioLogger instance

    Returns:
        (executor, bar_controller, worker_orchestrator, decision_logic, clipping_monitor)
    """
    # === Phase 1: Broker Config ===
    broker_config = _create_broker_config(config)

    # === Phase 2: DecisionLogic Requirements ===
    decision_logic_factory = DecisionLogicFactory(logger=logger)
    decision_logic_class = decision_logic_factory._resolve_logic_class(
        config.strategy_config.get('decision_logic_type', '')
    )
    required_order_types = decision_logic_class.get_required_order_types(
        config.strategy_config.get('decision_logic_config', {})
    )
    logger.debug(
        f"📋 Decision logic requires: {[t.value for t in required_order_types]}"
    )

    # === Phase 3: LiveTradeExecutor ===
    executor = build_live_executor(
        broker_config=broker_config,
        initial_balance=config.account.initial_balance,
        account_currency=config.account.currency,
        logger=logger,
    )
    logger.info(
        f"💱 LiveTradeExecutor created: "
        f"{config.account.initial_balance} {config.account.currency}"
    )

    # === Phase 4: TradingContext ===
    market_config_manager = MarketConfigManager()
    market_type = market_config_manager.get_market_type(
        config.broker_type
    )
    trading_context = TradingContext(
        broker_type=BrokerType(config.broker_type),
        market_type=market_type,
        symbol=config.symbol
    )

    # === Phase 5: Workers ===
    worker_factory = WorkerFactory(logger=logger)
    workers_dict = worker_factory.create_workers_from_config(
        strategy_config=config.strategy_config,
        trading_context=trading_context
    )
    workers = list(workers_dict.values())
    logger.debug(f"✅ Created {len(workers)} workers")

    # === Phase 6: DecisionLogic ===
    decision_logic = decision_logic_factory.create_logic(
        logic_type=config.strategy_config.get('decision_logic_type', ''),
        logic_config=config.strategy_config.get('decision_logic_config', {}),
        logger=logger,
        trading_context=trading_context
    )
    logger.debug(
        f"✅ Created decision logic: "
        f"{config.strategy_config.get('decision_logic_type', '')}"
    )

    # === Phase 7: WorkerOrchestrator + DecisionTradingApi ===
    worker_orchestrator = WorkerOrchestrator(
        decision_logic=decision_logic,
        strategy_config=config.strategy_config,
        workers=workers,
        parallel_workers=config.execution.parallel_workers,
    )
    worker_orchestrator.initialize()
    logger.debug(f"✅ Orchestrator initialized: {len(workers)} workers")

    trading_api = DecisionTradingApi(
        executor=executor,
        required_order_types=required_order_types
    )
    decision_logic.set_trading_api(trading_api)
    logger.debug('✅ DecisionTradingApi injected')

    # === Phase 8: BarRenderingController ===
    bar_controller = BarRenderingController(
        logger=logger,
        max_history=config.execution.bar_max_history
    )
    bar_controller.register_workers(workers)
    logger.debug('✅ BarRenderingController created')

    # === Phase 9: Warmup — SKIPPED (Step 1b adds this) ===
    logger.debug('⏭️  Warmup skipped (Step 1b)')

    # === Phase 10: LiveClippingMonitor ===
    clipping_monitor = LiveClippingMonitor(
        report_interval_s=config.clipping_monitor.report_interval_s,
        strategy=config.clipping_monitor.strategy,
    )
    logger.debug(
        f"✅ ClippingMonitor: strategy={config.clipping_monitor.strategy}, "
        f"report_interval={config.clipping_monitor.report_interval_s}s"
    )

    return executor, bar_controller, worker_orchestrator, decision_logic, clipping_monitor


def setup_tick_source(
    config: AutoTraderConfig,
    tick_queue: queue.Queue,
    logger: ScenarioLogger
) -> tuple:
    """
    Create tick source and start it in a separate thread.

    Threading model 8.a: tick source pushes to queue.Queue,
    main thread pulls via queue.get().

    Args:
        config: AutoTrader configuration
        tick_queue: Thread-safe queue for tick delivery
        logger: Logger instance

    Returns:
        (tick_source, tick_thread)
    """
    if config.tick_source.type == 'mock':
        tick_source = MockTickSource(
            parquet_path=config.tick_source.parquet_path,
            symbol=config.symbol,
            tick_queue=tick_queue,
            mode=config.tick_source.mode,
        )
    else:
        raise ValueError(
            f"Unknown tick source type: '{config.tick_source.type}'. "
            f"Supported: 'mock'. WebSocket tick source: #232."
        )

    tick_thread = threading.Thread(
        target=tick_source.start,
        name='AutoTrader-TickSource',
        daemon=True,
    )
    tick_thread.start()
    logger.info(
        f"📡 Tick source started: {config.tick_source.type} "
        f"(mode={config.tick_source.mode})"
    )

    return tick_source, tick_thread


def _create_broker_config(config: AutoTraderConfig) -> BrokerConfig:
    """
    Load broker config and attach appropriate adapter.

    For adapter_type='mock': uses MockBrokerAdapter.
    For adapter_type='live': loads from broker config JSON (future).

    Args:
        config: AutoTrader configuration

    Returns:
        BrokerConfig with adapter
    """
    # Load base broker config from JSON
    broker_config = BrokerConfigFactory.build_broker_config(
        config.broker_config_path
    )

    if config.adapter_type == 'mock':
        # Replace adapter with MockBrokerAdapter (live-capable)
        mock_adapter = MockBrokerAdapter(
            broker_config=broker_config.adapter.broker_config
        )
        return BrokerConfig(
            broker_type=broker_config.broker_type,
            adapter=mock_adapter
        )

    # Future: adapter_type='live' → use adapter from broker config as-is
    return broker_config
