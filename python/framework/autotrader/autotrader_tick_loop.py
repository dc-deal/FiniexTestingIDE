"""
FiniexTestingIDE - AutoTrader Tick Loop
Main tick processing loop for an AutoTrader session (Threading model 8.a).

Runs in the main thread, pulls ticks from queue, processes through:
executor.on_tick → bar_controller → workers → decision_logic.

Session log rotates daily: session_logs/autotrader_session_YYYYMMDD.log
"""

import queue
import time
from collections import deque
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Deque, Dict, List, Optional, Tuple

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.autotrader.autotrader_display_exporter import AutotraderDisplayExporter
from python.framework.autotrader.live_clipping_monitor import LiveClippingMonitor
from python.framework.autotrader.operator_alerts.alert_episode import (
    AlertEpisode,
    all_clear_detail,
)
from python.framework.autotrader.operator_alerts.operator_alert_messages import (
    day_closed_text,
    days_closed_unheard_text,
)
from python.framework.autotrader.operator_alerts.operator_alerts import OperatorAlerts
from python.framework.autotrader.operator_alerts.operator_alerts_setup import (
    disabled_operator_alerts,
)
from python.framework.autotrader.operator_alerts.signal_staleness_alerter import (
    SignalStalenessAlerter,
)
from python.framework.autotrader.run_record.state_frame_recorder import StateFrameRecorder
from python.framework.autotrader.safety.risk_baseline_tracker import RiskBaselineTracker
from python.framework.autotrader.safety.safety_circuit_breaker import SafetyCircuitBreaker
from python.framework.autotrader.session_log_rotator import SessionLogRotator
from python.framework.autotrader.tick_sources.abstract_tick_source import AbstractTickSource
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.persistence.algo_state_store import AlgoStateStore
from python.framework.persistence.position_book_watcher import PositionBookWatcher
from python.framework.process.market_data_episode_tracker import MarketDataEpisodeTracker
from python.framework.process.tick_pipeline_core import (
    execute_algo_path,
    render_bars_for_tick,
    run_ghost_pass,
)
from python.framework.reporting.api_perf_monitor import ApiPerfMonitor
from python.framework.reporting.booking_period_recorder import (
    BookingPeriodRecorder,
    snapshot_from_portfolio,
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
from python.framework.types.autotrader_types.autotrader_display_types import RejectionEntry
from python.framework.types.autotrader_types.display_label_cache import DisplayLabelCache
from python.framework.types.autotrader_types.operator_alert_types import (
    OperatorAlertKind,
    OperatorAlertLevel,
)
from python.framework.types.autotrader_types.safety_session_types import SafetySessionRecord
from python.framework.types.config_types.market_config_types import TradingModel
from python.framework.types.decision_logic_types import Decision, DecisionLogicAction
from python.framework.types.disturbance_episode_types import DisturbanceEpisode, MarketDataTickStats
from python.framework.types.log_level import LogLevel
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.persistence_types import BaselineQuantities
from python.framework.types.run_results_types import BookingPeriod, PeriodCloseReason
from python.framework.types.trading_env_types.market_data_status_types import MarketDataStatus
from python.framework.types.trading_env_types.order_types import OrderResult
from python.framework.utils.trading_day_anchor import boundary_opening
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


class AutotraderTickLoop:
    """
    Tick processing loop for an AutoTrader session.

    Pulls ticks from a queue.Queue (fed by a TickSource thread),
    processes each tick through the full algo pipeline:
    on_tick → bars → workers → decision → clipping monitor.

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
        book_sealed_periods: Writes the booking periods sealed so far into the results ledger,
            called at each trading day's seal (#476); None writes nothing before the session ends
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
        signal_inbox: Optional[SignalInbox] = None,
        signal_transport: Optional[AbstractSignalTransport] = None,
        carried_period_no: int = 0,
        stale_stress_driver: Optional[StaleDataStressDriver] = None,
        operator_alerts: Optional[OperatorAlerts] = None,
        state_frames: Optional[StateFrameRecorder] = None,
        book_sealed_periods: Optional[Callable[[List[BookingPeriod]], None]] = None,
    ):
        self._config = config
        self._tick_queue = tick_queue
        self._tick_source = tick_source
        self._executor = executor
        self._bar_controller = bar_controller
        self._worker_orchestrator = worker_orchestrator
        # #141 Part 2a: filled by a live signal transport on its own thread, drained here
        # once per pass. Present ONLY when the startup resolved a LIVE signal source — a
        # mounted (mock / simulation) session has none, so every drain below is a no-op and the
        # loop behaves exactly as before.
        self._signal_inbox = signal_inbox
        # Display only — the loop never calls it. It is handed to the exporter so the
        # operator panel can show whether anything is still arriving; draining goes
        # through the inbox above.
        self._signal_transport = signal_transport
        self._decision_logic = decision_logic
        self._clipping_monitor = clipping_monitor
        # #444: planned tick-plane stale windows, present only on a mock profile that
        # declares them. None on every live-adapter session — the block lives in
        # scenario_settings, which a live-adapter profile does not carry at all.
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
        # Each trading day's close, rendered for the operator from the period it sealed
        self._daily_digest = config.operator_alerts.telegram.daily_digest
        self._market_data_alert = AlertEpisode(self._all_clear_after_s)
        # Each signal input is its own episode; the orchestrator owns the stale edge
        self._signal_staleness_alerter = SignalStalenessAlerter(
            worker_orchestrator, self._operator_alerts, self._all_clear_after_s,
            executor.get_current_time_if_set)
        # When the loop started, on the monotonic clock: a first tick that never comes is an
        # outage the operator hears about, though the status calls it a startup wait (#436)
        self._loop_started_monotonic: float = 0.0
        self._logger = logger
        self._trading_model = trading_model
        self._run_dir = run_dir
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
        self._risk_baseline = risk_baseline
        # #355 — the open book is written when it CHANGES, in two classes. A STRUCTURAL
        # change (a position opens, closes, is partially closed) is written at once: it cannot
        # be recovered, and waiting out an interval is exactly the window a hard kill takes
        # the position away in. DRIFT (exit levels, excursion extrema) waits for a cadence,
        # because a trailing stop moves on nearly every tick of a trend and one write costs
        # 11 ms on this project's tree (§42) — immediate would mean a 11 ms stall per tick to
        # protect a value the algo re-derives anyway. The watcher is seeded with the book as
        # it stands after adoption, so a session that changes nothing never writes.
        self._persist_carry_over = persist_carry_over
        self._book_sealed_periods = book_sealed_periods
        # The trading days one boundary check sealed, closed together once the check is done:
        # a check several days late seals one period per day, and booking each on its own would
        # rewrite the ledger fragment once per day inside one pass (#476)
        self._sealed_in_check: List[BookingPeriod] = []
        self._book_watcher = PositionBookWatcher(executor.portfolio.get_open_positions())
        # #356 — the baseline record the carry-over on disk already carries. Seeded from the
        # tracker rather than from None: a tracker that already holds a record at
        # CONSTRUCTION time restored it from the store, and the boot write ran before this
        # loop existed, so that record is on disk. A session that takes its baseline on the
        # first tick seeds None and therefore writes it there — see
        # `_baseline_needs_writing` for why that moment matters.
        self._persisted_baseline = (
            risk_baseline.get_baseline() if risk_baseline is not None else None)
        self._book_drift_interval_ticks = config.cold_start.book_drift_interval_ticks
        self._last_book_drift_tick = 0
        self._running = False

        # #360: idle-heartbeat cadence — max wait for a real tick before the loop
        # fires a timer event (drain + reconcile + re-poll + decision ghost-pass).
        # Config is in ms; queue.get() wants seconds.
        self._heartbeat_interval_s = config.execution.heartbeat_interval_ms / 1000.0

        # #360 ghost-pass observability — proves the heartbeat decision pass fires
        # (and how often it acts), reported once at session end.
        self._ghost_pass_count: int = 0
        self._ghost_action_count: int = 0

        # #436: market-data staleness contract — no real tick for this many wall
        # seconds → session-level stale (pot warning + on_market_data_stale +
        # OrderGuard entry block). 0 disables. Edge state below; the episode
        # record itself belongs to the observer (#451).
        self._market_data_stale_after_s = config.execution.market_data_stale_after_s
        self._market_stale = False
        self._market_stale_since: Optional[datetime] = None
        self._last_reconnect_count: int = 0

        # #451: the observer that turns the status flips above into episode records.
        # It sees both event sources (tick + heartbeat) and measures on the wall axis
        # (§9 duration rule — the canonical clock is bimodal in a mock replay session).
        #
        # #549: the wall axis is right for LIVE, where the physical silence is the truth, and
        # wrong for a mock REPLAY, where the drill's real extent is its DATA span. The rendered
        # line puts the two side by side: measured 2026-09-24, a ten-minute injected window over
        # 281 stale ticks printed `06:10 → 06:20 (0s)`, and the freeze drill printed a five-month
        # span with a one-second duration. Not a one-line fix — in the same mock session the
        # freeze wants the wall axis and the planned window wants the data axis, so it is a
        # question per EPISODE rather than per session.
        self._market_data_tracker = MarketDataEpisodeTracker(
            source=config.broker_type,
            logger=logger,
            measure_wall_duration=True,
        )

        # Resolve symbol currencies from broker config (avoids string splitting heuristic)
        symbol_spec = executor.broker.adapter.get_symbol_specification(config.symbol)
        self._base_currency = symbol_spec.base_currency
        self._quote_currency = symbol_spec.quote_currency

        # #356 / #314 — the safety layer: soft breaker, hard stop, daily loss and their record.
        # Built before the display exporter, which shows its state on every frame.
        self._safety = SafetyCircuitBreaker(
            config=config,
            executor=executor,
            trading_model=trading_model,
            risk_baseline=risk_baseline,
            logger=logger,
            operator_alerts=self._operator_alerts,
            all_clear_after_s=self._all_clear_after_s,
            trading_day=self._trading_day,
        )

        # Last rejection (displayed until overwritten by next rejection)
        self._recent_rejections: Deque[RejectionEntry] = deque(maxlen=5)
        self._rejection_count: int = 0

        # New-max tracking for debug logging
        self._known_worker_maxes: Dict[str, float] = {}
        self._known_decision_max: float = 0.0

        # The DISPLAY's spot reference — the value the account started this session at, so
        # a running P&L has something to be a P&L against. Deliberately NOT the risk
        # baseline since #356: that one survives restarts and may be a high-water mark, and
        # showing a drawdown against a peak while the P&L is shown against the session start
        # is two different questions in one column.
        self._initial_spot_equity: float = 0.0

        # #320 — Last real-tick state for heartbeat pulse frames. Updated only
        # when a tick is actually processed, NOT during heartbeat. Used to
        # build pulse snapshots that show last-known portfolio state plus a
        # "Ns since last tick" idle indicator.
        self._last_real_tick: Optional[TickData] = None
        self._last_real_decision: Optional[Decision] = None
        self._last_real_tick_wall_time: float = 0.0

        # #476 — where THIS market's trading day flips, resolved once. Crypto states
        # 00:00 UTC, forex falls back to its swap rollover (17:00 New York). The booking period
        # below owns the day; the log rotation and the daily-loss baseline read it from there,
        # so a session cannot rotate its log on one boundary and reset its limit on another.
        market_config = MarketConfigManager()
        self._day_anchor = market_config.get_trading_day_anchor(config.broker_type)

        # === BOOKING PERIODS (#537) — this session's ledger entries ===
        # One recorder, shared with the simulation loop: the two are shaped differently (a class
        # here, a function there) and a recorder each would be two implementations of one rule.
        # It COLLECTS; the report coordinator writes every period at once when the run ends, so
        # the parquet write stays out of what the throughput benchmark measures.
        # The market time this session processed — its first tick and the latest one the broker
        # path saw (the glossary's *tick timespan*). Two assignments per tick, no allocation.
        self._first_tick_time: Optional[datetime] = None
        self._last_tick_time: Optional[datetime] = None
        self._booking = BookingPeriodRecorder(
            unit_name=config.get_unit_name(),
            anchor=self._day_anchor,
            carried_period_no=carried_period_no,
            log=self._logger.info,
            on_seal=self._on_period_sealed,
            # Which days are trading days, asked the way the simulation asks it — so a session
            # and a backtest over the same days book the same rows (#476)
            weekend_closure=market_config.has_weekend_closure(config.broker_type),
        )

        # The session log rotates on this market's trading day — the same day the booking
        # period and the daily loss limit read
        self._log_rotator = SessionLogRotator(run_dir, logger, self._trading_day)

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

    def stop(self) -> None:
        """Signal the tick loop to stop. Thread-safe."""
        self._running = False

    def run(self) -> Tuple[int, int]:
        """
        Execute the tick processing loop.

        Blocks until tick source is exhausted, stop() is called,
        or a sentinel (None) is received from the queue.

        Returns:
            (ticks_processed, ticks_clipped) counts
        """
        self._running = True
        self._loop_started_monotonic = time.monotonic()
        ticks_processed = 0
        ticks_clipped = 0
        prev_msc: int = 0

        # Track last valid tick/decision for the final shutdown snapshot
        last_tick: Optional[TickData] = None
        last_decision: Optional[Decision] = None

        # #320 — Push an initial startup pulse frame so the display renders
        # the full dashboard from t=0 instead of falling back to a wait
        # placeholder. Subsequent pulses fire from queue.Empty heartbeats.
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
                self._executor.set_current_time(datetime.now(timezone.utc))

        while self._running:
            # #235: one stamp per pass, tick or heartbeat — a pass that hangs stamps no more,
            # and the liveness ping stops vouching for the loop
            self._loop_progress.stamp()
            self._signal_staleness_alerter.check()
            try:
                tick = self._tick_queue.get(timeout=self._heartbeat_interval_s)
            except queue.Empty:
                # No tick within timeout — check if source is exhausted
                if self._tick_source and self._tick_source.is_exhausted():
                    self._logger.info(
                        '📭 Tick source exhausted — ending session')
                    break
                # #360: timer event. Inject the wall-clock so the canonical clock
                # advances during idle (phase/op timeouts track real elapsed time),
                # then run the side-effect-free cadence — no tick state mutation.
                self._executor.set_current_time(datetime.now(timezone.utc))
                # #537 / #476: the boundary is checked HERE too, before anything this pass
                # resolves — a fill the heartbeat drains after the boundary belongs to the day
                # it opened. The seal is stamped with the boundary, whenever it is noticed.
                self._booking.check_boundary(
                    self._executor.get_current_time_if_set(), self._seal_source)
                self._close_sealed_days()
                # The log follows the booking period's day, and after it: the line that seals a
                # day is written into that day's file, and a feed that goes quiet across the
                # rollover still rotates. A booking does not depend on whether logs are written.
                self._log_rotator.check_daily_rotation()
                # #320 + #360: drain async responses + check timeouts + re-poll
                # active orders (the fill/cancel-confirm query now fires during idle).
                self._executor.heartbeat()
                # #436: market-data staleness contract — evaluate the last real
                # tick's age BEFORE events/ghost-pass, so the decision sees the
                # stale status (and the edge hook) in this very pass.
                self._evaluate_market_data_staleness()
                # #151 + #360: reconcile on the timer too (was tick-only). Time-bounded
                # by min_interval_seconds → self-throttled, no API storm during idle.
                self._reconcile_if_due(ticks_processed)
                # #141 Part 2a: an envelope that landed between two ticks reaches the
                # decision HERE. process_heartbeat forwards cached worker results by design,
                # so without this the arrival would wait for the next tick — minutes on a
                # quiet instrument, which is exactly what the push channel exists to avoid.
                self._drain_signal_inbox(off_tick=True)
                # #348: deliver events surfaced by the heartbeat drain
                # (idle-time fills/cancels) to the algo hooks — before the ghost-pass
                # so the decision observes them this pass.
                self._drain_decision_events()
                # #360 ghost-pass: let an opt-in decision act between ticks (advance
                # phases, react to drained events, issue follow-up orders) with no
                # synthetic tick and no tick-state mutation.
                ghost_order = self._run_decision_heartbeat(ticks_processed)
                # #354: persist algo state on the idle timer too (hybrid cadence).
                self._persist_state_if_due(ticks_processed)
                # #355: a fill can resolve on the heartbeat, so the book can change here too.
                self._persist_carry_over_if_needed(ticks_processed)
                # #579: the cadence runs on a quiet feed too — a live session's clock is the
                # wall clock here — and an order a ghost pass sent is a moment of its own
                if self._state_frames is not None:
                    self._state_frames.on_pass(
                        ticks_processed, self._last_real_decision,
                        self._safety.get_display_state(), on_tick=False, order=ghost_order)
                if self._executor.is_session_end_requested():
                    self._logger.info(
                        f'🛑 Session end requested: {self._executor.get_session_end_reason()}')
                    break
                self._push_pulse_frame(ticks_processed)
                continue

            # Sentinel value: None = tick source finished
            if tick is None:
                self._logger.info(
                    '📭 Tick source signaled end — ending session')
                break

            # === TIMING START ===
            tick_start_ns = time.perf_counter_ns()

            # Inter-tick delta for clipping detection
            current_msc = tick.collected_msc if tick.collected_msc > 0 else tick.time_msc
            tick_delta_ms = 0.0
            if prev_msc > 0 and current_msc > prev_msc:
                tick_delta_ms = float(current_msc - prev_msc)
            if current_msc > 0:
                prev_msc = current_msc

            ticks_processed += 1

            # #141 Part 2a: fold in whatever the signal transport received since the last
            # pass, BEFORE the workers run — the existing should_refresh picks a new snapshot
            # up exactly as it picks up a mounted one, so tick behaviour and its counters are
            # untouched.
            self._drain_signal_inbox(off_tick=False)

            # === 0. Trading-day boundary (#476) — BEFORE the tick is applied ===
            # A day ends at its boundary: the seal reads the account as the previous pass left
            # it and is stamped with the boundary, so this tick's fills, trades and valuation
            # open the new day — the same cut the heartbeat makes, and the one the simulation
            # makes. The daily loss limit rolls on this seal too, from the value it read. The
            # canonical clock moves to the tick's instant first — forward only, so a clock a
            # heartbeat set ahead of the venue's stamp stays where it is, which is the instant
            # on_tick applies the tick at — and the boundary is checked on it.
            self._executor.set_current_time(tick.timestamp)
            self._booking.check_boundary(
                self._executor.get_current_time_if_set(), self._seal_source)
            self._close_sealed_days()

            # === DAILY LOG ROTATION ===
            # On the booking period's day, which the check above advanced — after it, so the
            # line that sealed the day stays in that day's file, and before the tick, so what the
            # tick logs (a fill, a triggered stop) is in the file of the day it is booked to. In a
            # live-adapter session the heartbeat has almost always rotated first; this covers a
            # replay whose queue never runs empty.
            self._log_rotator.check_daily_rotation()

            # === 1. Trade Executor — BROKER PATH (all ticks) ===
            self._executor.on_tick(tick)
            if self._first_tick_time is None:
                self._first_tick_time = tick.timestamp
            self._last_tick_time = tick.timestamp

            # #436: a real tick ends a stale episode (recovery, edge reset).
            if self._market_stale:
                self._end_market_stale_episode()
            elif self._last_real_tick_wall_time <= 0 and self._market_data_alert.is_open():
                # #235: the first tick ends the wait for it the operator was told about
                self._market_data_alert.clears(self._executor.get_current_time())
            if self._market_data_alert.is_open():
                self._send_market_data_all_clear_if_due()

            # #444: advance the planned stale-window state machine AFTER that recovery and
            # BEFORE the tracker observes — the wall-clock episode resolves itself first,
            # then an injected window re-states its claim, and the tracker sees the status
            # both of them left behind. Ticks keep flowing inside a window by design.
            if self._stale_stress_driver is not None:
                self._stale_stress_driver.on_tick(self._executor.get_current_time())

            # #451: observe the resulting status — counts this tick and closes the
            # episode record from what the session actually experienced.
            self._market_data_tracker.on_tick(
                self._executor.get_current_time(),
                self._executor.get_market_data_status(),
                self._injected_outage_label(),
            )

            # Capture spot equity baseline on first tick (first live price available)
            if ticks_processed == 1 and self._trading_model == TradingModel.SPOT:
                first_price = tick.mid
                self._initial_spot_equity = self._executor.portfolio.get_spot_equity(first_price)
            # #356 — the RISK baseline, which is a different question and answers it once:
            # `ensure_taken` does nothing when a record was restored from the predecessor, so
            # a restart mid-drawdown keeps the reference it had instead of re-anchoring at
            # the drawn-down value. `observe` then advances a high-water mark, and only that.
            # The booking period observes the same value; its boundary was checked above,
            # before this tick moved the account (#476).
            self._observe_account_value(tick)

            # === 2. Bar Rendering (shared core, #303) ===
            current_bars = render_bars_for_tick(tick, self._bar_controller)
            # #579: read before the algo pass below consumes them — a state frame names the bar
            # the decision just saw close
            closed_timeframes = (self._bar_controller.get_pending_closed_timeframes()
                                 if self._state_frames is not None else frozenset())

            # === 3+4. Bar History + Worker Processing + Decision (shared core, #303) ===
            decision = execute_algo_path(
                tick=tick,
                current_bars=current_bars,
                bar_controller=self._bar_controller,
                worker_orchestrator=self._worker_orchestrator,
                symbol=self._config.symbol,
            )

            # === 4b. New-max debug logging ===
            self._check_new_maxes()

            # === 5. Safety Check (circuit breaker) ===
            # ONE value definition, the same one the drawdown series uses (#356/#492). The
            # margin branch used to read `get_balance()` — settled cash, which by
            # construction moves only on REALISED P&L, so an open drawdown was invisible to
            # the breaker of the account model that can lose more than it holds. None means
            # the holdings cannot be valued yet; the breaker then does not measure rather
            # than measuring against a guess.
            safety_value = self._executor.portfolio.get_account_value()
            safety_baseline = self._safety.baseline_value()
            if safety_value is not None:
                # #497 — the REPORT's drawdown reads the same per-tick value the breaker
                # does, and pays nothing for it: the evaluation above is already done. The
                # series was otherwise written only when a position CLOSED, which measures
                # the largest decline across closed trades rather than the largest decline
                # of the equity curve. Two different measures; only the second is what the
                # word means. (#366 will add the sim's own per-tick pass — it rides this
                # same seam rather than opening a second one.)
                self._executor.portfolio.sample_equity(safety_value)

                # Record the excursion BEFORE the checks and from the same two values they
                # read. A report measuring a different number from the one that decides is
                # the defect this issue removes, one layer up.
                self._safety.observe_excursion(safety_value, safety_baseline)
                self._safety.check_safety(safety_value, safety_baseline)
                # #356 — the HARD stop, checked after the soft one so a session that trips
                # both reports both. Sends the closes ONCE; the drain below carries them.
                self._safety.check_emergency_flatten(
                    safety_value, safety_baseline, ticks_processed)
            self._safety.drive_emergency_flatten(ticks_processed)

            # === 6. Order Execution ===
            if self._safety.entries_blocked():
                decision.action = DecisionLogicAction.FLAT
            order_result = self._decision_logic.execute_decision(decision, tick)
            self._record_rejection(order_result, decision)

            # === 6b. Decision event drain (#348) ===
            # Events buffered during on_tick (fills, partial closes, cancels)
            # are delivered to the algo hooks here — after compute/execute and
            # before the next tick. A hook may request session end.
            self._drain_decision_events()
            if self._executor.is_session_end_requested():
                self._logger.info(
                    f'🛑 Session end requested: {self._executor.get_session_end_reason()}')
                break

            # === 6c. Reconciliation (#151, hybrid cadence, ALERT_ONLY) ===
            # Broker truth-pull every N ticks OR M seconds. Sync (like the #320
            # polling path); infrequent, so the periodic block is bounded.
            self._reconcile_if_due(ticks_processed)

            # === 6d. Algo State Persistence (#354, hybrid cadence) ===
            # Restart-safe algo memory (Category B). Save every N ticks OR M seconds.
            self._persist_state_if_due(ticks_processed)

            # === 6e. Position Book Carry-Over (#355, on change) ===
            # A spot position only survives a restart if we wrote it down. Structural changes
            # write at once; exit levels and extrema wait for the tick cadence (§42 — one
            # write costs 11 ms on this tree).
            self._persist_carry_over_if_needed(ticks_processed)

            # === TIMING END ===
            elapsed_ns = time.perf_counter_ns() - tick_start_ns

            # === 7. Clipping Monitor ===
            self._clipping_monitor.record_tick(elapsed_ns, tick_delta_ms)
            self._clipping_monitor.record_queue_depth(self._tick_queue.qsize())

            # Periodic clipping report
            report = self._clipping_monitor.get_periodic_report()
            if report is not None:
                self._logger.info(
                    f'📊 Clipping report: {report.interval_ticks} ticks, '
                    f'{report.interval_clipped} clipped, '
                    f'avg {report.interval_avg_processing_ms:.2f}ms, '
                    f'max {report.interval_max_processing_ms:.2f}ms, '
                    f'queue_depth_max={report.interval_max_queue_depth}'
                )
                ticks_clipped += report.interval_clipped

            # === 8. Display Stats ===
            if self._display_queue is not None:
                display_stats = self._display_exporter.build(
                    decision, ticks_processed, tick,
                    safety=self._safety.get_display_state(),
                    recent_rejections=list(self._recent_rejections),
                    total_rejections=self._rejection_count,
                    initial_spot_equity=self._initial_spot_equity)
                try:
                    self._display_queue.put_nowait(display_stats)
                except queue.Full:
                    pass  # Display will use last known state

            # === 9. State Frame (#579 part A) — on its cadence, at a closed bar, at an order ===
            # After the timing above: recording is not the algo being slow
            if self._state_frames is not None:
                self._state_frames.on_pass(
                    ticks_processed, decision, self._safety.get_display_state(), on_tick=True,
                    closed_timeframes=closed_timeframes, order=order_result)

            # Track last valid tick/decision for the final shutdown snapshot
            # and heartbeat pulse frames (#320).
            last_tick = tick
            last_decision = decision
            self._last_real_tick = tick
            self._last_real_decision = decision
            self._last_real_tick_wall_time = time.time()

        # === Session end event (#348) — emitted once the loop ends, whether by
        # request, tick-source exhaustion, or stop(). Delivered before teardown.
        if self._decision_event_dispatcher is not None:
            self._decision_event_dispatcher.emit_session_end()

        # Final display snapshot — ensures the last rendered frame reflects
        # the terminal pipeline state regardless of display refresh timing.
        # The queue (maxsize=10) is likely full from high-speed tick replay,
        # so drain stale snapshots first to guarantee the final one lands.
        if (self._display_queue is not None
                and last_tick is not None
                and last_decision is not None):
            while not self._display_queue.empty():
                try:
                    self._display_queue.get_nowait()
                except queue.Empty:
                    break
            final_stats = self._display_exporter.build(
                last_decision, ticks_processed, last_tick,
                safety=self._safety.get_display_state(),
                recent_rejections=list(self._recent_rejections),
                total_rejections=self._rejection_count,
                initial_spot_equity=self._initial_spot_equity)
            try:
                self._display_queue.put(final_stats, timeout=1.0)
            except queue.Full:
                pass

        # #360 ghost-pass observability — proves the idle decision pass fired
        # (and acted) over the session. Machine-parseable.
        self._logger.info(
            f'[GHOST] ghost_passes={self._ghost_pass_count} '
            f'ghost_actions={self._ghost_action_count} ticks={ticks_processed}')

        # #444: close a window still active at session end, so the episode span reaches the
        # report even without a recovery tick. Same step the sim takes at scenario end.
        if self._stale_stress_driver is not None:
            self._stale_stress_driver.finish()

        self._running = False
        return ticks_processed, ticks_clipped

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
        self._state_frames.write_shutdown(
            ticks_processed, self._last_real_decision, self._safety.get_display_state())

    def _drain_signal_inbox(self, off_tick: bool) -> None:
        """
        Fold newly received signal envelopes into the series (#141 Part 2a).

        Two shapes, because the two loop paths need different things. On a TICK the merge is
        enough — the worker pass that follows picks the new snapshot up itself. On the
        HEARTBEAT nothing else would run, so the workers are refreshed and the shared signal
        pass fires the stale edge and the outage episode at the arrival moment.

        A session that mounted its series has no inbox, so this returns immediately and the
        loop is unchanged — which is what keeps a replay reproducible.

        Args:
            off_tick: True on the heartbeat path (refresh + signal pass), False on a tick
        """
        if self._signal_inbox is None:
            return
        arrivals = self._signal_inbox.drain()
        if not arrivals:
            return

        if not off_tick:
            self._worker_orchestrator.merge_signal_arrivals(arrivals)
            return

        merged = self._worker_orchestrator.process_signal_arrivals(
            arrivals, self._executor.get_current_time())
        if merged:
            self._logger.debug(
                f'📡 {merged} signal envelope(s) arrived between ticks')

    def _drain_decision_events(self) -> None:
        """Drain buffered decision events to the algo hooks, if a dispatcher is active (#348)."""
        if self._decision_event_dispatcher is not None:
            self._decision_event_dispatcher.drain()

    def _record_rejection(self, order_result: Optional[OrderResult], decision: Decision) -> None:
        """
        Record a rejected order into the rolling rejection buffer (display + count).

        Args:
            order_result: Result of the executed decision (may be None / non-rejected)
            decision: The decision that produced the order (for the side label)
        """
        if not (order_result and order_result.is_refused):
            return
        reason = order_result.rejection_reason.value if order_result.rejection_reason else 'unknown'
        self._rejection_count += 1
        self._recent_rejections.append(RejectionEntry(
            seq=self._rejection_count,
            reason=reason,
            message=order_result.rejection_message or '',
            side=decision.action.value,
            tick_time=self._executor.get_current_time(),
        ))

    def _reconcile_if_due(self, ticks_processed: int) -> None:
        """
        Pull broker truth if the reconcile cadence is due, and act on what came back.

        The Reconciler only ever reads (ALERT_ONLY). The one outcome that carries a write
        is an ATTRIBUTION (#355): a resting broker order carrying this session's client
        order id belongs to a local pending whose submit answer was lost, so the venue's
        reference is handed to the executor — the owner of that state — which puts the
        order back into the poll path. Everything else the cycle found is reported by the
        Reconciler itself; correction is #349. A cycle whose picture changed is written to the
        order-event stream as a broker-truth record — by the executor, which numbers the
        stream (#362).

        Args:
            ticks_processed: Current tick counter (drives the hybrid cadence)
        """
        if self._reconciler is None or not self._reconciler.is_due(ticks_processed):
            return
        result = self._reconciler.reconcile(ticks_processed)
        if result.attributed_orders:
            self._executor.apply_order_attributions(result.attributed_orders)
        if result.broker_truth_due:
            self._executor.record_reconcile_truth(result)

    def _persist_state_if_due(self, ticks_processed: int) -> None:
        """
        Save the algo state snapshot if the persistence cadence is due (#354).

        No-op when no state store is wired (algo did not opt in). Mid-session save
        failures are logged (error pot, §35) and swallowed — a persistence problem
        must never abort an AutoTrader session.

        Args:
            ticks_processed: Current tick counter (drives the hybrid cadence)
        """
        if self._state_store is None or not self._state_store.is_due(ticks_processed):
            return
        try:
            self._state_store.save(
                self._decision_logic.get_state_snapshot(), ticks_processed)
        except Exception as e:
            self._logger.error(f'Algo state save failed (continuing): {e}')

    def _persist_carry_over_if_needed(self, ticks_processed: int = 0) -> None:
        """
        Write the framework carry-over when it has fallen behind (#355 / #356).

        Two things can make it stale, and they are checked together because they share one
        document: the open POSITION BOOK (#355) and the risk BASELINE (#356). A session that
        never opens a position still has a denominator worth carrying across a restart, which
        is why the baseline has its own trigger rather than riding along with the book.

        The watcher is advanced ONLY after a write that reported success. A watcher advanced
        on the query would drop the trigger for good whenever a write failed: the change is
        reported once, to a caller that could not act on it, and the position is then missing
        from the note until something else happens to move the book.

        No-op when nothing is wired (simulation, or a session without a carry-over store).
        The write itself logs its own failures — a carry-over problem must never end an
        AutoTrader session.

        Args:
            ticks_processed: Current tick counter — it drives the drift cadence
        """
        if self._persist_carry_over is None:
            return

        positions = self._executor.portfolio.get_open_positions()
        drift_due = self._book_drift_is_due(ticks_processed)
        if not (self._book_watcher.has_changed(positions, drift_due=drift_due)
                or self._baseline_needs_writing(drift_due)):
            return

        if self._persist_carry_over():
            self._book_watcher.accept(positions)
            self._persisted_baseline = (
                self._risk_baseline.get_baseline()
                if self._risk_baseline is not None else None)
            if drift_due:
                self._last_book_drift_tick = ticks_processed

    def _baseline_needs_writing(self, drift_due: bool) -> bool:
        """
        Whether the risk baseline on disk is behind the one in memory (#356).

        Two cases, and the first is the one a hard kill exposes. A baseline TAKEN this
        session has never reached the disk: the boot write ran before the first tick, when
        there was none to write, and the only other in-run trigger is a structural change of
        the POSITION BOOK. So a session that stays flat — or any margin session, whose book
        is deliberately not written at all — would carry the record to disk for the first
        time at SHUTDOWN, which is exactly the moment a hard kill does not reach. The
        successor would then re-anchor at the drawn-down value, which is the drift this
        whole issue exists to remove.

        The second case is a high-water mark that has ADVANCED, and it waits for the drift
        window rather than writing on every new peak: a trending market makes a new peak on a
        great many ticks and one write costs 11 ms on this tree (§42). Writing that one late
        is safe in a way writing the first one late is not — a stale, LOWER peak is a looser
        limit, never a tighter one.

        Args:
            drift_due: Whether the cadence window for the slow-moving values is open

        Returns:
            True when the record should go to disk now
        """
        if self._risk_baseline is None:
            return False
        baseline = self._risk_baseline.get_baseline()
        if baseline is None:
            return False
        if self._persisted_baseline is None:
            return True
        return drift_due and baseline != self._persisted_baseline

    def _book_drift_is_due(self, ticks_processed: int) -> bool:
        """
        Whether the cadence window for exit levels and excursion extrema is open.

        Counted in ticks, not seconds: drift is caused by ticks, so a quiet market needs no
        writes — and a tick counter needs no clock, which matters because the first passes
        happen before the canonical clock is injected.

        Args:
            ticks_processed: Current tick counter

        Returns:
            True when the interval has elapsed since the last drift write
        """
        if self._book_drift_interval_ticks <= 0:
            return False

        return ticks_processed - self._last_book_drift_tick >= self._book_drift_interval_ticks

    def _run_decision_heartbeat(self, ticks_processed: int) -> Optional[OrderResult]:
        """
        #360 ghost-pass: run an opt-in decision between ticks without a synthetic tick.

        Workers do not recompute — the orchestrator serves their cached results. The
        decision runs with tick=None so it can advance internal state (e.g. field-study
        phases), react to drained #348 events, and issue follow-up orders (re-arm /
        cancel / confirm). No tick state is mutated (no counter, no mark_dirty, no bar
        render). Safety reuses the last evaluated block state (no fresh price on idle).

        Args:
            ticks_processed: Current tick counter (unchanged on a ghost-pass)

        Returns:
            What the decision sent, or None when it sent nothing or did not run
        """
        decision = run_ghost_pass(self._worker_orchestrator)
        if decision is None:
            return None
        self._ghost_pass_count += 1
        if self._safety.entries_blocked():
            decision.action = DecisionLogicAction.FLAT
        order_result = self._decision_logic.execute_decision(decision, tick=None)
        if order_result is not None:
            self._ghost_action_count += 1
        self._record_rejection(order_result, decision)
        return order_result

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

        if self._last_real_tick is None or self._last_real_decision is None:
            seconds_since = (datetime.now(timezone.utc) - self._session_start).total_seconds()
            stats = self._display_exporter.build_startup_pulse(seconds_since)
        else:
            seconds_since = time.time() - self._last_real_tick_wall_time
            stats = self._display_exporter.build(
                self._last_real_decision, ticks_processed, self._last_real_tick,
                safety=self._safety.get_display_state(),
                recent_rejections=list(self._recent_rejections),
                total_rejections=self._rejection_count,
                initial_spot_equity=self._initial_spot_equity)
            stats.is_pulse = True
            stats.seconds_since_last_tick = seconds_since

        try:
            self._display_queue.put_nowait(stats)
        except queue.Full:
            pass  # Display will use last known state

    def _evaluate_market_data_staleness(self) -> None:
        """
        Evaluate the session-level market-data staleness contract (#436).

        Runs on the idle heartbeat. Wall-clock is valid here as a DURATION
        measurement (elapsed since the last real tick arrived); episode
        RECORDS (stale_since, log lines) are stamped from the canonical
        clock. Edge-triggered: pot warning + on_market_data_stale fire once
        per fresh→stale episode; recovery happens on the tick path. While
        stale, the readable status stays current (escalation input for
        heartbeat-driven logics).
        """
        self._note_reconnects()

        if self._market_data_stale_after_s <= 0:
            return
        # #444: an injected window owns the status while it runs. Both sources write the
        # SAME field, so without this the wall clock could overwrite a deterministic window
        # — and it would say nothing new: the status is already stale and the edge hook has
        # already fired. When the window ends with the feed still quiet, the next heartbeat
        # evaluates normally and the real outage is reported then.
        if (self._stale_stress_driver is not None
                and self._stale_stress_driver.get_active_label()):
            return
        if self._last_real_tick_wall_time <= 0:
            # No tick yet — a startup wait for the status, not an outage. The operator still
            # hears when it outlasts the threshold (#235)
            self._alert_on_missing_first_tick()
            return
        seconds_since = time.time() - self._last_real_tick_wall_time
        if seconds_since <= self._market_data_stale_after_s:
            return

        flipped = not self._market_stale
        if flipped:
            self._market_stale = True
            self._market_stale_since = self._executor.get_current_time()
        status = MarketDataStatus(
            is_stale=True,
            stale_since=self._market_stale_since,
            seconds_since_last_tick=seconds_since,
            reconnect_count=self._last_reconnect_count,
        )
        self._executor.set_market_data_status(status)
        # #451: the episode opens here — the observer stamps it back to the last tick
        # the session still saw as fresh, not to the pass that revealed the silence.
        self._market_data_tracker.on_heartbeat(
            self._executor.get_current_time(), status, self._injected_outage_label())
        if flipped:
            # One episode, two instants: the last tick is when the feed fell silent — the start
            # the episode record and its recovery line count from — and the flip is when the
            # threshold revealed the silence and entries were blocked. Both are named.
            last_tick = self._last_tick_time or self._market_stale_since
            self._logger.warning(
                f"⚠️ Market data stale since "
                f"{self._market_stale_since.strftime('%H:%M:%S')}: no tick since "
                f"{last_tick.strftime('%H:%M:%S')} ({seconds_since:.0f}s, threshold "
                f"{self._market_data_stale_after_s:.0f}s) — entries guard-blocked"
            )
            if self._market_data_alert.opens(self._executor.get_current_time()):
                self._operator_alerts.send(
                    OperatorAlertKind.MARKET_DATA_STALE, OperatorAlertLevel.WARNING,
                    'Market data stale — new entries blocked',
                    f"No tick since {last_tick.strftime('%H:%M:%S')} UTC "
                    f'({seconds_since:.0f}s, threshold {self._market_data_stale_after_s:.0f}s) '
                    f"— stale, new entries blocked since "
                    f"{self._market_stale_since.strftime('%H:%M:%S')} UTC")
            self._decision_logic.on_market_data_stale(status)

    def _alert_on_missing_first_tick(self) -> None:
        """
        #235: tell the operator, once, when no first tick has come within the stale threshold.

        A feed that never delivers its first tick — a socket down at a restart while the REST
        boot went through — would otherwise leave "Session started" as the operator's last word
        while the ping stays green, because the idle passes keep the loop alive. Only the alert:
        the status stays fresh until a first tick exists to measure from (#436). The first tick
        ends the episode, and its all-clear follows the same quiet period as any other.
        """
        if self._market_data_alert.is_open() or self._loop_started_monotonic <= 0:
            return
        waited = time.monotonic() - self._loop_started_monotonic
        if waited <= self._market_data_stale_after_s:
            return
        self._logger.warning(
            f'⚠️ No market data yet: no tick for {waited:.0f}s since the tick loop started '
            f'(threshold {self._market_data_stale_after_s:.0f}s) — the feed may not be connected')
        if self._market_data_alert.opens(self._executor.get_current_time()):
            self._operator_alerts.send(
                OperatorAlertKind.MARKET_DATA_STALE, OperatorAlertLevel.WARNING,
                'No market data since the session started',
                f'No tick for {waited:.0f}s since the tick loop started (threshold '
                f'{self._market_data_stale_after_s:.0f}s). The feed may not be connected.')

    def _note_reconnects(self) -> None:
        """
        Surface the outages the tick stream came back from to the session pot, and keep the count
        the market-data status carries current (typed connection events are #375/#331).

        The source counts an outage when the first trade after it arrives (#609) — the very tick
        that ends a stale episode — so the recovery reads the count here too, not only the idle
        heartbeat; otherwise the status after a recovery would miss the outage that just ended.
        """
        if self._tick_source is None:
            return
        reconnects = self._tick_source.get_reconnect_count()
        if reconnects > self._last_reconnect_count:
            self._logger.warning(
                f'🔌 Tick stream reconnected '
                f'(+{reconnects - self._last_reconnect_count}, total {reconnects})')
            self._last_reconnect_count = reconnects

    def _end_market_stale_episode(self) -> None:
        """
        Close a stale episode on tick resumption (#436): fresh status + edge reset.

        The episode RECORD and its pot line come from the MarketDataEpisodeTracker
        (#451), which derives both from the observed status change — so a real and an
        injected outage travel the exact same path into the report.
        """
        self._note_reconnects()
        self._market_stale = False
        self._market_stale_since = None
        self._executor.set_market_data_status(MarketDataStatus(
            reconnect_count=self._last_reconnect_count))
        # #235: the all-clear waits until the data has stayed fresh for the quiet period
        self._market_data_alert.clears(self._executor.get_current_time())

    def _send_market_data_all_clear_if_due(self) -> None:
        """#235: the market data's all-clear, once it has stayed fresh for the quiet period."""
        if self._market_data_alert.all_clear_due(self._executor.get_current_time()):
            self._operator_alerts.send(
                OperatorAlertKind.MARKET_DATA_RECOVERED, OperatorAlertLevel.INFO,
                'Market data fresh again',
                all_clear_detail(self._market_data_alert))

    def _injected_outage_label(self) -> str:
        """
        Outage label of a deliberately injected silence — planned window or tick source.

        Returns:
            The label of whichever injection is running, '' when the silence is real
        """
        # The planned window first: it is the deterministic one, and the two cannot both
        # be running for the same reason. Without it an injected outage would be recorded
        # as a REAL one — the very confusion the label exists to prevent (#451).
        if self._stale_stress_driver is not None:
            planned = self._stale_stress_driver.get_active_label()
            if planned:
                return planned
        if self._tick_source is None:
            return ''
        return self._tick_source.get_injected_outage_label()

    def get_disturbance_episodes(self) -> List[DisturbanceEpisode]:
        """
        The session's observed market-data outage episodes (#451).

        Returns:
            List of DisturbanceEpisode (an episode still open is never-recovered)
        """
        return self._market_data_tracker.get_episodes(self._executor.get_current_time())

    def has_opened_day_log(self) -> bool:
        """
        Whether a pass of this loop has opened a trading day's session log.

        Returns:
            True once the first day's log is open
        """
        return self._log_rotator.has_opened_day_log()

    def get_tick_span(self) -> Tuple[Optional[datetime], Optional[datetime]]:
        """
        The first and the latest tick this session processed.

        Returns:
            (first, last) tick timestamps; (None, None) before the first tick
        """
        return self._first_tick_time, self._last_tick_time

    def get_market_data_tick_stats(self) -> MarketDataTickStats:
        """
        The session's market-data tick counters (#451 Part 4).

        Returns:
            MarketDataTickStats for the session's tick source
        """
        return self._market_data_tracker.get_tick_stats()

    def _check_new_maxes(self) -> None:
        """Log new all-time max execution times for workers and decision logic."""
        for name, worker in self._worker_orchestrator.workers.items():
            if not worker.performance_logger:
                continue
            current_max = worker.performance_logger.get_stats().worker_max_time_ms
            prev_max = self._known_worker_maxes.get(name, 0.0)
            if current_max > prev_max:
                self._known_worker_maxes[name] = current_max
                self._logger.debug(
                    f'NEW MAX: {name:<16s} {current_max:.2f}ms  (prev: {prev_max:.2f}ms)'
                )

        if self._decision_logic.performance_logger:
            current_max = self._decision_logic.performance_logger.get_stats().decision_max_time_ms
            if current_max > self._known_decision_max:
                prev = self._known_decision_max
                self._known_decision_max = current_max
                dl_type = self._config.strategy_config.get('decision_logic_type', '')
                dl_label = dl_type.split('/')[-1] if dl_type else 'decision'
                self._logger.debug(
                    f'NEW MAX: {dl_label:<16s} {current_max:.2f}ms  (prev: {prev:.2f}ms)'
                )

    def _observe_account_value(self, tick: TickData) -> None:
        """
        Value the account once for this tick, and hand the value to everything that tracks it.

        The booking period counts the tick and observes the value. The risk baseline is taken if
        the session has none, and a high-water mark advances. Both calls are cheap and both are
        no-ops in the ordinary case — `ensure_taken` after the first tick, `observe` for every
        mode except HIGH_WATER_MARK. They run on every tick rather than on a cadence because a
        peak missed is a peak lost: the record is a running maximum, and sampling it would make
        the limit depend on when we happened to look.

        Args:
            tick: The tick whose price values the holdings
        """
        value = self._executor.portfolio.get_account_value()
        # The booking period's VALUATION half (#537), and its tick count — the tick counts even
        # when the holdings cannot be valued yet, because the market was heard (#476)
        self._booking.observe_tick(value)
        if self._risk_baseline is None or value is None:
            return

        mark_price: Optional[float] = None
        quantities: Optional[BaselineQuantities] = None
        if self._trading_model == TradingModel.SPOT:
            mark_price = tick.mid
            quantities = self._spot_quantities()

        self._risk_baseline.ensure_taken(value, mark_price, quantities)
        self._risk_baseline.observe(value, mark_price, quantities)
        # The day's baseline is struck where the day rolled — at the seal, from the value it
        # read; this takes the first day's, and a day's whose seal could not value the account
        self._safety.ensure_day_baseline(value, mark_price, quantities)
        # The report's equity curve too: a seal reads the curve's running figures (peak, deepest
        # decline), and the simulation samples on every tick as well. The safety pass samples
        # again after the decision, which changes nothing unless an order filled within this tick.
        self._executor.portfolio.sample_equity(value)

    def _trading_day(self) -> Optional[date]:
        """
        The trading day the session runs on — the one its open booking period books (#476).

        The log rotation and the daily loss limit read it here, so all three agree on where a
        day ends, and on which days a market that closes has at all.

        Returns:
            The trading day's date, or None before the first period opened
        """
        return self._booking.get_current_day()

    def _seal_source(self):
        """
        The records and the stock reading a seal needs, at this instant.

        Handed to the recorder as a CALLABLE rather than as values: a boundary check runs on
        every pass and almost never seals, so the portfolio is only read when a period actually
        closes.

        Returns:
            (trade records, PeriodSnapshot)
        """
        return snapshot_from_portfolio(self._executor.portfolio)

    def get_highest_period_no(self) -> int:
        """
        The largest booking period this session has sealed (#537).

        Read by the carry-over so the next session continues the count.

        Returns:
            The high-water mark, which is the inherited floor when nothing was sealed
        """
        return self._booking.get_highest_period_no()

    def _on_period_sealed(self, period: BookingPeriod) -> None:
        """
        What one sealed booking period sets off in the session, the moment it is sealed.

        At a trading day's boundary the daily loss limit's day rolls on the same seal (#476) —
        per day, because each day keeps its own record. What can wait for the end of the check
        is collected for `_close_sealed_days`. The session's end has its own closing message and
        no day after it.

        Args:
            period: The booking period just sealed
        """
        if period.reason is not PeriodCloseReason.ANCHOR:
            return
        self._roll_safety_day(period)
        self._sealed_in_check.append(period)

    def _close_sealed_days(self) -> None:
        """
        What the trading days one boundary check sealed set off, once for all of them (#476).

        The days are booked into the ledger and their number into the carry-over, the log buffer
        lets go of their ordinary lines, and the operator hears how the day closed (#235). Once
        and not per day: a check several days late — a machine that slept, a replay whose clock
        jumped to the present (#616) — seals one period per day, and each would otherwise
        rewrite the whole ledger fragment and send its own message within one pass.
        """
        if not self._sealed_in_check:
            return
        sealed, self._sealed_in_check = self._sealed_in_check, []
        self._book_sealed_day()
        # The days' lines are in their log files; the end reads only warnings and errors from
        # the buffer, so the rest of the days leave memory here
        self._logger.trim_buffer_below(LogLevel.WARNING)
        self._announce_day_closed(sealed[0])
        if len(sealed) > 1:
            self._announce_days_closed_unheard(sealed[1:])

    def _book_sealed_day(self) -> None:
        """
        Make a sealed trading day survive the session (#476): its ledger rows, then the period
        number the carry-over hands the next session.

        In that order, the order the recorder itself keeps: killed between the two, the next
        session books the same number again — a visible duplicate — where the other order would
        leave a number nobody booked.
        """
        if self._book_sealed_periods is not None:
            self._book_sealed_periods(self._booking.get_sealed_periods())
        if self._persist_carry_over is not None and self._persist_carry_over():
            # The write carried the book and the baseline as they stand, so neither is behind
            self._book_watcher.accept(self._executor.portfolio.get_open_positions())
            self._persisted_baseline = (
                self._risk_baseline.get_baseline() if self._risk_baseline is not None else None)

    def _roll_safety_day(self, period: BookingPeriod) -> None:
        """
        Roll the daily loss limit's day at the boundary that sealed the period (#476).

        The new day's baseline is the value the seal read — where the account stood at the
        boundary — so whatever the first tick of the day does counts against the new day's
        limit, a weekend's gap on a forex open included. At spot it is valued at the price the
        seal valued it at, the last tick before the boundary.

        Args:
            period: The period the boundary sealed
        """
        value = period.figures.final_equity
        mark_price: Optional[float] = None
        quantities: Optional[BaselineQuantities] = None
        if (self._trading_model == TradingModel.SPOT and value is not None
                and self._last_real_tick is not None):
            mark_price = self._last_real_tick.mid
            quantities = self._spot_quantities()
        self._safety.roll_day(period.closed_at, value, mark_price, quantities)

    def _announce_days_closed_unheard(self, periods: List[BookingPeriod]) -> None:
        """
        #235: tell the operator, in one message, about the trading days a late check sealed after
        the first — days with no market data, each booked on its own.

        Args:
            periods: Those days' booking periods, oldest first
        """
        if not self._daily_digest:
            return
        try:
            title, detail = days_closed_unheard_text(periods)
        except Exception as error:   # noqa: BLE001 — a message must never end a trading session
            self._logger.error(
                f'📣 The message for {len(periods)} trading days closed unheard could not be '
                f'written ({type(error).__name__}: {error})')
            return
        self._operator_alerts.send(
            OperatorAlertKind.DAY_CLOSED, OperatorAlertLevel.INFO, title, detail)

    def _announce_day_closed(self, period: BookingPeriod) -> None:
        """
        #235: tell the operator how the trading day closed — the period its boundary just sealed.

        A second distribution of that period: the session log already carries its full line and
        the ledger books it; this renders the same figures for the operator. Only a trading-day
        boundary is announced — the session's end has its own closing message. A message that
        cannot be written is said in the session log and never ends the session.

        Args:
            period: The booking period just sealed
        """
        if period.reason is not PeriodCloseReason.ANCHOR or not self._daily_digest:
            return
        try:
            # The day the period booked, as the recorder knew it — after a market's closed days
            # the period opened where the last day ended, so its opening names no day
            trading_day = period.trading_day
            title, detail = day_closed_text(
                period, trading_day, boundary_opening(trading_day, self._day_anchor))
        except Exception as error:   # noqa: BLE001 — a message must never end a trading session
            self._logger.error(
                f'📣 The day-close message for booking period {period.period_no} could not be '
                f'written ({type(error).__name__}: {error})')
            return
        self._operator_alerts.send(
            OperatorAlertKind.DAY_CLOSED, OperatorAlertLevel.INFO, title, detail)

    def get_booking_periods(self) -> List[BookingPeriod]:
        """
        This session's ledger entries — one per closed booking period (#537).

        Seals the period that was still running, so the final and necessarily incomplete one is
        filed like every other rather than dropped for having no successor. Exactly what
        `get_safety_session` does for the day records, and for the same reason.

        Returns:
            The periods, oldest first
        """
        return self._booking.close(
            self._executor.get_current_time_if_set(), self._seal_source)

    def get_safety_session(self) -> SafetySessionRecord:
        """
        What the circuit breaker saw over this session, for the report (#356 / #314).

        Returns:
            The breaker's record; its baseline is None when none was ever taken
        """
        return self._safety.get_safety_session()

    def _spot_quantities(self) -> BaselineQuantities:
        """
        What the account holds right now, split into quote and everything else.

        Carried on a spot baseline so its value can be RE-DERIVED rather than trusted:
        `value == quote + base * mark_price`. The split mirrors `get_spot_equity`, which
        values every non-account-currency balance at the same one price — so anything that
        is not the quote currency belongs to `base`, and on a single-symbol bot that is the
        traded asset.

        Returns:
            The quote balance and the base holding
        """
        portfolio = self._executor.portfolio
        balances = portfolio.get_balances()
        account_currency = portfolio.account_currency
        return BaselineQuantities(
            quote=balances.get(account_currency, 0.0),
            base=sum(amount for currency, amount in balances.items()
                     if currency != account_currency),
        )
