"""
FiniexTestingIDE - AutoTrader Tick Loop
Main tick processing loop for live trading (Threading model 8.a).

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

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.market_config_manager import MarketConfigManager
from python.framework.autotrader.autotrader_display_exporter import AutotraderDisplayExporter
from python.framework.autotrader.autotrader_startup import create_session_file_logger
from python.framework.autotrader.session_log_retention import prune_rotated_session_logs
from python.framework.autotrader.live_clipping_monitor import LiveClippingMonitor
from python.framework.autotrader.risk_baseline_tracker import RiskBaselineTracker
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
from python.framework.types.autotrader_types.autotrader_display_types import (
    RejectionEntry,
    SafetyState,
)
from python.framework.types.autotrader_types.display_label_cache import DisplayLabelCache
from python.framework.types.autotrader_types.safety_session_types import (
    SafetyDayRecord,
    SafetySessionRecord,
)
from python.framework.types.config_types.market_config_types import TradingModel
from python.framework.types.decision_event_types import SessionEndEvent, SessionEndSeverity
from python.framework.types.decision_logic_types import Decision, DecisionLogicAction
from python.framework.types.disturbance_episode_types import DisturbanceEpisode, MarketDataTickStats
from python.framework.types.market_types.market_data_types import TickData
from python.framework.reporting.booking_segment_recorder import (
    BookingSegmentRecorder,
    snapshot_from_portfolio,
)
from python.framework.types.run_results_types import BookingSegment
from python.framework.utils.trading_day_anchor import trading_day_of
from python.framework.types.persistence_types import (
    BaselineKind,
    BaselineOrigin,
    BaselineQuantities,
)
from python.framework.types.trading_env_types.market_data_status_types import MarketDataStatus
from python.framework.types.trading_env_types.order_types import OrderResult
from python.framework.workers.worker_orchestrator import WorkerOrchestrator

# How many ticks the hard stop keeps draining before it ends the session with positions
# still open (#356). A close is asynchronous: it is sent on one tick and its fill arrives on
# a later one, so ending immediately would report a flat book that is not flat. The window
# is generous because the alternative — exiting early — is the failure it exists to prevent,
# and it is BOUNDED because a venue that never answers must not hold the session open for
# the rest of the month. What happens at the end of it is not a silent exit: every position
# still open is named into the error pot (§35).
_FLATTEN_DRAIN_TICKS = 200


class AutotraderTickLoop:
    """
    Tick processing loop for live trading.

    Pulls ticks from a queue.Queue (fed by a TickSource thread),
    processes each tick through the full algo pipeline:
    on_tick → bars → workers → decision → clipping monitor.

    Session log rotates at midnight UTC — each day gets its own file
    in session_logs/ to prevent unbounded log growth on 24/7 sessions.

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
        carried_segment_no: int = 0,
        stale_stress_driver: Optional[StaleDataStressDriver] = None,
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
        # declares them. None on every live session — the block lives in
        # scenario_settings, which a live profile does not carry at all.
        self._stale_stress_driver = stale_stress_driver
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
        self._risk_baseline = risk_baseline
        # #314 — the DAILY denominator. A second record of the same type, and a second
        # tracker rather than a mutable field: "a new day is a new baseline" is then
        # literal, and the session tracker keeps its one job of never moving backwards.
        # Deliberately NOT persisted: a daily limit that survived a restart would carry
        # yesterday's loss into today, which is the opposite of what daily means.
        self._day_baseline: Optional[RiskBaselineTracker] = None
        self._safety_current_day: Optional[str] = None
        # #356 — the HARD stop, and it is a two-step because a close is ASYNCHRONOUS. The
        # session-end validator already refuses `positions='close'` for exactly this reason:
        # the fill arrives on a later tick, and EMERGENCY means immediate exit. Sending the
        # closes and ending in the same breath would leave positions open at the venue while
        # the books called them closed. So: send once, keep draining, end when the book is
        # flat or the window runs out — and say which positions were never confirmed.
        self._flatten_sent_at_tick: Optional[int] = None
        self._flatten_reason: str = ''
        # #355 — the open book is written when it CHANGES, in two classes. A STRUCTURAL
        # change (a position opens, closes, is partially closed) is written at once: it cannot
        # be recovered, and waiting out an interval is exactly the window a hard kill takes
        # the position away in. DRIFT (exit levels, excursion extrema) waits for a cadence,
        # because a trailing stop moves on nearly every tick of a trend and one write costs
        # 11 ms on this project's tree (§42) — immediate would mean a 11 ms stall per tick to
        # protect a value the algo re-derives anyway. The watcher is seeded with the book as
        # it stands after adoption, so a session that changes nothing never writes.
        self._persist_carry_over = persist_carry_over
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
        self._market_data_tracker = MarketDataEpisodeTracker(
            source=config.broker_type,
            logger=logger,
            measure_wall_duration=True,
        )

        # Resolve symbol currencies from broker config (avoids string splitting heuristic)
        symbol_spec = executor.broker.adapter.get_symbol_specification(config.symbol)
        self._base_currency = symbol_spec.base_currency
        self._quote_currency = symbol_spec.quote_currency

        # Safety / circuit breaker state
        self._safety_blocked = False
        self._safety_reason = ''
        self._safety_current_value: float = 0.0
        self._safety_drawdown_pct: float = 0.0

        # #356 — what the breaker SAW, for the end-of-session report. The live state above
        # answers "is the bot blocked right now", which is the wrong question after a
        # thirty-day run: a session that touched 18 % at hour three and recovered ends
        # looking exactly like one that never moved. These are running maxima, and they are
        # kept whether the breaker is ENABLED or not — a session with the limits off still
        # measures its drawdown, and that record is what says what would have fired.
        self._safety_final_value: float = 0.0
        self._safety_worst_dd_abs: float = 0.0
        self._safety_worst_dd_abs_at: str = ''
        self._safety_worst_dd_pct: float = 0.0
        self._safety_worst_dd_pct_at: str = ''
        self._safety_block_count: int = 0
        self._safety_days: List[SafetyDayRecord] = []
        self._day_worst_loss_abs: float = 0.0
        self._day_worst_loss_pct: float = 0.0
        self._day_worst_loss_at: str = ''
        self._day_limit_hit: bool = False
        self._flatten_completed: Optional[bool] = None
        self._flatten_unconfirmed: List[str] = []


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
        # 00:00 UTC, forex falls back to its swap rollover (17:00 New York). The log
        # rotation and the daily-loss baseline below read the same answer, so a session
        # cannot rotate its log on one boundary and reset its limit on another.
        self._day_anchor = MarketConfigManager().get_trading_day_anchor(config.broker_type)

        # === BOOKING SEGMENTS (#537) — the Hauptbuch of this session ===
        # One recorder, shared with the simulation loop: the two are shaped differently (a class
        # here, a function there) and a recorder each would be two implementations of one rule.
        # It COLLECTS; the report coordinator writes every period at once when the run ends, so
        # the parquet write stays out of what the throughput benchmark measures.
        self._booking = BookingSegmentRecorder(
            unit_name=config.name or config.symbol,
            anchor=self._day_anchor,
            carried_segment_no=carried_segment_no,
            log=self._logger.info,
        )

        # Daily rotation state
        self._current_log_date: Optional[str] = None
        # Track placeholder file for cleanup on first tick
        self._initial_placeholder_path: Optional[Path] = None
        if self._logger.file_logger:
            self._initial_placeholder_path = self._logger.file_logger.log_file_path

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
        ticks_processed = 0
        ticks_clipped = 0
        prev_msc: int = 0

        # Track last valid tick/decision for the final shutdown snapshot
        last_tick: Optional[TickData] = None
        last_decision: Optional[Decision] = None

        # Daily rotation: date initialized from first tick (not wall clock)
        # This prevents spurious rotation in mock/replay mode where tick
        # timestamps differ from current date.
        self._current_log_date = None

        # #320 — Push an initial startup pulse frame so the display renders
        # the full dashboard from t=0 instead of falling back to a wait
        # placeholder. Subsequent pulses fire from queue.Empty heartbeats.
        self._push_pulse_frame(ticks_processed)

        while self._running:
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
                # #476: the boundary is checked HERE too, and this is the half that makes
                # it reliable — a feed that goes quiet across the rollover would otherwise
                # never rotate. The clock is set one line above, so the day is current.
                self._check_daily_rotation()
                # #537: the booking period seals on the SAME boundary, in its own pass — a
                # booking must not depend on whether logs are being written.
                self._booking.check_boundary(
                    self._executor.get_current_time_if_set(), self._seal_source)
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
                self._run_decision_heartbeat(ticks_processed)
                # #354: persist algo state on the idle timer too (hybrid cadence).
                self._persist_state_if_due(ticks_processed)
                # #355: a fill can resolve on the heartbeat, so the book can change here too.
                self._persist_carry_over_if_needed(ticks_processed)
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

            # === 1. Trade Executor — BROKER PATH (all ticks) ===
            self._executor.on_tick(tick)

            # === DAILY LOG ROTATION ===
            # After on_tick, because that is what advances the canonical clock to this
            # tick's time — before it, the clock still holds the previous pass. In live the
            # heartbeat above has almost always rotated first; this covers a replay whose
            # queue never runs empty.
            self._check_daily_rotation()
            self._booking.check_boundary(
                self._executor.get_current_time_if_set(), self._seal_source)

            # #436: a real tick ends a stale episode (recovery, edge reset).
            if self._market_stale:
                self._end_market_stale_episode()

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
            self._update_risk_baseline(tick)

            # === 2. Bar Rendering (shared core, #303) ===
            current_bars = render_bars_for_tick(tick, self._bar_controller)

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
            safety_baseline = self._safety_baseline_value()
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
                self._observe_safety_excursion(safety_value, safety_baseline)
                self._check_safety(safety_value, safety_baseline)
                # #356 — the HARD stop, checked after the soft one so a session that trips
                # both reports both. Sends the closes ONCE; the drain below carries them.
                self._check_emergency_flatten(
                    safety_value, safety_baseline, ticks_processed)
            self._drive_emergency_flatten(ticks_processed)

            # === 6. Order Execution ===
            if self._safety_blocked:
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
                    safety=self._safety_state(),
                    recent_rejections=list(self._recent_rejections),
                    total_rejections=self._rejection_count,
                    initial_spot_equity=self._initial_spot_equity)
                try:
                    self._display_queue.put_nowait(display_stats)
                except queue.Full:
                    pass  # Display will use last known state

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
            if self._executor.is_session_end_requested():
                end_reason = self._executor.get_session_end_reason()
                end_severity = self._executor.get_session_end_severity()
            else:
                end_reason = 'tick source exhausted'
                end_severity = SessionEndSeverity.NORMAL
            self._decision_event_dispatcher.submit(SessionEndEvent(
                reason=end_reason,
                severity=end_severity,
                tick_time=self._executor.get_current_time(),
            ))
            self._decision_event_dispatcher.drain()

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
                safety=self._safety_state(),
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
        if not (order_result and order_result.is_rejected):
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
        Reconciler itself; correction is #349.

        Args:
            ticks_processed: Current tick counter (drives the hybrid cadence)
        """
        if self._reconciler is None or not self._reconciler.is_due(ticks_processed):
            return
        result = self._reconciler.reconcile(ticks_processed)
        if result.attributed_orders:
            self._executor.apply_order_attributions(result.attributed_orders)

    def _persist_state_if_due(self, ticks_processed: int) -> None:
        """
        Save the algo state snapshot if the persistence cadence is due (#354).

        No-op when no state store is wired (algo did not opt in). Mid-session save
        failures are logged (error pot, §35) and swallowed — a persistence problem
        must never abort a live trading session.

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
        The write itself logs its own failures — a carry-over problem must never end a live
        trading session.

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

    def _run_decision_heartbeat(self, ticks_processed: int) -> None:
        """
        #360 ghost-pass: run an opt-in decision between ticks without a synthetic tick.

        Workers do not recompute — the orchestrator serves their cached results. The
        decision runs with tick=None so it can advance internal state (e.g. field-study
        phases), react to drained #348 events, and issue follow-up orders (re-arm /
        cancel / confirm). No tick state is mutated (no counter, no mark_dirty, no bar
        render). Safety reuses the last evaluated block state (no fresh price on idle).

        Args:
            ticks_processed: Current tick counter (unchanged on a ghost-pass)
        """
        decision = run_ghost_pass(self._worker_orchestrator)
        if decision is None:
            return
        self._ghost_pass_count += 1
        if self._safety_blocked:
            decision.action = DecisionLogicAction.FLAT
        order_result = self._decision_logic.execute_decision(decision, tick=None)
        if order_result is not None:
            self._ghost_action_count += 1
        self._record_rejection(order_result, decision)

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
                safety=self._safety_state(),
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
        # Reconnect visibility (error channel v0): surface transport
        # reconnects to the session pot; typed connection events are #375/#331.
        if self._tick_source is not None:
            reconnects = self._tick_source.get_reconnect_count()
            if reconnects > self._last_reconnect_count:
                self._logger.warning(
                    f'🔌 Tick stream reconnected '
                    f'(+{reconnects - self._last_reconnect_count}, total {reconnects})')
                self._last_reconnect_count = reconnects

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
            return  # no tick yet — startup wait, not an outage
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
            self._logger.warning(
                f"⚠️ Market data stale since "
                f"{self._market_stale_since.strftime('%H:%M:%S')}: no tick for "
                f"{seconds_since:.0f}s (threshold "
                f"{self._market_data_stale_after_s:.0f}s) — entries guard-blocked"
            )
            self._decision_logic.on_market_data_stale(status)

    def _end_market_stale_episode(self) -> None:
        """
        Close a stale episode on tick resumption (#436): fresh status + edge reset.

        The episode RECORD and its pot line come from the MarketDataEpisodeTracker
        (#451), which derives both from the observed status change — so a real and an
        injected outage travel the exact same path into the report.
        """
        self._market_stale = False
        self._market_stale_since = None
        self._executor.set_market_data_status(MarketDataStatus(
            reconnect_count=self._last_reconnect_count))

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

    def get_market_data_tick_stats(self) -> MarketDataTickStats:
        """
        The session's market-data tick counters (#451 Part 4).

        Returns:
            MarketDataTickStats for the session's tick source
        """
        return self._market_data_tracker.get_tick_stats()

    def _safety_state(self) -> SafetyState:
        """Bundle the current circuit-breaker state for the display exporter."""
        return SafetyState(
            blocked=self._safety_blocked,
            reason=self._safety_reason,
            current_value=self._safety_current_value,
            drawdown_pct=self._safety_drawdown_pct,
        )

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

    def _update_risk_baseline(self, tick: TickData) -> None:
        """
        Take the session's risk baseline if it has none, and advance a high-water mark.

        Both calls are cheap and both are no-ops in the ordinary case — `ensure_taken` after
        the first tick, `observe` for every mode except HIGH_WATER_MARK. They run on every
        tick rather than on a cadence because a peak missed is a peak lost: the record is a
        running maximum, and sampling it would make the limit depend on when we happened to
        look.

        Args:
            tick: The tick whose price values the holdings
        """
        if self._risk_baseline is None:
            return
        value = self._executor.portfolio.get_account_value()
        if value is None:
            return

        mark_price: Optional[float] = None
        quantities: Optional[BaselineQuantities] = None
        if self._trading_model == TradingModel.SPOT:
            mark_price = tick.mid
            quantities = self._spot_quantities()

        self._risk_baseline.ensure_taken(value, mark_price, quantities)
        self._risk_baseline.observe(value, mark_price, quantities)
        self._update_day_baseline(value, mark_price, quantities)
        # The booking period's VALUATION half, beside the risk one (#537). Same tick, same
        # value, so the two never describe different instants.
        self._booking.observe_equity(value)

    def _update_day_baseline(
        self,
        value: float,
        mark_price: Optional[float],
        quantities: Optional[BaselineQuantities],
    ) -> None:
        """
        Start a new daily baseline when the market crosses into a new trading day (#314).

        The day comes from the canonical clock and this market's anchor (#476), never from
        the wall clock and no longer from the tick stamp: both event sources advance that
        clock, so a replay and a live session still answer alike, and a quiet feed over the
        boundary no longer hides it. The check stays deliberately independent of
        `_check_daily_rotation`: that one rotates a LOG file and returns early when there is
        no run directory, and a loss limit must not depend on whether logs are being written.

        Same ANCHOR as that rotation, but not the same MOMENT: the rotation also runs on the
        heartbeat, while this reaches the boundary only on the next real TICK, because at spot
        the baseline needs a mark price and a heartbeat carries none. The lateness is
        self-limiting rather than a gap — nothing can move the account until a tick arrives,
        and that is the same tick which resets the denominator.

        Args:
            value: The account value at this moment
            mark_price: The price the holdings are valued at (spot only)
            quantities: What is held at that instant (spot only)
        """
        current_day = self._trading_day()
        if current_day is None:
            return
        day = current_day.isoformat()
        if day == self._safety_current_day:
            return
        self._close_safety_day()
        self._safety_current_day = day
        self._day_baseline = RiskBaselineTracker(
            mode=BaselineKind.DAY_START,
            restored=None,
            spot_mode=self._trading_model == TradingModel.SPOT,
            exclusive_account=self._config.capital.exclusive_account,
            clock_fn=self._executor.get_current_time_if_set,
            logger=self._logger,
        )
        self._day_baseline.ensure_taken(
            value, mark_price, quantities, origin=BaselineOrigin.DAY_BOUNDARY)

    def _trading_day(self) -> Optional[date]:
        """
        The trading day the canonical clock currently stands in (#476).

        Returns:
            The trading day's date, or None before the clock has been set for the first time
        """
        now = self._executor.get_current_time_if_set()
        return trading_day_of(now, self._day_anchor) if now else None

    def _close_safety_day(self) -> None:
        """
        File the day that is ending and reset the per-day counters (#314).

        One row per day rather than one maximum across all of them: a daily limit is
        measured against a denominator that is struck fresh every morning, so a maximum over
        thirty days would be a maximum over thirty different references — a number about
        nothing. The rows are bounded by the run's length in days.
        """
        if self._safety_current_day is None or self._day_baseline is None:
            return
        self._safety_days.append(SafetyDayRecord(
            day=self._safety_current_day,
            baseline=self._day_baseline.get_baseline(),
            worst_loss_abs=self._day_worst_loss_abs,
            worst_loss_pct=self._day_worst_loss_pct,
            worst_loss_at=self._day_worst_loss_at,
            limit_hit=self._day_limit_hit,
        ))
        self._day_worst_loss_abs = 0.0
        self._day_worst_loss_pct = 0.0
        self._day_worst_loss_at = ''
        self._day_limit_hit = False

    def _seal_source(self):
        """
        The records and the stock reading a seal needs, at this instant.

        Handed to the recorder as a CALLABLE rather than as values: a boundary check runs on
        every pass and almost never seals, so the portfolio is only read when a period actually
        closes.

        Returns:
            (trade records, SegmentSnapshot)
        """
        return snapshot_from_portfolio(self._executor.portfolio)

    def get_highest_segment_no(self) -> int:
        """
        The largest booking period this session has sealed (#537).

        Read by the carry-over so the next session continues the count.

        Returns:
            The high-water mark, which is the inherited floor when nothing was sealed
        """
        return self._booking.get_highest_segment_no()

    def get_booking_segments(self) -> List[BookingSegment]:
        """
        This session's Hauptbuch — one entry per closed booking period (#537).

        Seals the period that was still running, so the final and necessarily incomplete one is
        filed like every other rather than dropped for having no successor. Exactly what
        `get_safety_session` does for the day records, and for the same reason.

        Returns:
            The periods, oldest first
        """
        return self._booking.close(
            self._executor.get_current_time_if_set(), self._seal_source)

    def _observe_safety_excursion(self, current_value: float, baseline: float) -> None:
        """
        Record how far the account moved BELOW its two denominators (#356).

        Runs whether the breaker is enabled or not, because a session with the limits off
        still measures a drawdown — and for a parity proof that record is the interesting
        one: it says what WOULD have fired before anything is armed.

        The absolute and the percentage low are tracked as SEPARATE instants. A
        high-water-mark baseline moves, so the deepest amount and the deepest share are not
        the same moment, and one figure alone always understates one of the two limits. With
        the default fixed baseline they coincide and agree by construction.

        Args:
            current_value: The account value the breaker is about to check
            baseline: The session denominator it measures against
        """
        self._safety_final_value = current_value
        stamp = self._executor.get_current_time_if_set()
        stamp_utc = stamp.isoformat() if stamp is not None else ''

        if baseline > 0:
            drawdown_abs = baseline - current_value
            drawdown_pct = drawdown_abs / baseline * 100.0
            if drawdown_abs > self._safety_worst_dd_abs:
                self._safety_worst_dd_abs = drawdown_abs
                self._safety_worst_dd_abs_at = stamp_utc
            if drawdown_pct > self._safety_worst_dd_pct:
                self._safety_worst_dd_pct = drawdown_pct
                self._safety_worst_dd_pct_at = stamp_utc

        if self._day_baseline is None:
            return
        day_start = self._day_baseline.get_value()
        if day_start <= 0:
            return
        daily_loss = day_start - current_value
        if daily_loss > self._day_worst_loss_abs:
            self._day_worst_loss_abs = daily_loss
            self._day_worst_loss_pct = daily_loss / day_start * 100.0
            self._day_worst_loss_at = stamp_utc

    def get_safety_session(self) -> SafetySessionRecord:
        """
        What the circuit breaker saw over this session, for the report (#356 / #314).

        Closes the day that was still running, so the final — necessarily incomplete — day
        is filed like every other one rather than dropped for having no successor.

        A hard stop whose drain never concluded is resolved here rather than left at None.
        The session can end for a reason of its own while the closes are still in flight
        (the tick source dies, the operator interrupts), and an unresolved drain reported as
        "did not fire" would hide open positions at the venue behind a blank field.

        Returns:
            The captured record; its baseline is None when none was ever taken
        """
        self._close_safety_day()
        self._safety_current_day = None

        flatten_fired = bool(self._flatten_reason)
        flatten_completed = self._flatten_completed
        flatten_unconfirmed = list(self._flatten_unconfirmed)
        if flatten_fired and flatten_completed is None:
            flatten_completed = False
            flatten_unconfirmed = [
                position.position_id
                for position in self._executor.portfolio.get_open_positions()]

        return SafetySessionRecord(
            enabled=self._config.safety.enabled,
            spot_mode=self._trading_model == TradingModel.SPOT,
            baseline=(self._risk_baseline.get_baseline()
                      if self._risk_baseline is not None else None),
            final_value=self._safety_final_value,
            worst_drawdown_abs=self._safety_worst_dd_abs,
            worst_drawdown_abs_at=self._safety_worst_dd_abs_at,
            worst_drawdown_pct=self._safety_worst_dd_pct,
            worst_drawdown_pct_at=self._safety_worst_dd_pct_at,
            block_count=self._safety_block_count,
            blocked_at_end=self._safety_blocked,
            reason_at_end=self._safety_reason,
            days=list(self._safety_days),
            flatten_fired=flatten_fired,
            flatten_reason=self._flatten_reason,
            flatten_completed=flatten_completed,
            flatten_unconfirmed=flatten_unconfirmed,
        )

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

    def _safety_baseline_value(self) -> float:
        """
        The denominator the circuit breaker measures its drawdown against.

        The tracker's record when there is one, and the portfolio's opening balance
        otherwise — which is the pre-#356 behaviour and the honest answer for a session that
        has not taken a baseline yet. Zero is what the breaker already reads as "no drawdown
        check", so a spot session before its first tick is unchanged.

        Returns:
            The baseline value in account currency
        """
        if self._risk_baseline is not None:
            value = self._risk_baseline.get_value()
            if value > 0:
                return value
        return self._executor.portfolio.initial_balance

    def _check_safety(self, current_value: float, initial_balance: float) -> None:
        """
        Evaluate circuit breaker conditions and update safety state.

        Soft stop: sets _safety_blocked flag. Existing positions continue,
        new entries are blocked by overriding decision to FLAT.

        Args:
            current_value: The account value — one definition per model (#356), never
                settled cash
            initial_balance: Session start balance (== initial equity, no positions at start)
        """
        safety = self._config.safety
        if not safety.enabled:
            return

        # Already blocked — check if conditions cleared (value recovered)
        was_blocked = self._safety_blocked
        self._safety_blocked = False
        self._safety_reason = ''
        self._safety_current_value = current_value

        # Min threshold: spot checks min_equity, margin checks min_balance — and since #356
        # both denominate the SAME quantity, the account value, so the account model only
        # decides which config key a profile writes.
        # ⚠️ #209 (2026-09-14, §31b): whether MARGIN also needs a floor on SETTLED CASH,
        # separate from the account value, is open. "Can I still cover a margin call" is a
        # question the account value does not answer, and merging the two fields would delete
        # the ability to ask it. Decided with a real margin account, not from here.
        if self._trading_model == TradingModel.SPOT:
            min_threshold = safety.min_equity
            min_label = 'min_equity'
        else:
            min_threshold = safety.min_balance
            min_label = 'min_balance'

        if min_threshold > 0 and current_value < min_threshold:
            self._block(f'{min_label} ({current_value:.4f} < {min_threshold:.4f})')

        # Drawdown against the risk baseline, on the ACCOUNT VALUE in both models (#356)
        if safety.max_drawdown_pct > 0 and initial_balance > 0:
            drawdown_pct = (initial_balance - current_value) / \
                initial_balance * 100.0
            self._safety_drawdown_pct = max(0.0, drawdown_pct)
            if drawdown_pct > safety.max_drawdown_pct:
                self._block(
                    f'max_drawdown ({drawdown_pct:.1f}% > {safety.max_drawdown_pct:.1f}%)')
        else:
            self._safety_drawdown_pct = 0.0

        # #314 — the ABSOLUTE sibling of the percentage above, and independent of it. A
        # percentage auto-scales with the account; an absolute floor is what stops that
        # percentage from meaning a dangerously large number once the account has grown.
        if safety.max_drawdown_abs > 0 and initial_balance > 0:
            drawdown_abs = initial_balance - current_value
            if drawdown_abs > safety.max_drawdown_abs:
                self._block(
                    f'max_drawdown_abs ({drawdown_abs:.2f} > {safety.max_drawdown_abs:.2f})')

        self._check_daily_loss(current_value)

        if self._safety_blocked and not was_blocked:
            # Count the ENGAGEMENT, not the ticks spent blocked. "Blocked on 40 000 ticks"
            # is one event read as forty thousand; "engaged 3 times" is the number an
            # operator can act on.
            self._safety_block_count += 1
            self._logger.warning(
                f'⛔ Safety circuit breaker triggered: {self._safety_reason}')
        elif was_blocked and not self._safety_blocked:
            self._logger.info('✅ Safety circuit breaker cleared')

    def _check_emergency_flatten(
        self,
        current_value: float,
        baseline: float,
        ticks_processed: int,
    ) -> None:
        """
        Trip the HARD stop, once, when the drawdown passes a hard threshold (#356).

        Three severities exist and this is the third. A soft block stops new entries and
        leaves what is open; HALT (#349) freezes orders and waits for a human; this CLOSES.
        The distinction matters because the soft block is the wrong answer to a runaway: it
        stops the bot from making things worse and does nothing about what it already holds.

        Sends the closes and returns. It does NOT end the session here — see
        `_drive_emergency_flatten` for why that is a second step rather than the next line.

        Args:
            current_value: The account value being checked
            baseline: The denominator the drawdown is measured against
            ticks_processed: The loop's tick counter, recorded as the drain window's start
        """
        safety = self._config.safety
        if not (safety.enabled and safety.emergency_flatten_enabled):
            return
        if self._flatten_sent_at_tick is not None or baseline <= 0:
            return

        drawdown_abs = baseline - current_value
        drawdown_pct = drawdown_abs / baseline * 100.0
        reason = ''
        if safety.max_drawdown_pct_hard > 0 and drawdown_pct > safety.max_drawdown_pct_hard:
            reason = (f'hard drawdown {drawdown_pct:.1f}% > '
                      f'{safety.max_drawdown_pct_hard:.1f}%')
        elif safety.max_drawdown_abs_hard > 0 and drawdown_abs > safety.max_drawdown_abs_hard:
            reason = (f'hard drawdown {drawdown_abs:.2f} > '
                      f'{safety.max_drawdown_abs_hard:.2f}')
        if not reason:
            return

        self._flatten_reason = reason
        self._send_emergency_closes(reason, ticks_processed)

    def _send_emergency_closes(self, reason: str, ticks_processed: int) -> None:
        """
        Ask the venue to close everything this bot holds.

        SPOT is gated behind its own switch and defaults OFF, and the asymmetry is not
        timidity. A margin position can lose more than the account holds, so liquidating it
        is the entire point of a hard stop. A spot holding cannot — the worst case is the
        asset going to zero, which is bounded by what was spent — so selling it converts an
        unrealised loss into a realised one, which is a trading decision rather than a
        safety one. A profile that must end flat turns it on.

        Args:
            reason: What tripped, for the log and the session end
            ticks_processed: The loop's tick counter, recorded as the drain window's start
        """
        positions = list(self._executor.portfolio.get_open_positions())
        spot = self._trading_model == TradingModel.SPOT
        if spot and not self._config.safety.spot_liquidate_to_quote:
            self._logger.error(
                f'🚨 EMERGENCY STOP: {reason}. New entries are blocked and the session will '
                f'end. The {len(positions)} open spot holding(s) are NOT sold — '
                f'safety.spot_liquidate_to_quote is off, so the position stays and its loss '
                f'stays unrealised.')
            self._flatten_sent_at_tick = -1
            return

        self._logger.error(
            f'🚨 EMERGENCY STOP: {reason}. Closing {len(positions)} open position(s) now, '
            f'then ending the session. This is the HARD stop, not the entry block.')
        for position in positions:
            try:
                self._executor.close_position(position.position_id)
            except Exception as e:                       # noqa: BLE001 — reported below
                # A close that could not even be SENT is the worst case of the three, and
                # it must not stop the loop from trying the others.
                self._logger.error(
                    f'❌ EMERGENCY STOP could not send the close for '
                    f'{position.position_id}: {e}. It is still open at the venue.')
        self._flatten_sent_at_tick = ticks_processed

    def _drive_emergency_flatten(self, ticks_processed: int) -> None:
        """
        End the session once the closes have actually landed — or say they did not.

        This is the second step, and it exists because a close is ASYNCHRONOUS. It is sent
        on one tick and its fill arrives on a later one, through the same drain every other
        fill uses. `SessionEndSeverity.EMERGENCY` means immediate exit, so requesting it in
        the same breath as the closes would tear the session down before the fills arrived —
        the books would report a flat account while the positions sat at the venue. The
        session-end validator already refuses `positions='close'` for exactly this reason.

        So the loop keeps running, the drain keeps working, and the session ends when the
        book is flat. The window is bounded: a venue that never answers must not hold the
        session open indefinitely, and what happens at the ceiling is loud rather than
        silent.

        Args:
            ticks_processed: The loop's own tick counter
        """
        if self._flatten_sent_at_tick is None:
            return
        open_positions = self._executor.portfolio.get_open_positions()
        if not open_positions:
            self._flatten_completed = True
            self._executor.request_session_end(
                f'emergency flatten complete ({self._flatten_reason})',
                SessionEndSeverity.EMERGENCY)
            self._flatten_sent_at_tick = None
            return

        waited = ticks_processed - max(self._flatten_sent_at_tick, 0)
        if self._flatten_sent_at_tick < 0 or waited >= _FLATTEN_DRAIN_TICKS:
            self._flatten_completed = False
            self._flatten_unconfirmed = [p.position_id for p in open_positions]
            still_open = ', '.join(p.position_id for p in open_positions)
            self._logger.error(
                f'🚨 EMERGENCY STOP ending the session with {len(open_positions)} '
                f'position(s) still open: {still_open}. They were not confirmed closed '
                f'within the drain window — check the account by hand.')
            self._executor.request_session_end(
                f'emergency flatten incomplete ({self._flatten_reason})',
                SessionEndSeverity.EMERGENCY)
            self._flatten_sent_at_tick = None

    def _block(self, reason: str) -> None:
        """
        Record one breached condition.

        Conditions are OR-combined and every one that fired is named, because an operator
        reading a blocked session needs to know whether one limit was touched or three were
        blown through. The reasons accumulate in the order they are checked.

        Args:
            reason: What tripped, with its numbers
        """
        self._safety_blocked = True
        self._safety_reason = (
            f'{self._safety_reason} + {reason}' if self._safety_reason else reason)

    def _check_daily_loss(self, current_value: float) -> None:
        """
        Evaluate the DAILY loss limits against the day's own baseline (#314).

        A daily limit guards a different failure from a session one. A session drawdown
        accumulates from process start; a day resets. A bot that loses a little every single
        day never trips a session limit at all, and that is exactly the shape a thirty-day
        unattended run is exposed to.

        Silent until a day baseline exists, which is the first tick of the session.

        Args:
            current_value: The account value being checked
        """
        safety = self._config.safety
        if self._day_baseline is None:
            return
        day_start = self._day_baseline.get_value()
        if day_start <= 0:
            return

        daily_loss = day_start - current_value
        if safety.max_daily_loss_abs > 0 and daily_loss > safety.max_daily_loss_abs:
            self._block(
                f'max_daily_loss_abs ({daily_loss:.2f} > {safety.max_daily_loss_abs:.2f})')
            self._day_limit_hit = True

        if safety.max_daily_loss_pct > 0:
            daily_loss_pct = daily_loss / day_start * 100.0
            if daily_loss_pct > safety.max_daily_loss_pct:
                self._block(
                    f'max_daily_loss ({daily_loss_pct:.1f}% > '
                    f'{safety.max_daily_loss_pct:.1f}%)')
                self._day_limit_hit = True

    def _check_daily_rotation(self) -> None:
        """
        Rotate the session log when the market crosses into a new trading day (#476).

        On the first pass: set the initial date and rotate to a file named for the trading
        day (startup creates one from the wall clock, which may differ in replay mode).

        The day comes from the canonical clock and this market's anchor, not from the tick
        stamp. Two things that fixes: a forex session rolls at its swap rollover instead of
        at midnight UTC, and a feed that goes silent across the boundary no longer keeps the
        old file growing — the heartbeat advances the clock and calls this too.
        """
        if not self._run_dir:
            return

        current_day = self._trading_day()
        if current_day is None:
            return
        day_label = current_day.strftime('%Y%m%d')

        if self._current_log_date is None:
            # First pass — ensure the file matches the trading day
            new_file_logger = create_session_file_logger(
                self._run_dir, day_label
            )
            self._logger.swap_file_logger(new_file_logger)
            self._current_log_date = day_label
            # Keep placeholder file — it contains pre-tick logs (warmup bars, pipeline setup)
            self._initial_placeholder_path = None
            self._prune_rotated_session_logs(day_label)
            return

        if day_label != self._current_log_date:
            self._logger.info(
                f'📅 Date change detected: {self._current_log_date} → {day_label} — rotating session log'
            )
            new_file_logger = create_session_file_logger(
                self._run_dir, day_label
            )
            self._logger.swap_file_logger(new_file_logger)
            self._current_log_date = day_label
            self._logger.info(
                f'📅 Session log rotated to autotrader_session_{day_label}.log'
            )
            self._prune_rotated_session_logs(day_label)

    def _prune_rotated_session_logs(self, day_label: str) -> None:
        """
        Apply the configured retention to the rotated session logs (#357).

        Called on EVERY rotation, which includes the first pass — so a session that runs
        long enough to rotate is also the one that prunes, and a session that never rotates
        never deletes anything. The window is read per call rather than cached: it costs one
        dictionary lookup a day, and it is what lets an operator widen the window on a
        running deployment by editing the config before the next restart.

        Args:
            day_label: The trading day just rotated to (YYYYMMDD) — never deleted
        """
        if not self._run_dir:
            return
        retention_days = (AppConfigManager().get_file_logging_config_object()
                          .session_logs.retention_days)
        prune_rotated_session_logs(
            self._run_dir, day_label, retention_days, self._logger)
