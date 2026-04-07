"""
FiniexTestingIDE - AutoTrader Startup Preparation
Pipeline object creation for live AutoTrader sessions.

Mirrors process_startup_preparation.py for backtesting.
"""

import json
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.autotrader.autotrader_warmup_preparator import AutotraderWarmupPreparator
from python.framework.autotrader.tick_sources.abstract_tick_source import AbstractTickSource
from python.framework.autotrader.live_clipping_monitor import LiveClippingMonitor
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.factory.decision_logic_factory import DecisionLogicFactory
from python.framework.factory.live_trade_executor_factory import build_live_executor
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.logging.file_logger import FileLogger
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.testing.mock_adapter import MockBrokerAdapter
from python.framework.autotrader.tick_sources.kraken_tick_source import KrakenTickSource
from python.framework.autotrader.tick_sources.mock_tick_source import MockTickSource
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.decision_trading_api import DecisionTradingApi
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.market_types.market_config_types import TradingModel
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
            autotrader_trades.csv
            autotrader_orders.csv

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
    9. Warmup bar injection (mock: parquet, live: API)
    10. Create LiveClippingMonitor

    Args:
        config: AutoTrader configuration
        logger: ScenarioLogger instance

    Returns:
        (executor, bar_controller, worker_orchestrator, decision_logic, clipping_monitor)
    """
    # === Phase 1: Broker Config ===
    broker_config = _create_broker_config(config, logger)

    # === Phase 2: DecisionLogic Requirements ===
    decision_logic_factory = DecisionLogicFactory(logger=logger)
    decision_logic_class, _ = decision_logic_factory._resolve_logic_class(
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
    executor = build_live_executor(
        broker_config=broker_config,
        balances=config.account.balances,
        account_currency=account_currency,
        logger=logger,
        spot_mode=spot_mode,
    )
    logger.info(
        f"💱 LiveTradeExecutor created: balances={config.account.balances}"
    )

    # === Phase 5: TradingContext ===
    volume_min = broker_config.adapter.get_symbol_specification(
        config.symbol
    ).volume_min
    trading_context = TradingContext(
        broker_type=BrokerType(config.broker_type),
        market_type=market_type,
        symbol=config.symbol,
        volume_min=volume_min,
        trading_model=trading_model,
    )

    # === Phase 6: Workers ===
    worker_factory = WorkerFactory(logger=logger)
    workers_dict = worker_factory.create_workers_from_config(
        strategy_config=config.strategy_config,
        trading_context=trading_context
    )
    workers = list(workers_dict.values())
    logger.debug(f"✅ Created {len(workers)} workers")

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

    # === Phase 9: Warmup ===
    warmup_preparator = AutotraderWarmupPreparator(logger=logger)
    warmup_preparator.prepare_and_inject(
        config=config,
        workers=workers,
        bar_controller=bar_controller,
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
            max_ticks=config.tick_source.max_ticks,
        )
    elif config.tick_source.type == 'kraken':
        ws_pair = _resolve_ws_pair(config.symbol, config.broker_settings, logger)
        tick_source = KrakenTickSource(
            symbol=config.symbol,
            ws_pair=ws_pair,
            tick_queue=tick_queue,
            ws_url=config.tick_source.ws_url,
            reconnect_initial_delay_s=config.tick_source.reconnect_initial_delay_s,
            reconnect_max_delay_s=config.tick_source.reconnect_max_delay_s,
            heartbeat_interval_s=config.tick_source.heartbeat_interval_s,
            heartbeat_dead_s=config.tick_source.heartbeat_dead_s,
            logger=logger,
        )
    else:
        raise ValueError(
            f"Unknown tick source type: '{config.tick_source.type}'. "
            f"Supported: 'mock', 'kraken'."
        )

    tick_thread = threading.Thread(
        target=tick_source.start,
        name='AutoTrader-TickSource',
        daemon=True,
    )
    tick_thread.start()
    if config.tick_source.type == 'mock':
        logger.info(
            f"📡 Tick source started: {config.tick_source.type} "
            f"(mode={config.tick_source.mode})"
        )
    else:
        logger.info(
            f"📡 Tick source started: {config.tick_source.type} "
            f"({config.symbol})"
        )

    return tick_source, tick_thread


def _create_broker_config(config: AutoTraderConfig, logger: ScenarioLogger) -> BrokerConfig:
    """
    Load broker config and attach appropriate adapter.

    For adapter_type='mock': uses static JSON + MockBrokerAdapter.
    For adapter_type='live': fetches from Kraken API (fallback to static JSON).

    Args:
        config: AutoTrader configuration
        logger: ScenarioLogger for status messages

    Returns:
        BrokerConfig with adapter
    """
    if config.adapter_type == 'live':
        return _create_live_broker_config(config, logger)

    # Mock path: load static JSON, wrap in MockBrokerAdapter
    broker_config = BrokerConfigFactory.build_broker_config(
        config.broker_config_path
    )
    mock_adapter = MockBrokerAdapter(
        broker_config=broker_config.adapter.broker_config
    )
    return BrokerConfig(
        broker_type=broker_config.broker_type,
        adapter=mock_adapter
    )


def _create_live_broker_config(config: AutoTraderConfig, logger: ScenarioLogger) -> BrokerConfig:
    """
    Fetch broker config from Kraken API, with fallback to static JSON.

    Loads broker settings via cascade, fetches symbol specs and account balance,
    then enables live execution on the adapter.

    Args:
        config: AutoTrader configuration
        logger: ScenarioLogger for status messages

    Returns:
        BrokerConfig with live-enabled KrakenAdapter
    """
    # Lazy import to avoid loading requests in mock mode
    from python.configuration.autotrader.kraken_config_fetcher import KrakenConfigFetcher

    # === Load broker settings via cascade ===
    if not config.broker_settings:
        raise ValueError(
            "broker_settings required for adapter_type='live'. "
            "Add 'broker_settings' to profile JSON."
        )

    broker_settings = _load_broker_settings(config.broker_settings)
    credentials_file = broker_settings.get('credentials_file', 'kraken_credentials.json')
    api_base_url = broker_settings.get('api_base_url')
    dry_run = broker_settings.get('dry_run', True)

    logger.info(
        f"🔧 Broker settings loaded: {config.broker_settings} "
        f"(dry_run={dry_run})"
    )
    print(f"  ▸ Broker settings: {config.broker_settings} (dry_run={dry_run})")

    fetcher = KrakenConfigFetcher(
        credentials_path=credentials_file,
        logger=logger,
        api_base_url=api_base_url,
    )

    # === Fetch broker config (symbol specs) ===
    try:
        config_dict = fetcher.fetch_broker_config(
            symbol=config.symbol,
            broker_type=config.broker_type,
        )
        logger.info(f"💱 Live broker config fetched for {config.symbol}")
    except Exception as e:
        # Fallback to static JSON for symbol specs (rarely change)
        logger.warning(
            f"⚠️  API config fetch failed ({e}), "
            f"falling back to static: {config.broker_config_path}"
        )
        config_dict = BrokerConfigFactory.build_broker_config(
            config.broker_config_path
        ).adapter.broker_config

    # === Fetch account balances (no fallback — must succeed for live) ===
    # Fetch all currencies listed in profile balances from Kraken
    for currency in list(config.account.balances.keys()):
        balance = fetcher.fetch_account_balance(currency)
        if balance is None:
            raise ConnectionError(
                f"Could not fetch account balance for '{currency}' from Kraken API. "
                f"Live trading requires a confirmed balance. "
                f"Check API credentials and account permissions."
            )
        logger.info(
            f"💰 Live balance: {balance} {currency} "
            f"(profile default was {config.account.balances.get(currency, 0.0)})"
        )
        print(f"  ▸ Live balance: {balance} {currency}")
        config.account.balances[currency] = balance

    # Build BrokerConfig from fetched dict
    broker_config = BrokerConfigFactory.from_serialized_dict(
        broker_type=BrokerType(config.broker_type),
        config_dict=config_dict,
    )

    # === Enable live execution on adapter ===
    broker_config.adapter.enable_live(broker_settings)
    mode_label = 'DRY RUN (validate only)' if dry_run else 'LIVE TRADING'
    logger.info(f"🚀 Mode: {mode_label}")
    print(f"  ▸ Mode: {mode_label}")

    return broker_config


def _load_broker_settings(settings_filename: str) -> Dict[str, Any]:
    """
    Load broker settings via cascade: user_configs/broker_settings/ → configs/broker_settings/.

    Args:
        settings_filename: Broker settings filename (e.g., 'kraken_spot.json')

    Returns:
        Parsed broker settings dict
    """
    user_path = Path('user_configs/broker_settings') / settings_filename
    default_path = Path('configs/broker_settings') / settings_filename

    if user_path.exists():
        settings_path = user_path
    elif default_path.exists():
        settings_path = default_path
    else:
        raise FileNotFoundError(
            f"Broker settings file not found. Expected at:\n"
            f"  {user_path} (user override)\n"
            f"  {default_path} (default)"
        )

    with open(settings_path, 'r') as f:
        return json.load(f)


def _resolve_ws_pair(
    symbol: str,
    broker_settings_filename: str,
    logger: ScenarioLogger
) -> str:
    """
    Resolve internal symbol to Kraken WS pair format.

    Lookup chain: symbol_to_ws_pair in broker settings -> fallback slash-insert at position 3.

    Args:
        symbol: Internal symbol (e.g., 'BTCUSD')
        broker_settings_filename: Broker settings filename (e.g., 'kraken_spot.json')
        logger: ScenarioLogger for warnings

    Returns:
        Kraken WS pair (e.g., 'BTC/USD')
    """
    if not broker_settings_filename:
        raise ValueError(
            "broker_settings required for tick_source type='kraken'. "
            "Add 'broker_settings' to the AutoTrader profile JSON."
        )

    settings = _load_broker_settings(broker_settings_filename)
    ws_pair_map = settings.get('symbol_to_ws_pair', {})

    if symbol in ws_pair_map:
        return ws_pair_map[symbol]

    # Fallback: insert slash at position 3 (e.g., BTCUSD -> BTC/USD)
    if len(symbol) >= 4:
        ws_pair = f'{symbol[:3]}/{symbol[3:]}'
        logger.warning(
            f"📡 Symbol '{symbol}' not in symbol_to_ws_pair mapping, "
            f"using fallback: '{ws_pair}'. "
            f"Add to broker_settings for explicit control."
        )
        return ws_pair

    raise ValueError(
        f"Cannot resolve WS pair for symbol '{symbol}'. "
        f"Add it to 'symbol_to_ws_pair' in broker settings ({broker_settings_filename})."
    )
