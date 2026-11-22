"""
FiniexTestingIDE - AutoTrader Event Loop
The event loop of an AutoTrader session (Threading model 8.a, #461).

Runs in the main thread: takes one instant at a time from the session's timeline — the ticks, the
venue's answers and the timers — and hands it to the loop core's dispatcher, which runs the broker
path, the bars, the workers and the decision in its order.

Session log rotates daily: session_logs/autotrader_session_YYYYMMDD.log
"""

import queue
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.autotrader.autotrader_display_exporter import AutotraderDisplayExporter
from python.framework.autotrader.event_loop.autotrader_cadence_work import AutotraderCadenceWork
from python.framework.autotrader.event_loop.autotrader_market_data_watch import (
    AutotraderMarketDataWatch,
)
from python.framework.autotrader.event_loop.autotrader_pass_hooks import AutotraderPassHooks
from python.framework.autotrader.event_loop.autotrader_stop_wait import AutotraderStopWait
from python.framework.autotrader.event_loop.autotrader_trading_days import AutotraderTradingDays
from python.framework.autotrader.live_clipping_monitor import LiveClippingMonitor
from python.framework.autotrader.operator_alerts.operator_alerts import OperatorAlerts
from python.framework.autotrader.operator_alerts.operator_alerts_setup import (
    disabled_operator_alerts,
)
from python.framework.autotrader.operator_alerts.signal_staleness_alerter import (
    SignalStalenessAlerter,
)
from python.framework.autotrader.safety.risk_baseline_tracker import RiskBaselineTracker
from python.framework.autotrader.safety.safety_circuit_breaker import SafetyCircuitBreaker
from python.framework.autotrader.shutdown_status_holder import ShutdownStatusHolder
from python.framework.autotrader.stop_budget import StopBudget
from python.framework.autotrader.tick_sources.abstract_tick_source import AbstractTickSource
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.loop.event_sequencer import EventSequencer
from python.framework.loop.intake.lane_arrivals import LaneArrivals
from python.framework.loop.intake.live_event_intake import LiveEventIntake
from python.framework.loop.intake.loop_wake import LoopWake
from python.framework.loop.loop_context import LoopContext
from python.framework.loop.loop_event_dispatcher import (
    dispatch_live_instant,
)
from python.framework.persistence.algo_state_store import AlgoStateStore
from python.framework.process.indicator_series_recorder import IndicatorSeriesRecorder
from python.framework.process.state_frame_recorder import StateFrameRecorder
from python.framework.reporting.api_perf_monitor import ApiPerfMonitor
from python.framework.reporting.booking_period_recorder import (
    BookingPeriodRecorder,
)
from python.framework.signal_data.transport.abstract_signal_transport import (
    AbstractSignalTransport,
)
from python.framework.signal_data.transport.signal_inbox import SignalInbox
from python.framework.stress_test.stale_data_stress_driver import StaleDataStressDriver
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.trading_env.decision_event_dispatcher import DecisionEventDispatcher
from python.framework.trading_env.live.drift_auditor import DriftAuditor
from python.framework.trading_env.live.reconciler import Reconciler
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_loop_state_types import AutotraderLoopState
from python.framework.types.autotrader_types.display_label_cache import DisplayLabelCache
from python.framework.types.autotrader_types.safety_session_types import SafetySessionRecord
from python.framework.types.config_types.market_config_types import TradingModel
from python.framework.types.disturbance_episode_types import DisturbanceEpisode, MarketDataTickStats
from python.framework.types.live_types.reconciliation_types import ReconciliationResult
from python.framework.types.loop_types.live_intake_stats_types import LiveIntakeStats
from python.framework.types.loop_types.live_pass_kind_types import LivePassKind
from python.framework.types.loop_types.loop_event_types import DataSliceEvent, TimeEvent
from python.framework.types.loop_types.loop_pass_record_types import LoopPassRecord
from python.framework.types.loop_types.timer_kind_types import TimerKind
from python.framework.types.run_results_types import BookingPeriod
from python.framework.validators.adapter_wiring_validator import REPLAY_TICK_SOURCE
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


class AutotraderEventLoop:
    """
    The event loop of an AutoTrader session.

    Takes the instants of the session's timeline (#461) — what the tick source put on its queue,
    what the venue answered, what fell due — and hands each to the loop core (#458), as it hands a
    stop's wait. The session's own steps inside a pass are its pass hooks; the market data watch,
    the cadence work, the trading days and the stop's wait are units of their own, which share the
    loop's running state. What follows an instant — the cadence's tick gates, the clipping monitor,
    the display, the state frame — is ordered here.

    Session log rotates when the market crosses into a new trading day — each day gets its
    own file in session_logs/ to prevent unbounded log growth on 24/7 sessions.

    Args:
        config: AutoTrader configuration
        tick_queue: Thread-safe queue receiving ticks from tick source
        tick_source: Tick source (for exhaustion check)
        executor: LiveTradeExecutor instance
        bar_controller: BarRenderingController instance
        worker_orchestrator: WorkerOrchestrator instance
        decision_logic: DecisionLogic instance
        clipping_monitor: LiveClippingMonitor instance
        logger: ScenarioLogger instance (session logger)
        run_dir: Session run directory (for log rotation)
        operator_alerts: The out-of-band channels to the operator (#235) — the loop stamps their
            loop progress on every pass and reports its symptoms through them; off by default
        state_frames: Records what the session holds and decides, moment by moment (#579 part A)
            — asked at the end of every pass; None when the profile records no frames
        indicator_series: Records each drawing worker's value per closed bar (#379) — asked
            beside the state frame after every tick pass; None when the profile records none
        book_sealed_periods: Writes the booking periods sealed so far into the results ledger,
            called at each trading day's seal (#476); None writes nothing before the session ends
        seal_streams: Seals the session's streams into day records at a trading day's boundary
            (#476), given the day that ended and the day that opened; None seals nothing
        open_stop_budget: Opens what a stop may still spend (#630), from the instant it was asked
            for; None opens one at the moment it is needed, from the configured settings
        shutdown_status: What every surface of the stop reads (#630), which the wait writes to;
            None gives the loop one of its own that says its lines in the loop's log
    """

    def __init__(
        self,
        config: AutoTraderConfig,
        tick_queue: queue.Queue,
        tick_source: AbstractTickSource,
        executor: AbstractTradeExecutor,
        bar_controller: BarRenderingController,
        worker_orchestrator: WorkerOrchestrator,
        decision_logic: AbstractDecisionLogic,
        clipping_monitor: LiveClippingMonitor,
        logger: ScenarioLogger,
        trading_model: TradingModel,
        run_dir: Optional[Path] = None,
        display_queue: Optional[queue.Queue] = None,
        session_start: Optional[datetime] = None,
        dry_run: bool = True,
        deployment_id: str = '',
        display_label_cache: Optional[DisplayLabelCache] = None,
        drift_auditor: Optional[DriftAuditor] = None,
        decision_event_dispatcher: Optional[DecisionEventDispatcher] = None,
        reconciler: Optional[Reconciler] = None,
        api_monitor: Optional[ApiPerfMonitor] = None,
        state_store: Optional[AlgoStateStore] = None,
        risk_baseline: Optional[RiskBaselineTracker] = None,
        persist_carry_over: Optional[Callable[[], bool]] = None,
        on_reconciled: Optional[Callable[[ReconciliationResult], None]] = None,
        signal_inbox: Optional[SignalInbox] = None,
        signal_transport: Optional[AbstractSignalTransport] = None,
        carried_period_no: int = 0,
        stale_stress_driver: Optional[StaleDataStressDriver] = None,
        operator_alerts: Optional[OperatorAlerts] = None,
        state_frames: Optional[StateFrameRecorder] = None,
        indicator_series: Optional[IndicatorSeriesRecorder] = None,
        book_sealed_periods: Optional[Callable[[List[BookingPeriod]], None]] = None,
        seal_streams: Optional[Callable[[date, date], None]] = None,
        open_stop_budget: Optional[Callable[[], StopBudget]] = None,
        shutdown_status: Optional[ShutdownStatusHolder] = None,
    ):
        self._config = config
        self._tick_queue = tick_queue
        self._tick_source = tick_source
        self._executor = executor
        self._bar_controller = bar_controller
        self._worker_orchestrator = worker_orchestrator
        # Display only — the loop never calls it. It is handed to the exporter so the
        # operator panel can show whether anything is still arriving; draining goes
        # through the signal inbox the pass hooks hold.
        self._signal_transport = signal_transport
        self._decision_logic = decision_logic
        self._clipping_monitor = clipping_monitor
        # #444: planned tick-plane stale windows, present only on a mock profile that
        # declares them; the market data watch follows them, the loop closes one still active at
        # the session's end.
        self._stale_stress_driver = stale_stress_driver
        # #235: what the operator hears out of band. The loop stamps the progress the liveness
        # ping vouches for on every pass, and reports symptoms — the breaker, a hard stop, stale
        # inputs — each once per edge, never per tick.
        self._operator_alerts = operator_alerts or disabled_operator_alerts()
        self._loop_progress = self._operator_alerts.get_loop_progress()
        # One episode per symptom: the opening alert, then the all-clear only once the symptom
        # stayed away this long — a breaker or a feed flapping around its threshold is one
        # episode, not a message per crossing
        self._all_clear_after_s = config.operator_alerts.telegram.all_clear_after_s
        # Each signal input is its own episode; the orchestrator owns the stale edge
        self._signal_staleness_alerter = SignalStalenessAlerter(
            worker_orchestrator, self._operator_alerts, self._all_clear_after_s,
            executor.get_current_time_if_set)
        self._logger = logger
        self._trading_model = trading_model
        self._display_queue = display_queue
        self._session_start = session_start or datetime.now(timezone.utc)
        self._dry_run = dry_run
        # The RESOLVED deployment, '' for a session that stands alone (#497).
        self._deployment_id = deployment_id
        self._display_label_cache = display_label_cache or DisplayLabelCache()
        self._drift_auditor = drift_auditor
        self._decision_event_dispatcher = decision_event_dispatcher
        self._reconciler = reconciler
        self._api_monitor = api_monitor
        self._state_store = state_store
        self._state_frames = state_frames
        self._indicator_series = indicator_series
        self._running = False
        # What the units of this event loop share while it runs (#461): the tick counts, the
        # tick span, the last tick the algo ran on
        self._state = AutotraderLoopState()

        # #360: idle-heartbeat cadence — max wait for a real tick before the loop
        # fires a timer event (drain + reconcile + re-poll + decision ghost-pass).
        # Config is in ms; queue.get() wants seconds.
        self._heartbeat_interval_s = config.execution.heartbeat_interval_ms / 1000.0
        # The session's timeline (#461), built when the loop starts; a replay slices and stamps
        # by its own time, never coalesces
        self._intake: Optional[LiveEventIntake] = None
        self._replay = config.tick_source.type == REPLAY_TICK_SOURCE

        # Resolve symbol currencies from broker config (avoids string splitting heuristic)
        symbol_spec = executor.broker.adapter.get_symbol_specification(config.symbol)
        self._base_currency = symbol_spec.base_currency
        self._quote_currency = symbol_spec.quote_currency

        # #476 — where THIS market's trading day flips, resolved once. Crypto states
        # 00:00 UTC, forex falls back to its swap rollover (17:00 New York). The booking period
        # below owns the day; the log rotation and the daily-loss baseline read it from there,
        # so a session cannot rotate its log on one boundary and reset its limit on another.
        market_config = MarketConfigManager()
        day_anchor = market_config.get_trading_day_anchor(config.broker_type)

        # === BOOKING PERIODS (#537) — this session's ledger entries ===
        # One recorder, shared with the simulation loop: the two are shaped differently (a class
        # here, a function there) and a recorder each would be two implementations of one rule.
        # It COLLECTS; the report coordinator writes every period at once when the run ends, so
        # the parquet write stays out of what the throughput benchmark measures.
        self._booking = BookingPeriodRecorder(
            unit_name=config.get_unit_name(),
            anchor=day_anchor,
            carried_period_no=carried_period_no,
            log=self._logger.info,
            on_seal=self._on_period_sealed,
            # Which days are trading days, asked the way the simulation asks it — so a session
            # and a backtest over the same days book the same rows (#476)
            weekend_closure=market_config.has_weekend_closure(config.broker_type),
        )

        # #356 / #314 — the safety layer: soft breaker, hard stop, daily loss and their record.
        # Built before the display exporter, which shows its state on every frame. Its trading
        # day is the booking period's.
        self._safety = SafetyCircuitBreaker(
            config=config,
            executor=executor,
            trading_model=trading_model,
            risk_baseline=risk_baseline,
            logger=logger,
            operator_alerts=self._operator_alerts,
            all_clear_after_s=self._all_clear_after_s,
            trading_day=self._booking.get_current_day,
        )

        # The units of the event loop (#461), each with its part of the session's work, built in
        # the order they need each other
        self._market_watch = AutotraderMarketDataWatch(
            config, executor, decision_logic, tick_source, stale_stress_driver,
            self._operator_alerts, self._all_clear_after_s, logger, self._state)
        self._cadence = AutotraderCadenceWork(
            config, executor, decision_logic, reconciler, state_store, risk_baseline,
            persist_carry_over, logger, self._get_intake, self._axis_now_msc, on_reconciled)
        self._trading_days = AutotraderTradingDays(
            executor, self._booking, self._safety, self._cadence, trading_model, day_anchor,
            run_dir, self._operator_alerts, config.operator_alerts.telegram.daily_digest,
            book_sealed_periods, seal_streams, logger, self._state)
        self._hooks = AutotraderPassHooks(
            config, executor, worker_orchestrator, decision_logic, signal_inbox, trading_model,
            self._booking, self._safety, risk_baseline, self._market_watch, self._cadence,
            self._trading_days, logger, self._state)

        # #400 — Display stats builder (extracted from this loop). Holds the
        # stable collaborators; the volatile per-frame state (safety, rejections,
        # spot-equity baseline) is passed per call.
        self._display_exporter = AutotraderDisplayExporter(
            config=self._config,
            executor=self._executor,
            worker_orchestrator=self._worker_orchestrator,
            decision_logic=self._decision_logic,
            clipping_monitor=self._clipping_monitor,
            tick_queue=self._tick_queue,
            trading_model=self._trading_model,
            base_currency=self._base_currency,
            quote_currency=self._quote_currency,
            session_start=self._session_start,
            dry_run=self._dry_run,
            deployment_id=self._deployment_id,
            display_label_cache=self._display_label_cache,
            drift_auditor=self._drift_auditor,
            reconciler=self._reconciler,
            api_monitor=self._api_monitor,
            signal_transport=self._signal_transport,
        )

        # #458 — what every pass of the loop core works with, bundled once for the session
        self._loop_context = LoopContext(
            executor=self._executor,
            bar_controller=self._bar_controller,
            orchestrator=self._worker_orchestrator,
            decision_logic=self._decision_logic,
            decision_event_dispatcher=self._decision_event_dispatcher,
            symbol=self._config.symbol,
            booking=self._booking,
            seal_source=self._trading_days.seal_source,
            reads_closed_bars=(self._state_frames is not None
                               or self._indicator_series is not None),
            profiling_enabled=False,
            profile_times={},
            profile_counts={},
        )

        if shutdown_status is None:
            shutdown_status = ShutdownStatusHolder(config.shutdown.supervisor_kill_after_s)
            shutdown_status.connect_journal(logger)
        # #630 — a normal stop's wait for the orders on their way
        self._stop_wait = AutotraderStopWait(
            config, executor, self._loop_context, self._hooks, self._cadence, shutdown_status,
            open_stop_budget, self._operator_alerts, self._loop_progress,
            decision_event_dispatcher, self._push_pulse_frame, self._heartbeat_interval_s,
            logger)

    def stop(self) -> None:
        """Signal the event loop to stop. Thread-safe."""
        self._running = False

    def run(self) -> Tuple[int, int]:
        """
        Execute the tick processing loop.

        Blocks until tick source is exhausted, stop() is called,
        or a sentinel (None) is received from the queue. A normal stop then waits for the orders
        on their way, under its own bound.

        Returns:
            (ticks_processed, ticks_clipped) counts
        """
        self._running = True
        self._state.loop_started_monotonic = time.monotonic()
        ticks_processed = 0
        ticks_clipped = 0
        # #458: what each tick pass leaves for the steps below it, and the slices' numbering
        pass_record = LoopPassRecord()
        sequencer = EventSequencer()

        # #320 — Push an initial startup pulse frame so the display renders
        # the full dashboard from t=0 instead of falling back to a wait
        # placeholder. Subsequent pulses fire from the heartbeat timer.
        self._push_pulse_frame(ticks_processed)

        # #348: an end requested before the first pass — a Ctrl+C or a SIGTERM while the session
        # was starting — makes no pass at all, so no decision runs after the stop was asked for.
        # The end-of-run steps below still run and tell the decision logic why.
        if self._executor.is_session_end_requested():
            self._logger.info(
                f'🛑 Session end requested before the first pass: '
                f'{self._executor.get_session_end_reason()}')
            self._running = False
            # No pass means nothing set the canonical clock, and the end-of-run steps read it —
            # the session-end event's stamp, the disturbance episodes the shutdown collects.
            # Set it the way an idle pass does, so a stop during the start ends cleanly.
            if self._executor.get_current_time_if_set() is None:
                self._executor.set_current_time(self._idle_pass_time())

        # #461: the session's timeline — the ticks and the venue's answers on two lanes, the
        # timers on their schedules, handed over one instant at a time
        self._intake = self._build_intake(sequencer)

        while self._running:
            # #235: one stamp per pass — a pass that hangs stamps no more, and the liveness ping
            # stops vouching for the loop
            self._loop_progress.stamp()
            self._signal_staleness_alerter.check()
            events = self._intake.next_instant()
            if events is None:
                self._logger.info('📭 Tick source signaled end — ending session')
                break
            data = next((event for event in events if isinstance(event, DataSliceEvent)), None)
            timers = {event.timer for event in events if isinstance(event, TimeEvent)}
            if (data is None and TimerKind.HEARTBEAT in timers and self._tick_source
                    and self._tick_source.is_exhausted()):
                # A quiet heartbeat with a source that has nothing more to give ends the session,
                # before the pass, as an idle pass always did
                self._logger.info('📭 Tick source exhausted — ending session')
                break

            # === TIMING START ===
            pass_start_ns = time.perf_counter_ns()
            if data is not None:
                ticks_processed += len(data.ticks)
                self._state.ticks_processed = ticks_processed
                self._state.tick_passes += 1

            # What the backlog left behind the newest tick, and how long it waited (#461)
            ticks_skipped, wait_ms = self._intake.get_handed_backlog()

            # === The instant, through the loop core (#458, #461) ===
            # Clock and day boundary, broker path, bars, signals, the venue's answers, the
            # timers' cadence work, the drain, the algo or the ghost pass, execute, drain — this
            # loop's own steps inside it are its hooks below
            kind = dispatch_live_instant(events, self._executor, self._loop_context, self._hooks,
                                         pass_record, ticks_skipped)
            algo_ran = kind is LivePassKind.TICK_PASS

            # === Reconciliation (#151) and algo state (#354) by the tick count — their time
            # part is a timer of the timeline ===
            if data is not None:
                self._cadence.reconcile_if_due(ticks_processed)
                self._cadence.persist_state_if_due(ticks_processed)
            if TimerKind.PERSISTENCE in timers:
                self._cadence.persist_state_now(ticks_processed)

            # === Position Book Carry-Over (#355, on change) ===
            # A fill can resolve on any instant, so the book can change on any instant
            self._cadence.persist_carry_over_if_needed(ticks_processed)

            # === TIMING END ===
            elapsed_ns = time.perf_counter_ns() - pass_start_ns

            if data is not None:
                # === Clipping Monitor — a measurement, never a skip (#461) ===
                self._clipping_monitor.record_pass(elapsed_ns, len(data.ticks), ticks_skipped,
                                                   wait_ms)
                self._clipping_monitor.record_queue_depth(self._tick_queue.qsize())
                report = self._clipping_monitor.get_periodic_report()
                if report is not None:
                    self._logger.info(
                        f'📊 Clipping report: {report.interval_ticks} ticks in '
                        f'{report.interval_passes} passes, '
                        f'{report.interval_clipped} clipped, '
                        f'avg {report.interval_avg_processing_ms:.2f}ms a pass, '
                        f'max {report.interval_max_processing_ms:.2f}ms, '
                        f'queue_depth_max={report.interval_max_queue_depth} arrivals'
                    )
                    ticks_clipped += report.interval_clipped
                    self._state.ticks_clipped = ticks_clipped

            if algo_ran and self._display_queue is not None:
                # === Display Stats ===
                display_stats = self._display_exporter.build(
                    pass_record.decision, ticks_processed, data.ticks[-1],
                    safety=self._safety.get_display_state(),
                    recent_rejections=self._hooks.get_recent_rejections(),
                    total_rejections=self._hooks.get_rejection_count(),
                    initial_spot_equity=self._hooks.get_initial_spot_equity())
                try:
                    self._display_queue.put_nowait(display_stats)
                except queue.Full:
                    pass  # Display will use last known state
            elif TimerKind.HEARTBEAT in timers:
                self._push_pulse_frame(ticks_processed)

            # === State Frame (#579) and indicator series (#379) — only where the algo ran, as in
            # a backtest; a timer writes the cadence through the recorder's own gate, unless a
            # hook ended the session in the instant's delivery: no pass ran, and a backtest's
            # instant writes nothing then ===
            # After the timing above: recording is not the algo being slow
            ended_in_delivery = (kind is LivePassKind.NO_PASS
                                 and self._executor.is_session_end_requested())
            if self._state_frames is not None and (
                    algo_ran or (timers and not ended_in_delivery)):
                safety = self._safety.get_display_state()
                self._state_frames.on_pass(
                    ticks_processed,
                    pass_record.decision if algo_ran else self._state.last_real_decision,
                    on_tick=algo_ran,
                    closed_timeframes=pass_record.closed_timeframes if algo_ran else frozenset(),
                    order=pass_record.order_result if algo_ran else pass_record.ghost_order,
                    safety_blocked=safety.blocked, safety_drawdown_pct=safety.drawdown_pct)
            if TimerKind.STATE_FRAME_SLOT in timers:
                self._arm_state_frame_slot()
            if algo_ran:
                if self._indicator_series is not None:
                    self._indicator_series.on_pass(pass_record.closed_timeframes,
                                                   pass_record.current_bars)
                # Track last valid tick/decision for the final shutdown snapshot and the
                # heartbeat pulse frames (#320)
                self._state.last_real_tick = data.ticks[-1]
                self._state.last_real_decision = pass_record.decision
                # Read back only as an age (the staleness flip, the display): a step of the wall
                # clock lands in it — it belongs on the monotonic clock (#549)
                self._state.last_real_tick_wall_time = time.time()

            # After the records, as in a backtest: a session a hook ended keeps its last pass
            if self._executor.is_session_end_requested():
                self._logger.info(
                    f'🛑 Session end requested: {self._executor.get_session_end_reason()}')
                break

        # #630: a normal stop waits here for the orders on their way — the last moment a hook can
        # still hear a fill, with the day boundary and the carry-over watcher still in this loop
        self._stop_wait.wait_for_orders(ticks_processed)

        # === Session end event (#348) — emitted once the loop ends, whether by
        # request, tick-source exhaustion, or stop(). Delivered before teardown.
        if self._decision_event_dispatcher is not None:
            self._decision_event_dispatcher.emit_session_end()
            # An order the logic sent from the session-end event is waited for as well, so
            # its fill is booked before the cleanup, and the logic hears how it ended (#642)
            if not self._stop_wait.has_run_out():
                self._stop_wait.wait_for_orders(ticks_processed, after_session_end=True)
            # The session is over: what the logic sends from the outcome hook is held back and
            # withdrawn, never sent — the simulation's settle, emit and withdraw, in a session
            self._executor.hold_new_orders()
            self._decision_event_dispatcher.emit_session_end_orders()
            self._executor.withdraw_unsent_orders()

        # Final display snapshot — ensures the last rendered frame reflects
        # the terminal pipeline state regardless of display refresh timing.
        self.push_last_display_frame(ticks_processed)

        # #360 ghost-pass observability — proves the idle decision pass fired
        # (and acted) over the session. Machine-parseable. ghost_decisions counts the passes that
        # returned a decision; the worker-decision report's ghost_passes counts every pass (#460)
        self._logger.info(
            f'[GHOST] ghost_decisions={self._hooks.get_ghost_pass_count()} '
            f'ghost_actions={self._hooks.get_ghost_action_count()} ticks={ticks_processed}')

        # #444: close a window still active at session end, so the episode span reaches the
        # report even without a recovery tick. Same step the sim takes at scenario end.
        if self._stale_stress_driver is not None:
            self._stale_stress_driver.finish()

        self._running = False
        return ticks_processed, ticks_clipped

    def _build_intake(self, sequencer: EventSequencer) -> LiveEventIntake:
        """
        Build the session's timeline (#461): the producers told where to report an arrival, and the
        timers armed on their schedules.

        Args:
            sequencer: The session's counter for an event's submission order

        Returns:
            The intake the loop takes its instants from
        """
        self._wake = LoopWake(self._tick_queue)
        self._market_arrivals = LaneArrivals()
        self._execution_arrivals = LaneArrivals()
        if self._tick_source is not None:
            self._tick_source.set_arrival_listener(self._on_ticks_arrived)
        self._executor.set_arrival_listener(self._on_answer_arrived)
        intake = LiveEventIntake(
            self._tick_queue, self._market_arrivals, self._execution_arrivals,
            sequencer, self._axis_now_msc, round(self._heartbeat_interval_s * 1000),
            coalesce_backlog=not self._replay,
            logger=self._logger)
        now = self._axis_now_msc()
        if self._reconciler is not None:
            intake.arm(TimerKind.RECONCILE,
                       now + round(self._reconciler.get_min_interval_seconds() * 1000))
        if self._state_store is not None:
            intake.arm(TimerKind.PERSISTENCE,
                       now + round(self._state_store.get_save_interval_seconds() * 1000))
        self._intake = intake
        if self._state_frames is not None:
            self._arm_state_frame_slot()
        return intake

    def _on_ticks_arrived(self) -> None:
        """
        A tick source put a frame's ticks on the queue — on its thread. The ticks woke the loop
        themselves; this records when they arrived, for the backlog's lag.
        """
        self._market_arrivals.note(self._axis_now_msc())

    def _on_answer_arrived(self) -> None:
        """
        The venue's answer landed in the executor's inbox — on the worker thread: record and wake
        only. Stamped on the session's axis, as a tick is: in a replay the wall clock lies months
        ahead of the replay's time, and a stamp from it would move the clock there and seal every
        day in between.
        """
        self._execution_arrivals.note(self._axis_now_msc())
        self._wake.notify()

    def _get_intake(self) -> Optional[LiveEventIntake]:
        """
        Returns:
            The session's timeline (#461); None before the loop built it
        """
        return self._intake

    def _arm_state_frame_slot(self) -> None:
        """Schedule the state frames' next cadence period on the timeline (#579, #461)."""
        if self._intake is None or self._state_frames is None:
            return
        now = datetime.fromtimestamp(self._axis_now_msc() / 1000.0, tz=timezone.utc)
        self._intake.arm(TimerKind.STATE_FRAME_SLOT,
                         round(self._state_frames.next_slot_start(now).timestamp() * 1000))

    def _axis_now_msc(self) -> int:
        """
        The receipt axis now — the wall clock live, a replay's own time in a mock (#616, #461).

        A replay's own time is the front of its source, and a loop running behind the replay stands
        earlier: there the loop's own position wins, so an answer or a timer is never stamped past
        ticks still waiting on the queue. Live, the wall clock is the receipt itself.

        Returns:
            UTC epoch milliseconds
        """
        source_now = round(self._idle_pass_time().timestamp() * 1000)
        if not self._replay or self._intake is None:
            return source_now
        position = self._intake.get_position_msc()
        return source_now if position is None else min(source_now, position)

    def _idle_pass_time(self) -> datetime:
        """
        The canonical clock for a pass with no tick: the tick source's own time (#616).

        Returns:
            The source's answer — the wall clock live, the replay's time in a mock — or the wall
            clock when the loop runs without a source
        """
        if self._tick_source is None:
            return datetime.now(timezone.utc)
        return self._tick_source.get_idle_pass_time()

    def _on_period_sealed(self, period: BookingPeriod) -> None:
        """
        A booking period was sealed: what that sets off belongs to the trading days (#476). The
        recorder is built before them, so it is handed this, which asks them when a seal comes.

        Args:
            period: The booking period just sealed
        """
        self._trading_days.on_period_sealed(period)

    def push_last_display_frame(self, ticks_processed: int) -> None:
        """
        Hand the display a snapshot of the session as it stands now: when the loop ends, and again
        once the stop's cleanup has moved the book, so its last frame shows the orders and
        the portfolio the report describes.

        The queue (maxsize=10) is likely full from a fast replay, so the stale snapshots are
        drained first, to guarantee this one lands.

        Args:
            ticks_processed: The session's tick count
        """
        if (self._display_queue is None or self._state.last_real_tick is None
                or self._state.last_real_decision is None):
            return
        while not self._display_queue.empty():
            try:
                self._display_queue.get_nowait()
            except queue.Empty:
                break
        final_stats = self._display_exporter.build(
            self._state.last_real_decision, ticks_processed, self._state.last_real_tick,
            safety=self._safety.get_display_state(),
            recent_rejections=self._hooks.get_recent_rejections(),
            total_rejections=self._hooks.get_rejection_count(),
            initial_spot_equity=self._hooks.get_initial_spot_equity())
        try:
            self._display_queue.put(final_stats, timeout=1.0)
        except queue.Full:
            pass

    def write_final_state_frame(self, ticks_processed: int) -> None:
        """
        Write the session's last state frame and close the stream (#579 part A).

        Called by the shutdown once the session's orders are finished and the venue was asked
        once more, before the results are collected — so the frame shows the state the report
        describes, valued at the same price.

        Args:
            ticks_processed: The session's tick count
        """
        if self._state_frames is None:
            return
        safety = self._safety.get_display_state()
        self._state_frames.write_shutdown(
            ticks_processed, self._state.last_real_decision,
            safety_blocked=safety.blocked, safety_drawdown_pct=safety.drawdown_pct)

    def _push_pulse_frame(self, ticks_processed: int) -> None:
        """
        Push a heartbeat pulse frame to the display queue (#320).

        Pulse frames carry the last-known portfolio snapshot plus a wall-clock
        delta so the dashboard shows "💓 Ns since last tick" instead of going
        stale during idle.

        Before the first real tick, a slim startup snapshot is pushed instead
        so the operator sees the session is alive (`💓 Ns since startup`)
        rather than the display's default "Waiting for first tick..." text.
        """
        if self._display_queue is None:
            return

        if self._state.last_real_tick is None or self._state.last_real_decision is None:
            seconds_since = (datetime.now(timezone.utc) - self._session_start).total_seconds()
            stats = self._display_exporter.build_startup_pulse(seconds_since)
        else:
            seconds_since = time.time() - self._state.last_real_tick_wall_time
            stats = self._display_exporter.build(
                self._state.last_real_decision, ticks_processed, self._state.last_real_tick,
                safety=self._safety.get_display_state(),
                recent_rejections=self._hooks.get_recent_rejections(),
                total_rejections=self._hooks.get_rejection_count(),
                initial_spot_equity=self._hooks.get_initial_spot_equity())
            stats.is_pulse = True
            stats.seconds_since_last_tick = seconds_since

        try:
            self._display_queue.put_nowait(stats)
        except queue.Full:
            pass  # Display will use last known state

    def get_disturbance_episodes(self) -> List[DisturbanceEpisode]:
        """
        The session's observed market-data outage episodes (#451).

        Returns:
            List of DisturbanceEpisode (an episode still open is never-recovered)
        """
        return self._market_watch.get_disturbance_episodes()

    def has_opened_day_log(self) -> bool:
        """
        Whether a pass of this loop has opened a trading day's session log.

        Returns:
            True once the first day's log is open
        """
        return self._trading_days.has_opened_day_log()

    def get_intake_stats(self) -> Optional[LiveIntakeStats]:
        """
        What the session's timeline counted (#461): its instants, coalesced slices, the venue's
        answers and the timers.

        Returns:
            The intake's counts; None before the loop ran
        """
        return self._intake.get_stats() if self._intake is not None else None

    def get_tick_span(self) -> Tuple[Optional[datetime], Optional[datetime]]:
        """
        The first and the latest tick this session processed.

        Returns:
            (first, last) tick timestamps; (None, None) before the first tick
        """
        return self._state.first_tick_time, self._state.last_tick_time

    def get_tick_counts(self) -> Tuple[int, int]:
        """
        The ticks this session processed and clipped so far — readable after an exception ended
        the loop, whose counts `run`'s return value cannot carry out (#636).

        Returns:
            (ticks_processed, ticks_clipped)
        """
        return self._state.ticks_processed, self._state.ticks_clipped

    def get_market_data_tick_stats(self) -> MarketDataTickStats:
        """
        The session's market-data tick counters (#451 Part 4).

        Returns:
            MarketDataTickStats for the session's tick source
        """
        return self._market_watch.get_tick_stats()

    def get_highest_period_no(self) -> int:
        """
        The largest booking period this session has sealed (#537).

        Read by the carry-over so the next session continues the count.

        Returns:
            The high-water mark, which is the inherited floor when nothing was sealed
        """
        return self._trading_days.get_highest_period_no()

    def get_booking_periods(self) -> List[BookingPeriod]:
        """
        This session's ledger entries — one per closed booking period (#537).

        Seals the period that was still running, so the final and necessarily incomplete one is
        filed like every other rather than dropped for having no successor. Exactly what
        `get_safety_session` does for the day records, and for the same reason.

        Returns:
            The periods, oldest first
        """
        return self._trading_days.get_booking_periods()

    def get_safety_session(self) -> SafetySessionRecord:
        """
        What the circuit breaker saw over this session, for the report (#356 / #314).

        Returns:
            The breaker's record; its baseline is None when none was ever taken
        """
        return self._safety.get_safety_session()
