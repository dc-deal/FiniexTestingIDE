"""
FiniexTestingIDE - State Frame Stream Opener (#579 part A)

Opens an AutoTrader session's state-frame stream when its profile records one, and registers it in
the run index while the session runs. The recorder itself is shared by both pipelines
(`process/state_frame_recorder.py`); what is the session's alone — its profile, its run index entry,
its reconciler — stays here.
"""

from typing import Optional

from python.configuration.app_config_manager import AppConfigManager
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.process.state_frame_recorder import StateFrameRecorder
from python.framework.reporting.store.run_index import RunIndex
from python.framework.store.entry_members.run_entry_writer import RunEntryWriter
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.trading_env.live.reconciler import Reconciler
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.config_types.market_config_types import TradingModel
from python.framework.types.store_member_types import RunEntryMemberId
from python.framework.utils.timeframe_config_utils import TimeframeConfig
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


def open_state_frame_stream(
    config: AutoTraderConfig,
    run_writer: RunEntryWriter,
    run_id: str,
    executor: AbstractTradeExecutor,
    worker_orchestrator: WorkerOrchestrator,
    bar_controller: BarRenderingController,
    trading_model: TradingModel,
    reconciler: Optional[Reconciler],
    logger: AbstractLogger,
) -> Optional[StateFrameRecorder]:
    """
    Open the session's state-frame stream, when the profile records one.

    The run index learns of the stream in the same step, so the run list names it while the session
    runs — the check-in reads it from there (#611), long before any report exists.

    Args:
        config: The resolved profile — its `state_frames` block, its unit and its tick source
        run_writer: The session's run writer, whose stream sealer the stream joins (#476)
        run_id: The run the stream belongs to
        executor: The session's executor
        worker_orchestrator: The session's workers
        bar_controller: The session's bars
        trading_model: SPOT or MARGIN
        reconciler: The reconciler, or None when none is wired
        logger: The session's channel

    Returns:
        The recorder, or None when the recording is switched off
    """
    settings = config.state_frames
    if not settings.enabled:
        logger.info('📼 State frames: not recorded — switched off in the profile')
        return None
    appender = run_writer.open_stream(
        RunEntryMemberId.STATE_FRAMES, run_id, logger,
        stops='The session\'s state is no longer recorded',
        header_fields={'unit_name': config.get_unit_name(),
                       'cadence_timeframe': settings.cadence_timeframe,
                       'cadence_s': TimeframeConfig.get_minutes(settings.cadence_timeframe) * 60.0})
    logger.info(
        f'📼 State frames: one per {settings.cadence_timeframe} period of the canonical clock, at '
        f'a closed bar and at an order → {appender.get_path()}')
    RunIndex(AppConfigManager().get_file_logging_config_object().run_index) \
        .record_streams(run_id, run_writer.get_run_dir())
    return StateFrameRecorder(
        appender, settings.cadence_timeframe, config.symbol, executor, worker_orchestrator,
        bar_controller, trading_model,
        replayed=config.tick_source.type == 'mock', logger=logger,
        reconcile_state=reconciler.get_last_verdict if reconciler is not None else None)
