

from dataclasses import asdict
from multiprocessing import Queue
import time
from typing import Dict, Optional, Tuple
from python.components.logger.scenario_logger import ScenarioLogger
from python.framework.trading_env.portfolio_manager import PortfolioManager
from python.framework.types.live_stats_config_types import ProcessLiveSetup
from python.framework.types.market_data_types import Bar, TickData
from python.framework.types.process_data_types import ProcessScenarioConfig
from python.framework.utils.process_serialization_utils import serialize_current_bars
from python.framework.workers.worker_coordinator import WorkerCoordinator


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
            update_interval_sec=config.live_stats_config.update_interval_sec,
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
                        worker_coordinator: WorkerCoordinator,
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

    if time_since_last >= live_setup.update_interval_sec or is_last_tick:
        # === BUILD CORE MESSAGE ===
        live_data = {
            "type": "progress",
            "scenario_index": config.scenario_index,
            "scenario_name": config.name,
            "symbol": config.symbol,

            # Progress
            "ticks_processed": tick_idx + 1,
            "total_ticks": tick_count,
            "progress_percent": ((tick_idx + 1) / tick_count) * 100,
            "status": "running",

            # In-Time tracking
            "first_tick_time": first_tick.timestamp.isoformat(),
            "current_tick_time": tick.timestamp.isoformat(),
            "tick_timespan_seconds": (
                tick.timestamp - first_tick.timestamp
            ).total_seconds(),

            # Basic Portfolio (direct access - safe after get_portfolio_statistics!)
            "initial_balance": portfolio.initial_balance,
            # dirty states
            "current_balance": portfolio.balance,
            "total_trades": len(portfolio.closed_positions),
            "winning_trades": portfolio._winning_trades,
            "losing_trades": portfolio._losing_trades,
            "portfolio_dirty_flag": portfolio._positions_dirty,
        }

        # === CONDITIONAL EXPORTS ===

        # 1. Portfolio Stats (expensive!)
        if config.live_stats_config.export_portfolio_stats:
            portfolio_stats_obj = portfolio.get_portfolio_statistics()
            live_data["portfolio_stats"] = asdict(portfolio_stats_obj)  # full
            # after refreshing we need to override the current values.
            # portfolio_dirty_flag will be false now.
            # overrides:
            live_data["current_balance"] = portfolio_stats_obj.current_balance
            live_data["total_trades"] = portfolio_stats_obj.total_trades
            live_data["winning_trades"] = portfolio_stats_obj.winning_trades
            live_data["losing_trades"] = portfolio_stats_obj.losing_trades
            live_data["portfolio_dirty_flag"] = portfolio._positions_dirty

        # 2. Performance Stats
        if config.live_stats_config.export_performance_stats:
            live_data["performance_stats"] = (
                worker_coordinator.performance_log_coordinator.get_snapshot()
            )

        # 3. Current Bars
        if config.live_stats_config.export_current_bars:
            live_data["current_bars"] = serialize_current_bars(
                current_bars)

        # Send to queue (non-blocking!)
        try:
            live_queue.put_nowait(live_data)
            live_setup.last_update_time = current_time
        except:
            pass  # Queue full - skip update

        return True

    return False
