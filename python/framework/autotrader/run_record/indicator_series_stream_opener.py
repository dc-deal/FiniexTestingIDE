"""
FiniexTestingIDE - Indicator Series Stream Opener (#379)

Opens a session's indicator series streams — one per drawing worker — when its profile records
them. The recorder itself is the backtest's too; only which member the streams are and where the
switch is read differ.
"""

from typing import Optional

from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.process.indicator_series_recorder import (
    IndicatorSeriesRecorder,
    open_indicator_series,
)
from python.framework.store.entry_members.run_entry_writer import RunEntryWriter
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.store_member_types import RunEntryMemberId
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


def open_indicator_series_streams(
    config: AutoTraderConfig,
    run_writer: RunEntryWriter,
    run_id: str,
    worker_orchestrator: WorkerOrchestrator,
    bar_controller: BarRenderingController,
    logger: AbstractLogger,
) -> Optional[IndicatorSeriesRecorder]:
    """
    Open the session's indicator series streams, when the profile records them.

    Args:
        config: The resolved profile — its `indicator_series` block, its unit and its symbol
        run_writer: The session's run writer, whose stream sealer the streams join (#476)
        run_id: The run the streams belong to
        worker_orchestrator: The session's workers
        bar_controller: The session's bars
        logger: The session's channel

    Returns:
        The recorder, or None when the recording is switched off or no worker draws
    """
    if not config.indicator_series.enabled:
        logger.info('📈 Indicator series: not recorded — switched off in the profile')
        return None
    return open_indicator_series(
        run_writer, RunEntryMemberId.INDICATOR_SERIES, run_id, config.get_unit_name(),
        worker_orchestrator, bar_controller, config.symbol, logger)
