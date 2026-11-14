"""
FiniexTestingIDE - Process Executor
Process-based scenario execution with live update support
"""
# ============================================================================
# BARRIER SYNCHRONIZATION IMPORT
# ============================================================================
# CRITICAL: Import from threading, NOT multiprocessing or asyncio!
#
# Why threading?
# - Manager.Barrier() is a proxy to a threading.Barrier object
# - The Manager runs a server process that uses threading internally
# - Therefore, it raises threading.BrokenBarrierError, not multiprocessing
#
# Common mistakes:
# ❌ from asyncio import BrokenBarrierError        # Wrong module
# ✅ from threading import BrokenBarrierError       # Correct!
# ============================================================================
import time
from collections import defaultdict
from multiprocessing import Queue
from typing import List, Optional, Tuple

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.constants.currency_constants import format_currency_signed
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.loop.event_sequencer import EventSequencer
from python.framework.loop.loop_context import LoopContext
from python.framework.loop.loop_event_dispatcher import (
    dispatch_backtest_heartbeat,
    dispatch_backtest_slice,
)
from python.framework.loop.tick_slicer import slice_of_tick
from python.framework.process.indicator_series_recorder import IndicatorSeriesRecorder
from python.framework.process.market_data_episode_tracker import MarketDataEpisodeTracker
from python.framework.process.process_live_export import process_live_export, process_live_setup
from python.framework.process.sim_pass_hooks import SimPassHooks
from python.framework.process.state_frame_recorder import StateFrameRecorder
from python.framework.reporting.booking_period_recorder import (
    BookingPeriodRecorder,
    snapshot_from_executor,
)
from python.framework.stress_test.stale_data_stress_driver import (
    StaleDataStressDriver,
    build_stale_stress_driver,
)
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.trading_env.decision_event_dispatcher import DecisionEventDispatcher
from python.framework.types.decision_logic_types import Decision
from python.framework.types.loop_types.loop_pass_record_types import LoopPassRecord
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.portfolio_types.portfolio_aggregation_types import PortfolioStats
from python.framework.types.process_data_types import (
    ProcessProfileData,
    ProcessScenarioConfig,
    ProcessTickLoopResult,
)
from python.framework.types.trading_env_types.order_event_types import OrderEvent
from python.framework.utils.process_debug_info_utils import (
    get_tick_range_stats,
    processed_tick_range_stats,
)
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


def _run_sim_heartbeats(
    prev_msc: int,
    current_msc: int,
    config: ProcessScenarioConfig,
    trade_simulator: AbstractTradeExecutor,
    worker_coordinator: WorkerOrchestrator,
    decision_logic: AbstractDecisionLogic,
    decision_event_dispatcher: Optional[DecisionEventDispatcher],
    booking: BookingPeriodRecorder,
    seal_source,
    state_frames: Optional[StateFrameRecorder] = None,
    ticks_processed: int = 0,
    last_decision: Optional[Decision] = None,
) -> bool:
    """
    Drive decision ghost-passes in the simulated-time gap between two data ticks (#360).

    Hard-gated by the caller on decision_logic.wants_heartbeat(). Fires a ghost-pass
    every config.heartbeat_interval_ms of simulated time strictly inside
    (prev_msc, current_msc), resolving broker fills at each ghost moment via
    TradeSimulator.heartbeat() so an opt-in algo reacts between ticks at the same
    relative point as live. No bar render, no tick counter — tick state untouched.

    Correctness gate (#208): no ghost-passes across a gap longer than
    inter_tick_gap_threshold_s — across a data/weekend gap the market says nothing,
    so synthesizing heartbeats there would fabricate activity.

    Args:
        prev_msc: Previous data tick timestamp (ms)
        current_msc: Current data tick timestamp (ms)
        config: Scenario config (heartbeat_interval_ms, inter_tick_gap_threshold_s)
        trade_simulator: Executor (clock injection + ghost broker resolution)
        worker_coordinator: Orchestrator (process_heartbeat → cached worker results)
        decision_logic: Opt-in decision (executes the ghost action)
        decision_event_dispatcher: #348 channel (drained around the ghost compute)
        booking: The unit's period recorder — the trading-day boundary is checked at EVERY
            ghost instant, because these passes are the only thing that advances the canonical
            clock across a quiet stretch and a fill can resolve at any of them (#537)
        seal_source: Callable handing the recorder its records + snapshot when a seal happens
        state_frames: The scenario's state-frame recorder, or None when it records none
        ticks_processed: The scenario's tick counter, for the frames
        last_decision: The last tick's decision — what a heartbeat frame records, as live

    Returns:
        True if the algo requested session end during a ghost-pass (caller stops)
    """
    interval_ms = config.heartbeat_interval_ms
    gap_ms = current_msc - prev_msc
    if gap_ms <= interval_ms or gap_ms > config.inter_tick_gap_threshold_s * 1000.0:
        return False

    k = 1
    while prev_msc + k * interval_ms < current_msc:
        ghost_order = dispatch_backtest_heartbeat(
            prev_msc + k * interval_ms, trade_simulator, worker_coordinator, decision_logic,
            decision_event_dispatcher, booking, seal_source)
        # #579 part B item 3: a heartbeat fills the slot it reaches, with the last tick's decision
        # and no closed bar — as a live session's heartbeat does
        if state_frames is not None:
            state_frames.on_pass(ticks_processed, last_decision, on_tick=False, order=ghost_order)
        if trade_simulator.is_session_end_requested():
            return True
        k += 1
    return False


def execute_tick_loop(
    config: ProcessScenarioConfig,
    worker_coordinator: WorkerOrchestrator,
    trade_simulator: AbstractTradeExecutor,
    bar_rendering_controller: BarRenderingController,
    decision_logic: AbstractDecisionLogic,
    scenario_logger: ScenarioLogger,
    ticks: Tuple[TickData, ...],
    live_queue: Optional[Queue] = None,
    decision_event_dispatcher: Optional[DecisionEventDispatcher] = None,
    state_frames: Optional[StateFrameRecorder] = None,
    indicator_series: Optional[IndicatorSeriesRecorder] = None,
) -> ProcessTickLoopResult:
    """
    Execute tick processing loop with live update support.

    MAIN PROCESSING: Iterate through ticks, process each.
    LIVE UPDATES: Send periodic updates via queue (time-based).

    Args:
        config: Scenario configuration
        worker_coordinator: Orchestrator for worker execution
        trade_simulator: Trade execution engine
        bar_rendering_controller: Bar rendering controller
        decision_logic: Decision logic instance
        scenario_logger: Logger for this scenario
        ticks: Deserialized tick data
        live_queue: Queue for live updates (optional)
        decision_event_dispatcher: The decision event channel (#348), or None
        state_frames: The scenario's state-frame recorder (#579 part B item 3), or None when it
            records none
        indicator_series: The scenario's indicator series recorder (#379), or None when it
            records none

    Returns:
        ProcessTickLoopResult with loop results
    """
    # #451: the tick-domain observer — episode spans + fresh/stale tick counters.
    # Always active: without stress the status stays fresh, so it simply counts.
    # Built before the loop so the result collection can read it after any error.
    market_data_tracker = MarketDataEpisodeTracker(
        source=config.broker_type.value if config.broker_type else '',
        logger=scenario_logger)

    # #362 — the scenario's order-event stream, collected here and carried back with the
    # result: a scenario subprocess writes no run artifact itself. Registered before the first
    # order, and it holds what the order history may drop — that list is capped, this is not.
    order_events: List[OrderEvent] = []
    trade_simulator.add_order_event_listener(order_events.append)

    try:
        portfolio = trade_simulator.portfolio

        # Performance profiling (Layer B — #137).
        # When disabled, profile dicts stay empty and inter-tick collection skipped.
        profiling_enabled = config.tick_loop_profiling
        profile_times = defaultdict(float) if profiling_enabled else {}
        profile_counts = defaultdict(int) if profiling_enabled else {}

        # Inter-tick interval collection (collected_msc preferred, time_msc fallback)
        inter_tick_intervals: List[float] = []
        prev_interval_msc: int = 0

        tick_range_stats = get_tick_range_stats(scenario_logger, trade_simulator, ticks)
        # What the loop core's passes leave for this drive (#458) — among it how many ticks
        # reached the broker path, measured again after the loop, so a session that ended early
        # reports the market time it processed rather than the time it loaded.
        pass_record = LoopPassRecord()
        # The last tick's decision — what a heartbeat frame and the final frame record
        last_decision: Optional[Decision] = None

        live_setup = process_live_setup(
            scenario_logger, config, ticks, live_queue)
        live_update_count = 0

        tick_loop_error: Exception = None
        current_tick = None

        # === BOOKING PERIODS (#537) — this scenario's ledger entries ===
        # Per SCENARIO and not per run: a run's scenarios cover different windows (measured:
        # 40 scenarios, 40 distinct ones), so "day 1 of the run" is not a thing and only
        # "day 1 of this unit" is. The recorder is the same one the AutoTrader loop uses — that loop
        # is a class and this one is a function, and a recorder each would be two
        # implementations of one rule (§19).
        #
        # No floor is carried in: a backtest has no predecessor to inherit a period count from,
        # so every scenario counts from 1. Resolved inside the try, so a market that declares no
        # anchor fails THIS scenario rather than the batch (§33).
        market_config = MarketConfigManager()
        booking = BookingPeriodRecorder(
            unit_name=config.name,
            # None when the scenario declares no broker: it has no market, so it has no
            # trading day, and it books nothing. A real scenario always declares one — this is
            # the internal harness that builds a config without it.
            anchor=(market_config.get_trading_day_anchor(config.broker_type.value)
                    if config.broker_type else None),
            log=scenario_logger.info,
            # Which days are trading days, asked the way an AutoTrader session asks it — so a
            # backtest and a session over the same days book the same rows (#476)
            weekend_closure=(market_config.has_weekend_closure(config.broker_type.value)
                             if config.broker_type else False),
        )

        def seal_source():
            """The records and the stock reading a seal needs, at this instant."""
            return snapshot_from_executor(trade_simulator)

        current_index = 0

        # Count algo ticks (non-clipped) for log messages
        algo_tick_count = sum(1 for t in ticks if not t.is_clipped)
        has_clipping = algo_tick_count < len(ticks)

        # #436: planned stale windows on the TICK source (stale_data_stress) —
        # status-plane driver on the sim time axis (events hitting the signal
        # source are already carved out of the series at preparation time).
        # The overlap guard warns about windows the data range can never reach.
        # The broker type is read the same way the booking anchor above reads it: a scenario
        # without one has no tick source to name, so no event can match it.
        stale_stress_driver: Optional[StaleDataStressDriver] = build_stale_stress_driver(
            config.stress_test_config,
            config.broker_type.value if config.broker_type else '',
            (ticks[0].timestamp, ticks[-1].timestamp) if ticks else None,
            trade_simulator, decision_logic, scenario_logger)

        # #458: the scenario's collaborators for the loop core's passes, and the runner's own
        # steps inside them
        loop_context = LoopContext(
            executor=trade_simulator, bar_controller=bar_rendering_controller,
            orchestrator=worker_coordinator, decision_logic=decision_logic,
            decision_event_dispatcher=decision_event_dispatcher, symbol=config.symbol,
            booking=booking, seal_source=seal_source,
            reads_closed_bars=state_frames is not None or indicator_series is not None,
            profiling_enabled=profiling_enabled, profile_times=profile_times,
            profile_counts=profile_counts)
        pass_hooks = SimPassHooks(
            trade_simulator, portfolio, booking, stale_stress_driver, market_data_tracker,
            profiling_enabled, profile_times, profile_counts)
        sequencer = EventSequencer()

        if has_clipping:
            scenario_logger.info(
                f'🔄 Starting tick loop ({live_setup.tick_count:,} ticks, '
                f'{algo_tick_count:,} algo)')
        else:
            scenario_logger.info(
                f'🔄 Starting tick loop ({live_setup.tick_count:,} ticks)')

        # === TICK LOOP ===
        # The log's event-time column needs no per-tick feeding: the logger pulls the canonical
        # clock, which on_tick and the ghost pass both advance.
        for tick_idx, tick in enumerate(ticks):
            if profiling_enabled: tick_start = time.perf_counter()
            current_tick = tick
            current_index = tick_idx

            # Inter-tick interval: use collected_msc (monotonic) when available,
            # fall back to time_msc for pre-V1.3.0 data (with negative-diff skip)
            current_msc = tick.collected_msc if tick.collected_msc > 0 else tick.time_msc
            if profiling_enabled and prev_interval_msc > 0 and current_msc > 0:
                delta = current_msc - prev_interval_msc
                if tick.collected_msc > 0 or delta >= 0:
                    inter_tick_intervals.append(float(delta))

            # #360: drive decision ghost-passes across the simulated gap to the
            # previous tick — opt-in algos only (hard-gated, absent otherwise).
            # prev_interval_msc still holds the previous tick here (updated below).
            if (decision_logic.wants_heartbeat()
                    and config.heartbeat_interval_ms > 0
                    and prev_interval_msc > 0 and current_msc > 0):
                # #537: the boundary is checked on the heartbeat too — the ghost passes resolve
                # fills between two ticks, and one resolved after the boundary belongs to the
                # day it opened. The check therefore lives INSIDE the ghost loop: called here it
                # would run on the clock the PREVIOUS tick already checked, which is no check at
                # all, and that fill would be booked into the day before it (#539 audit). The
                # boundary's stamp does not depend on it any more (#476).
                if _run_sim_heartbeats(
                        prev_interval_msc, current_msc, config, trade_simulator,
                        worker_coordinator, decision_logic, decision_event_dispatcher,
                        booking, seal_source, state_frames, pass_record.ticks_applied,
                        last_decision):
                    scenario_logger.info(
                        f'🛑 Session end requested: {trade_simulator.get_session_end_reason()}')
                    break

            prev_interval_msc = current_msc

            # #458: the pass itself — the broker path, the bars, the equity sample, the clipping gate,
            # the algo, the execution and the drain — runs in the loop core, in this runner's order
            if not dispatch_backtest_slice(slice_of_tick(tick, sequencer), loop_context, pass_hooks,
                                           pass_record):
                continue

            # === 5c. State Frame (#579 part B item 3) — at the end of the pass, before an end ===
            if state_frames is not None:
                state_frames.on_pass(pass_record.ticks_applied, pass_record.decision, on_tick=True,
                                     closed_timeframes=pass_record.closed_timeframes,
                                     order=pass_record.order_result)
            # #379: each drawing worker's value for a bar that closed, in the same place
            if indicator_series is not None:
                indicator_series.on_pass(pass_record.closed_timeframes, pass_record.current_bars)
            last_decision = pass_record.decision

            if decision_event_dispatcher is not None and trade_simulator.is_session_end_requested():
                scenario_logger.info(
                    f'🛑 Session end requested: {trade_simulator.get_session_end_reason()}')
                break


            # === 6. LIVE UPDATES (Time-based) ===
            if profiling_enabled: t11 = time.perf_counter()
            live_updated = process_live_export(
                live_setup, config, tick_idx, tick, portfolio, worker_coordinator,
                pass_record.current_bars)
            if (live_updated):
                live_update_count += 1
            if profiling_enabled:
                profile_times['live_update'] += (time.perf_counter() - t11) * 1000
                profile_counts['live_update'] += 1

            # Total tick time
            if profiling_enabled:
                profile_times['total_per_tick'] += (time.perf_counter() - tick_start) * 1000

        # #436: close a stale window still active at scenario end (episode
        # span reaches the pot even without a recovery tick).
        if stale_stress_driver is not None:
            stale_stress_driver.finish()

        # === Session end event (#348) — emitted once the loop ends (tick
        # exhaustion or session-end request). Delivered before reporting.
        if decision_event_dispatcher is not None:
            decision_event_dispatcher.emit_session_end()
            # #642 — an order the strategy sent from the session-end event is settled, as a live
            # stop's second wait settles it, and the strategy hears how each ended; one sent
            # after that is withdrawn, as live withdraws it
            trade_simulator.settle_session_end_orders()
            decision_event_dispatcher.emit_session_end_orders()
            trade_simulator.withdraw_unsent_orders()

        if has_clipping:
            scenario_logger.info(
                f'✅ Tick loop completed: {live_setup.tick_count:,} ticks '
                f'({algo_tick_count:,} algo)')
        else:
            scenario_logger.info(
                f'✅ Tick loop completed: {live_setup.tick_count:,} ticks')

        # === FINISH OPEN ORDERS ===
        # The scenario's data has ended, so its active orders can never fill and are
        # expired for the record. An open POSITION is left open and reported as open
        # (#492): force-closing it produced a trade whose exit the strategy never chose,
        # and that trade counted in every ranked KPI — so where the data happened to stop
        # decided part of the result. `expect_flat=False` says the survivor is a
        # consequence of that decision, not an orphan.
        trade_simulator.finish_remaining_orders()
        trade_simulator.check_clean_shutdown(expect_flat=False)
        # #579 part B item 3: the scenario's last frame, at the state its report describes
        if state_frames is not None:
            state_frames.write_shutdown(pass_record.ticks_applied, last_decision)
        # update live the last time - to show final balance correctly
        live_updated = process_live_export(
            live_setup, config, current_index, current_tick, portfolio, worker_coordinator,
            pass_record.current_bars)

    except Exception as e:
        # in case of an error, abort & try to collect the rest of data.
        # see below.
        scenario_logger.error(
            f'Error in Tick Loop - Runtime - try to collect statistics now.: {e}')
        tick_loop_error = e
        if state_frames is not None:
            state_frames.close()
    # #379: closed once on both ends — every line is flushed as written; the close releases the
    # file handles, which a sequential batch would otherwise hold for every scenario it ran
    if indicator_series is not None:
        indicator_series.close()

    try:
        # === CLEANUP COORDINATOR ===
        worker_coordinator.cleanup()
        scenario_logger.debug('✅ Coordinator cleanup completed')

        # === GET RESULTS ===
        tick_range_stats = processed_tick_range_stats(ticks, pass_record.ticks_applied)
        # Collect statistics from Algorithm section
        decision_statistics = decision_logic.get_statistics()
        worker_statistics = worker_coordinator.get_worker_statistics()
        signal_statistics = worker_coordinator.get_signal_statistics()
        coordination_statistics = worker_coordinator.get_coordination_statistics()

        # #451: both staleness domains travel as ONE episode list — the tick domain
        # from the status observer, the signal domain from the orchestrator's pass.
        run_end = current_tick.timestamp if current_tick else None
        disturbance_episodes = (
            market_data_tracker.get_episodes(run_end)
            + worker_coordinator.get_signal_episodes(run_end)
        )

        # collect statistics from Trader section
        # #492: one equity sample at the end — the series behind the drawdown is otherwise
        # written only when a position CLOSES, and the scenario end no longer closes
        # anything. Spot-aware inside, or a purchase would read as a drawdown.
        trade_simulator.portfolio.sample_equity()
        portfolio_stats = trade_simulator.portfolio.get_portfolio_statistics()
        portfolio_stats.symbol = config.symbol
        _symbol_spec = trade_simulator.portfolio.broker_config.get_symbol_specification(
            config.symbol)
        portfolio_stats.base_currency = _symbol_spec.base_currency
        portfolio_stats.quote_currency = _symbol_spec.quote_currency
        # #489 — the same claim an AutoTrader session stamps, from the same shared executor
        # method, so both pipelines report the figure identically.
        portfolio_stats.committed_funds = {
            _symbol_spec.quote_currency: trade_simulator.get_committed_funds(
                _symbol_spec.quote_currency),
            _symbol_spec.base_currency: trade_simulator.get_committed_funds(
                _symbol_spec.base_currency),
        }
        if current_tick:
            portfolio_stats.last_price = current_tick.mid
        execution_stats = trade_simulator.get_execution_stats()
        cost_breakdown = trade_simulator.portfolio.get_cost_breakdown()
        trade_history = trade_simulator.get_trade_history()
        protective_fill_levels = trade_simulator.get_protective_fill_levels()
        order_history = trade_simulator.get_order_history()
        active_orders = trade_simulator.get_active_orders_snapshot()

        # #492: what the scenario's end left open — the block edge's impact. Handed over
        # raw; the block-splitting builder derives the disposition from it off the run.
        open_positions = trade_simulator.get_open_positions()

        _print_tick_loop_finishing_log(
            live_update_count, scenario_logger, portfolio_stats
        )

        # Seals the period that was still running, so the final and necessarily incomplete one
        # is filed like every other rather than dropped for having no successor (#537).
        booking_periods = booking.close(trade_simulator.get_current_time(), seal_source)

        return ProcessTickLoopResult(
            booking_periods=booking_periods,
            decision_statistics=decision_statistics,
            worker_statistics=worker_statistics,
            signal_statistics=signal_statistics,
            disturbance_episodes=disturbance_episodes,
            market_data_tick_stats=market_data_tracker.get_tick_stats(),
            coordination_statistics=coordination_statistics,
            portfolio_stats=portfolio_stats,
            execution_stats=execution_stats,
            cost_breakdown=cost_breakdown,
            trade_history=trade_history,
            order_history=order_history,
            order_events=order_events,
            protective_fill_levels=protective_fill_levels,
            active_orders=active_orders,
            open_positions=open_positions,
            profiling_data=ProcessProfileData(
                profile_times=profile_times,
                profile_counts=profile_counts,
                inter_tick_intervals_ms=inter_tick_intervals,
                gap_threshold_s=config.inter_tick_gap_threshold_s,
                ticks_total=len(ticks)
            ),
            tick_range_stats=tick_range_stats,
            indicator_series_opened=indicator_series is not None,
            tick_loop_error=tick_loop_error
        )
    except Exception as e:
        # an error here is not possible to handle correctly.
        scenario_logger.error(f'Error in Tick Loop - Statistics & Return: {e}')
        # The two errors stand at the same level, and that is not politeness. The block above
        # ABSORBS a tick-loop failure into `tick_loop_error` and lets collection continue — so
        # whatever failed there usually causes something here to fail too, and without the
        # chain the CONSEQUENCE is what propagates while the CAUSE lives only in the scenario
        # log. Measured 2026-09-24: a construction error before the loop surfaced as
        # `ClockNotInjectedError` from the booking close, pointing forty lines past the line
        # that broke. `from` says what is true — no tick ran, so there was no clock to seal
        # against — and prints both tracebacks, the cause first.
        if tick_loop_error is not None:
            raise e from tick_loop_error
        raise


def _print_tick_loop_finishing_log(
        live_update_count: int,
        scenario_logger: ScenarioLogger,
        portfolio_stats: PortfolioStats):
    """
        Simple finishing log after Tick loop for debug purposes.
    """
    total_profit = portfolio_stats.total_profit
    total_loss = portfolio_stats.total_loss
    total_pnl = total_profit - total_loss
    currency = portfolio_stats.currency
    scenario_logger.debug(
        f'Live update count: {live_update_count}')
    scenario_logger.info(
        f'💰 Portfolio P&L: {format_currency_signed(total_pnl, currency, plus_sign=True)}')
