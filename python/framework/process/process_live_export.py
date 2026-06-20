

from multiprocessing import Queue
import time
from typing import Dict, Optional, Tuple
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.trading_env.portfolio_manager import PortfolioManager
from python.framework.types.live_types.live_core_snapshot_types import LiveCoreSnapshot
from python.framework.types.live_types.live_scenario_stats_types import LiveScenarioStats
from python.framework.types.live_types.live_stats_config_types import ProcessLiveSetup, ScenarioStatus
from python.framework.types.market_types.market_data_types import Bar, TickData
from python.framework.types.process_data_types import ProcessScenarioConfig
from python.framework.utils.process_serialization_utils import serialize_current_bars
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


def process_live_setup(logger: ScenarioLogger,
                       config: ProcessScenarioConfig,
                       ticks: Tuple[TickData, ...],
                       live_queue: Optional[Queue] = None) -> ProcessLiveSetup:
    # === LIVE UPDATE SETUP ===
    live_enabled = (
        live_queue is not None
        and config.live_stats_config.enabled
    )
    if (live_queue is None and config.live_stats_config.enabled):
        logger.error(
            f"Live Queue (live_queue) is missing, but live view is enabled "
            f"(config.live_stats_config.enabled). This combination MUST not occur. "
            f"Something in the process chain is not forwarding live_queue.")
    tick_count = len(ticks)
    if live_enabled:
        return ProcessLiveSetup(
            live_queue=live_queue,
            last_update_time=time.perf_counter(),
            update_interval_ms=config.live_stats_config.update_interval_ms,
            first_tick=ticks[0],
            live_enabled=live_enabled,
            tick_count=tick_count
        )

    return ProcessLiveSetup(
        # No monitoring - zero overhead path
        live_queue=live_queue,
        live_enabled=live_enabled,
        tick_count=tick_count
    )


def process_live_export(live_setup: ProcessLiveSetup,
                        config: ProcessScenarioConfig,
                        tick_idx: int,
                        tick: TickData,
                        portfolio: PortfolioManager,
                        worker_coordinator: WorkerOrchestrator,
                        current_bars: Dict[str, Bar]
                        ) -> bool:
    first_tick = live_setup.first_tick
    tick_count = live_setup.tick_count
    live_queue = live_setup.live_queue
    if not live_setup.live_enabled:
        return False

    current_time = time.perf_counter()
    is_last_tick = (tick_idx == live_setup.tick_count - 1)

    # Check time since last update (from object!)
    time_since_last = current_time - live_setup.last_update_time

    if time_since_last >= (live_setup.update_interval_ms / 1000.0) or is_last_tick:
        # === BUILD CORE FRAME ===
        # Basic portfolio (direct access — safe after get_portfolio_statistics!).
        # AwarenessChannel is ephemeral (~0 cost).
        core = LiveCoreSnapshot(
            symbol=config.symbol,
            ticks_processed=tick_idx + 1,
            balance=portfolio.balance,
            initial_balance=portfolio.initial_balance,
            total_trades=portfolio.get_total_trades(),
            winning_trades=portfolio.get_winning_trades(),
            losing_trades=portfolio.get_losing_trades(),
            last_awareness=worker_coordinator.decision_logic.get_last_awareness(),
        )
        frame = LiveScenarioStats(
            core=core,
            scenario_name=config.name,
            scenario_index=config.scenario_index,
            total_ticks=tick_count,
            progress_percent=((tick_idx + 1) / tick_count) * 100,
            status=ScenarioStatus.RUNNING,
            first_tick_time=first_tick.timestamp.isoformat(),
            current_tick_time=tick.timestamp.isoformat(),
            tick_timespan_seconds=(
                tick.timestamp - first_tick.timestamp).total_seconds(),
            portfolio_dirty_flag=portfolio._positions_dirty,
        )

        # === CONDITIONAL EXPORTS ===

        # 1. Portfolio Stats (expensive!)
        if config.live_stats_config.export_portfolio_stats:
            portfolio_stats_obj = portfolio.get_portfolio_statistics()
            frame.portfolio_stats = portfolio_stats_obj  # full
            # after refreshing we override the current values on the core.
            # portfolio_dirty_flag will be false now.
            core.balance = portfolio_stats_obj.current_balance
            core.total_trades = portfolio_stats_obj.total_trades
            core.winning_trades = portfolio_stats_obj.winning_trades
            core.losing_trades = portfolio_stats_obj.losing_trades
            frame.portfolio_dirty_flag = portfolio._positions_dirty

        # 2. Current Bars
        if config.live_stats_config.export_current_bars:
            frame.current_bars = serialize_current_bars(current_bars)

        # Send to queue (non-blocking!)
        try:
            live_queue.put_nowait(frame)
            live_setup.last_update_time = current_time
        except:
            pass  # Queue full - skip update

        return True

    return False
