"""
FiniexTestingIDE - AutoTrader Startup Preparation
Pipeline object creation for live AutoTrader sessions.

Mirrors process_startup_preparation.py for backtesting.
"""

from datetime import datetime
from pathlib import Path
from typing import Tuple

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.autotrader.autotrader_broker_config_setup import create_broker_config
from python.framework.autotrader.autotrader_sentiment_feed import setup_sentiment_feed
from python.framework.autotrader.autotrader_warmup_preparator import AutotraderWarmupPreparator
from python.framework.autotrader.live_clipping_monitor import LiveClippingMonitor
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.factory.decision_logic_factory import DecisionLogicFactory
from python.framework.factory.live_trade_executor_factory import build_live_executor
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.logging.file_logger import FileLogger
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.trading_env.decision_trading_api import DecisionTradingApi
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.display_label_cache import DisplayLabelCache
from python.framework.types.config_types.market_config_types import TradingModel
from python.framework.types.market_types.market_types import TradingContext
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


def create_autotrader_loggers(
    config: AutoTraderConfig,
    run_timestamp: datetime
) -> Tuple[ScenarioLogger, ScenarioLogger, ScenarioLogger, Path]:
    """
    Create all loggers for an AutoTrader session.

    Three separate loggers with distinct purposes:
    - global: Startup phases, shutdown, errors (file + direct console print)
    - session: Per-tick processing, daily rotated in session_logs/ subdir
    - summary: Post-session summary (file + console flush)

    Log directory layout:
        logs/autotrader/<name>/<timestamp>/
            autotrader_global.log
            autotrader_summary.log
            session_logs/
                autotrader_session_YYYYMMDD.log
            events.csv

    Args:
        config: AutoTrader configuration
        run_timestamp: Session start timestamp (UTC)

    Returns:
        (global_logger, session_logger, summary_logger, run_dir)
    """
    session_name = config.name or f'{config.symbol}_{config.adapter_type}'
    log_root = Path('logs/autotrader')

    # Global logger — startup/shutdown/errors
    global_logger = ScenarioLogger(
        scenario_set_name=session_name,
        scenario_name='global',
        run_timestamp=run_timestamp,
        log_root_override=log_root,
        file_name_prefix_override='autotrader'
    )

    run_dir = global_logger.get_log_dir()

    # Summary logger — post-session report (shares run_dir with global)
    summary_logger = ScenarioLogger(
        scenario_set_name=session_name,
        scenario_name='summary',
        run_timestamp=run_timestamp,
        log_root_override=log_root,
        file_name_prefix_override='autotrader'
    )

    # Session logger — tick loop, daily rotated in session_logs/ subdir
    # Initial file logger is a placeholder — the tick loop swaps it on
    # the first tick to match the tick's date (avoids wallclock vs replay mismatch).
    session_logger = ScenarioLogger(
        scenario_set_name=session_name,
        scenario_name='session',
        run_timestamp=run_timestamp,
        log_root_override=log_root,
        file_name_prefix_override='autotrader'
    )

    # Create session_logs/ subdir (tick loop will create files there)
    if run_dir:
        session_logs_dir = run_dir / 'session_logs'
        session_logs_dir.mkdir(parents=True, exist_ok=True)

    return global_logger, session_logger, summary_logger, run_dir


def create_session_file_logger(run_dir: Path, date_suffix: str, log_level) -> FileLogger:
    """
    Create a new FileLogger for a specific day's session log.

    Used for daily rotation: when the tick date changes, the tick loop
    calls this to get a fresh FileLogger for the new day.

    Args:
        run_dir: Session run directory (contains session_logs/ subdir)
        date_suffix: Date string for filename (YYYYMMDD)
        log_level: Log level for the file logger

    Returns:
        FileLogger writing to session_logs/autotrader_session_YYYYMMDD.log
    """
    session_logs_dir = run_dir / 'session_logs'
    session_logs_dir.mkdir(parents=True, exist_ok=True)
    return FileLogger(
        log_filename=f'autotrader_session_{date_suffix}.log',
        file_path=session_logs_dir,
        log_level=log_level
    )


def setup_pipeline(
    config: AutoTraderConfig,
    logger: ScenarioLogger
) -> Tuple[AbstractTradeExecutor, BarRenderingController, WorkerOrchestrator, AbstractDecisionLogic, LiveClippingMonitor, TradingModel, DisplayLabelCache]:
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
    9. Warmup bar injection (mock: parquet, live: API)
    10. Create LiveClippingMonitor

    Args:
        config: AutoTrader configuration
        logger: ScenarioLogger instance

    Returns:
        (executor, bar_controller, worker_orchestrator, decision_logic, clipping_monitor, trading_model, display_label_cache)
    """
    # === Phase 1: Broker Config ===
    broker_config = create_broker_config(config, logger)

    # === Phase 2: DecisionLogic Requirements ===
    decision_logic_factory = DecisionLogicFactory(logger=logger)
    decision_logic_class, _ = decision_logic_factory.resolve_logic_class(
        config.strategy_config.get('decision_logic_type', '')
    )
    required_order_types = decision_logic_class.get_required_order_types(
        config.strategy_config.get('decision_logic_config', {})
    )
    logger.debug(
        f"📋 Decision logic requires: {[t.value for t in required_order_types]}"
    )

    # === Phase 3: Resolve trading model ===
    market_config_manager = MarketConfigManager()
    market_type = market_config_manager.get_market_type(config.broker_type)
    trading_model = market_config_manager.get_trading_model(config.broker_type)
    spot_mode = trading_model == TradingModel.SPOT

    # Validate: balances must be configured
    if not config.account.balances:
        raise ValueError(
            f"Configuration error: AutoTrader profile '{config.name}' has no 'balances' "
            f"defined in account config.\n"
            f"Add to profile:\n"
            f'  "account": {{ "balances": {{ "USD": 10000.0 }} }}'
        )

    # Determine account_currency: explicit override or derive from balances + symbol
    symbol_spec = broker_config.adapter.get_symbol_specification(config.symbol)
    if config.account.account_currency:
        account_currency = config.account.account_currency
    elif symbol_spec.quote_currency in config.account.balances:
        account_currency = symbol_spec.quote_currency
    elif symbol_spec.base_currency in config.account.balances:
        account_currency = symbol_spec.base_currency
    else:
        account_currency = list(config.account.balances.keys())[0]

    # === Phase 4: LiveTradeExecutor ===
    broker_entry = market_config_manager.get_broker_entry(config.broker_type)
    executor = build_live_executor(
        broker_config=broker_config,
        balances=config.account.balances,
        account_currency=account_currency,
        logger=logger,
        spot_mode=spot_mode,
        poll_interval_ms=broker_entry.broker_transport.poll_interval_ms,
    )
    logger.info(
        f"💱 LiveTradeExecutor created: balances={config.account.balances}"
    )

    # === Phase 5: TradingContext ===
    adapter = broker_config.adapter
    volume_min = adapter.get_symbol_specification(config.symbol).volume_min
    trading_context = TradingContext(
        broker_type=BrokerType(config.broker_type),
        market_type=market_type,
        symbol=config.symbol,
        volume_min=volume_min,
        trading_model=trading_model,
        pip_size=adapter.get_pip_size(config.symbol),
    )

    # === Phase 6: Workers ===
    worker_factory = WorkerFactory(logger=logger)
    workers_dict = worker_factory.create_workers_from_config(
        strategy_config=config.strategy_config,
        trading_context=trading_context
    )
    workers = list(workers_dict.values())
    logger.debug(f"✅ Created {len(workers)} workers")

    # === Phase 6b: Sentiment Feed (mock, #431) ===
    setup_sentiment_feed(config, workers, logger)

    # === Phase 7: DecisionLogic ===
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

    # === Phase 8: WorkerOrchestrator + DecisionTradingApi ===
    worker_orchestrator = WorkerOrchestrator(
        decision_logic=decision_logic,
        strategy_config=config.strategy_config,
        workers=workers,
        parallel_workers=config.execution.parallel_workers,
        worker_decision_tracking=config.execution.performance_tracking.worker_decision_tracking,
    )
    worker_orchestrator.initialize()
    logger.debug(f"✅ Orchestrator initialized: {len(workers)} workers")

    trading_api = DecisionTradingApi(
        executor=executor,
        required_order_types=required_order_types,
        order_guard_config=config.order_guard,
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

    # === Phase 9: Warmup + Display Label Cache ===
    warmup_preparator = AutotraderWarmupPreparator(logger=logger)
    warmup_preparator.prepare_and_inject(
        config=config,
        workers=workers,
        bar_controller=bar_controller,
    )
    display_label_cache = warmup_preparator.build_display_label_cache(
        decision_logic=decision_logic,
        workers=workers,
        sentiment_source=config.sentiment_source.get_feed_label(),
    )

    # === Phase 10: LiveClippingMonitor ===
    clipping_monitor = LiveClippingMonitor(
        report_interval_s=config.clipping_monitor.report_interval_s,
        strategy=config.clipping_monitor.strategy,
    )
    logger.debug(
        f"✅ ClippingMonitor: strategy={config.clipping_monitor.strategy}, "
        f"report_interval={config.clipping_monitor.report_interval_s}s"
    )

    return executor, bar_controller, worker_orchestrator, decision_logic, clipping_monitor, trading_model, display_label_cache
