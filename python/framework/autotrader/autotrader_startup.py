"""
FiniexTestingIDE - AutoTrader Startup Preparation
Pipeline object creation for live AutoTrader sessions.

Mirrors process_startup_preparation.py for backtesting.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.market_config_manager import MarketConfigManager
from python.configuration.sentiment_config_manager import SentimentConfigManager
from python.framework.autotrader.autotrader_broker_config_setup import create_broker_config
from python.framework.autotrader.autotrader_warmup_preparator import AutotraderWarmupPreparator
from python.framework.autotrader.live_clipping_monitor import LiveClippingMonitor
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.factory.decision_logic_factory import DecisionLogicFactory
from python.framework.factory.live_trade_executor_factory import build_live_executor
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.logging.file_logger import FileLogger
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.process.process_startup_preparation import inject_signal_providers
from python.framework.reporting.store.run_index import RunIndex
from python.framework.signal_data.signal_data_provider import SignalDataProvider
from python.framework.signal_data.signal_source_resolver import SignalSourceResolver
from python.framework.signal_data.transport.signal_boot_resolver import prepare_live_signal_boot
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.trading_env.decision_trading_api import DecisionTradingApi
from python.framework.types.api.report_types import RunHeader
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.display_label_cache import DisplayLabelCache
from python.framework.types.config_types.market_config_types import TradingModel
from python.framework.types.log_layout_types import RUN_TYPE_LIVE
from python.framework.types.market_types.market_types import TradingContext
from python.framework.types.process_data_types import ProcessDataPackage
from python.framework.types.signal_data_types import (
    SignalSourceMode,
)
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.utils.git_info_utils import get_git_commit
from python.framework.utils.run_id_utils import mint_run_id, session_key_from_run_id
from python.framework.validators.component_metadata_advisory import (
    surface_decision_logic_version,
)
from python.framework.validators.decision_logic_hook_validator import check_cold_start_hook
from python.framework.workers.abstract_signal_worker import AbstractSignalWorker
from python.framework.workers.worker_orchestrator import WorkerOrchestrator

# How far back the archive is read when the producer could not be asked for its own
# replay window at boot (#473, degraded start). Wide enough that a session restarted
# after a night still mounts something; the staleness contract judges whether it is
# usable, which is not this number's job.
_DEGRADED_REPLAY_WINDOW_HOURS: float = 24.0


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
        runs/autotrader/<name>/<run_id>/
            autotrader_global.log
            autotrader_summary.log
            session_logs/
                autotrader_session_YYYYMMDD.log
            events.csv

    Args:
        config: AutoTrader configuration
        run_timestamp: Session start timestamp (UTC)

    Returns:
        (global_logger, session_logger, summary_logger, run_dir, run_id)
    """
    session_name = config.name or f'{config.symbol}_{config.adapter_type}'
    # From config (file_logging.run_logs.live) — the same source the API reads,
    # so a moved log root cannot make a session invisible to the run index.
    log_root = AppConfigManager().get_file_logging_config_object().run_logs.live

    # Minted ONCE for all three loggers. Deriving it per logger would give three ids and
    # therefore three directories for one session — the trap this threading exists to avoid.
    run_id = mint_run_id(run_timestamp, log_root / session_name)

    # Global logger — startup/shutdown/errors
    global_logger = ScenarioLogger(
        scenario_set_name=session_name,
        scenario_name='global',
        run_timestamp=run_timestamp,
        run_id=run_id,
        log_root_override=log_root,
        file_name_prefix_override='autotrader'
    )

    run_dir = global_logger.get_log_dir()

    # Summary logger — post-session report (shares run_dir with global)
    summary_logger = ScenarioLogger(
        scenario_set_name=session_name,
        scenario_name='summary',
        run_timestamp=run_timestamp,
        run_id=run_id,
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
        run_id=run_id,
        log_root_override=log_root,
        file_name_prefix_override='autotrader',
        # The tick-by-tick record of the session — every line carries the run's own time.
        # global and summary do not: they describe the session from outside a moment in it.
        event_time_column=True
    )

    # The run header goes down FIRST, before the session can fail — a crashed session is
    # exactly the one somebody needs to identify afterwards.
    # Only the COMMIT is needed here — `get_git_commit()` costs 68 ms where the full read
    # costs ~2.0 s, and the header has no use for branch / dirty (§42).
    if run_dir:
        header = RunHeader(
            run_id=run_id,
            start_time=run_timestamp,
            run_type=RUN_TYPE_LIVE,
            run_name=session_name,
            parent_id=None,
            config_snapshot='autotrader_config.json',
            app_version=AppConfigManager().get_version(),
            git_commit=get_git_commit(),
        )
        RunIndex(AppConfigManager().get_file_logging_config_object().run_index).register_run(
            header, run_dir)

    # Create session_logs/ subdir (tick loop will create files there)
    if run_dir:
        session_logs_dir = run_dir / 'session_logs'
        session_logs_dir.mkdir(parents=True, exist_ok=True)

    return global_logger, session_logger, summary_logger, run_dir, run_id


def create_session_file_logger(run_dir: Path, date_suffix: str) -> FileLogger:
    """
    Create a new FileLogger for a specific day's session log.

    Used for daily rotation: when the tick date changes, the tick loop
    calls this to get a fresh FileLogger for the new day.

    Args:
        run_dir: Session run directory (contains session_logs/ subdir)
        date_suffix: Date string for filename (YYYYMMDD)

    Returns:
        FileLogger writing to session_logs/autotrader_session_YYYYMMDD.log
    """
    session_logs_dir = run_dir / 'session_logs'
    session_logs_dir.mkdir(parents=True, exist_ok=True)
    return FileLogger(
        log_filename=f'autotrader_session_{date_suffix}.log',
        file_path=session_logs_dir,
        # The threshold the session logger actually gates on, read from config (§28) — not
        # carried over from the file logger being replaced, which never enforced it anyway.
        log_level=AppConfigManager().get_file_logging_config_object().scenario_log_level
    )



def setup_pipeline(
    config: AutoTraderConfig,
    logger: ScenarioLogger,
    run_id: str,
    package: Optional[ProcessDataPackage] = None
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
        run_id: This session's run id — its random half becomes the client-order-id
            discriminator every order carries to the venue (#473)
        package: Prepared scenario data package (#438, mock) — its signal series is injected
            into SIGNAL workers; None for live

    Returns:
        (executor, bar_controller, worker_orchestrator, decision_logic, clipping_monitor, trading_model, display_label_cache)
    """
    # === Phase 1: Broker Config ===
    # Balances source (#438): the mock replays a scenario → its scenario_settings.balances
    # (starting capital); live is filled from the broker inside create_broker_config (the
    # profile declares no balances).
    if config.scenario_settings is not None:
        balances = dict(config.scenario_settings.balances)
        explicit_account_currency = config.scenario_settings.account_currency
    else:
        balances = {}
        explicit_account_currency = None
    broker_config = create_broker_config(config, logger, balances)

    # === Phase 2: DecisionLogic Requirements ===
    decision_logic_factory = DecisionLogicFactory(logger=logger)
    decision_logic_class, _ = decision_logic_factory.resolve_logic_class(
        config.strategy_config.get('decision_logic_type', '')
    )
    required_order_types = decision_logic_class.get_required_order_types(
        config.strategy_config.get('decision_logic_config', {})
    )
    logger.debug(
        f'📋 Decision logic requires: {[t.value for t in required_order_types]}'
    )
    # #493 — a logic that can leave an order resting at a venue must answer for finding one
    # there after a restart. Checked on the DECLARATION, before anything is built.
    cold_start_hook_error = check_cold_start_hook(decision_logic_class, required_order_types)
    if cold_start_hook_error:
        raise ValueError(cold_start_hook_error)

    # === Phase 3: Resolve trading model ===
    market_config_manager = MarketConfigManager()
    market_type = market_config_manager.get_market_type(config.broker_type)
    trading_model = market_config_manager.get_trading_model(config.broker_type)
    spot_mode = trading_model == TradingModel.SPOT

    # Validate: balances must be resolved
    if not balances:
        raise ValueError(
            f"Configuration error: AutoTrader profile '{config.name}' resolved no balances.\n"
            f"Mock: set 'scenario_settings.balances' (e.g. {{ \"USD\": 10000.0 }}).\n"
            f"Live: the broker returned no balance for the symbol's currencies."
        )

    # Determine account_currency: explicit override or derive from balances + symbol
    symbol_spec = broker_config.adapter.get_symbol_specification(config.symbol)
    if explicit_account_currency:
        account_currency = explicit_account_currency
    elif symbol_spec.quote_currency in balances:
        account_currency = symbol_spec.quote_currency
    elif symbol_spec.base_currency in balances:
        account_currency = symbol_spec.base_currency
    else:
        account_currency = list(balances.keys())[0]

    # === Phase 4: LiveTradeExecutor ===
    broker_entry = market_config_manager.get_broker_entry(config.broker_type)
    connection_policy = broker_entry.broker_transport.connection
    executor = build_live_executor(
        broker_config=broker_config,
        balances=balances,
        account_currency=account_currency,
        logger=logger,
        spot_mode=spot_mode,
        poll_interval_ms=broker_entry.broker_transport.poll_interval_ms,
        connection_policy=connection_policy,
        # #473 — four characters of the run id's random half. The SESSION owns it: a #476
        # day fragment mints its own run id and must not change the key mid-session, which
        # is why it is derived here and never re-derived downstream.
        session_key=session_key_from_run_id(run_id),
    )
    # The session log's event-time column pulls from the canonical clock. Attachable only
    # HERE: the logger goes INTO build_live_executor above, so it necessarily exists first.
    logger.attach_clock(executor.get_current_time_if_set)
    logger.info(
        f'💱 LiveTradeExecutor created: balances={balances}'
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
    logger.debug(f'✅ Created {len(workers)} workers')

    # === Phase 6b: Signal Providers (#431/#438, live transport #141 Part 2a) ===
    # A SIGNAL worker resolves against exactly one collaborator and never learns where its
    # snapshots came from. Two ways to give it one:
    #   mounted  — the mock's prepared series, injected exactly as the sim subprocess does;
    #   live     — an EMPTY provider that the signal transport fills as envelopes arrive.
    # The empty case is not a degenerate mount: a live session legitimately starts knowing
    # nothing, and its first decision waits for the first arrival (the worker reports BLIND
    # until then, which the staleness contract already handles).
    # The mode is resolved ONCE here and carried on the orchestrator, because every later
    # site that acts on it (the transport setup, the inbox drain) used to derive it again
    # and disagreed with this one.
    signal_source = SignalSourceResolver.resolve(
        workers=workers,
        package=package,
        sentiment_config=SentimentConfigManager().get_config())

    live_boot = None
    if signal_source.mode is SignalSourceMode.MOUNTED:
        inject_signal_providers(workers, package, logger)
    elif signal_source.mode is SignalSourceMode.LIVE:
        live_boot = prepare_live_signal_boot(config, signal_source, logger)
        # ONE provider per source, shared by every worker reading it. Not one each: the
        # arrival merge extends one provider per signal kind, so per-worker providers would
        # leave every worker after the first permanently blind.
        provider = SignalDataProvider(live_boot.mount.series)
        for worker in [w for w in workers if isinstance(w, AbstractSignalWorker)]:
            worker.set_signal_provider(provider)
    logger.info(f'📡 {signal_source.reason}')

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

    # Provenance for this session log (#118 Stage 0) — logged where the logic is built, the
    # same place the sim does it (process_startup_preparation). The market-fit VERDICT stays
    # in AutotraderMain: it is a finding, not a log line, and it needs the session's
    # validation channel.
    surface_decision_logic_version(decision_logic, logger)

    # === Phase 8: WorkerOrchestrator + DecisionTradingApi ===
    worker_orchestrator = WorkerOrchestrator(
        decision_logic=decision_logic,
        strategy_config=config.strategy_config,
        workers=workers,
        parallel_workers=config.execution.parallel_workers,
        worker_decision_tracking=config.execution.performance_tracking.worker_decision_tracking,
        signal_source=signal_source,
        signal_boot=live_boot,
    )
    worker_orchestrator.initialize()
    logger.debug(f'✅ Orchestrator initialized: {len(workers)} workers')

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
        connection_policy=connection_policy,
    )
    display_label_cache = warmup_preparator.build_display_label_cache(
        decision_logic=decision_logic,
        workers=workers,
        sentiment_source=(
            config.scenario_settings.data_sentiment_type
            if config.scenario_settings else ''
        ),
    )

    # === Phase 10: LiveClippingMonitor ===
    clipping_monitor = LiveClippingMonitor(
        report_interval_s=config.clipping_monitor.report_interval_s,
        strategy=config.clipping_monitor.strategy,
    )
    logger.debug(
        f'✅ ClippingMonitor: strategy={config.clipping_monitor.strategy}, '
        f'report_interval={config.clipping_monitor.report_interval_s}s'
    )

    return executor, bar_controller, worker_orchestrator, decision_logic, clipping_monitor, trading_model, display_label_cache
