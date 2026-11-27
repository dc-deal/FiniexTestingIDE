"""
FiniexTestingIDE - AutoTrader Pass Hooks
An AutoTrader session's own steps inside a pass of the loop core (#458).
"""

from collections import deque
from python.framework.autotrader.event_loop.autotrader_cadence_work import AutotraderCadenceWork
from python.framework.autotrader.event_loop.autotrader_market_data_watch import (
    AutotraderMarketDataWatch,
)
from python.framework.autotrader.event_loop.autotrader_trading_days import AutotraderTradingDays
from python.framework.types.autotrader_types.autotrader_loop_state_types import AutotraderLoopState
from typing import Deque, Dict, List, Optional

from python.framework.autotrader.safety.risk_baseline_tracker import RiskBaselineTracker
from python.framework.autotrader.safety.safety_circuit_breaker import SafetyCircuitBreaker
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.loop.abstract_live_pass_hooks import AbstractLivePassHooks
from python.framework.reporting.booking_period_recorder import (
    BookingPeriodRecorder,
)
from python.framework.signal_data.transport.signal_inbox import SignalInbox
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.trading_env.algo_masks.algo_order_result import AlgoOrderResult
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_display_types import RejectionEntry
from python.framework.types.config_types.market_config_types import TradingModel
from python.framework.types.decision_logic_types import Decision
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.persistence_types import BaselineQuantities
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


class AutotraderPassHooks(AbstractLivePassHooks):
    """
    What the loop core's dispatcher calls at fixed points of an AutoTrader session's pass (#458):
    the signals, the day boundary's close, the market and the account after a tick, the safety
    layer after the decision, what a decision sent, the staleness check and the reconcile when
    their timers fell due, and the ghost pass's counts.

    Args:
        config: AutoTrader configuration
        executor: The session's executor
        worker_orchestrator: The workers — the signal arrivals they take, their maxima
        decision_logic: The decision logic, for its maxima
        signal_inbox: Filled by a live signal transport on its own thread; None when the session
            mounted its series
        trading_model: Margin or spot
        booking: The session's booking period recorder, which observes the account's value
        safety: The safety layer — soft breaker, hard stop, daily loss limit
        risk_baseline: The risk baseline the account's value is observed against; None without
        market_watch: The market-data staleness contract
        cadence: The reconcile, run when its timer fell due
        trading_days: Closes the trading days a boundary check sealed
        logger: The session logger
        state: What the event loop's units share while it runs
    """

    def __init__(
        self,
        config: AutoTraderConfig,
        executor: AbstractTradeExecutor,
        worker_orchestrator: WorkerOrchestrator,
        decision_logic: AbstractDecisionLogic,
        signal_inbox: Optional[SignalInbox],
        trading_model: TradingModel,
        booking: BookingPeriodRecorder,
        safety: SafetyCircuitBreaker,
        risk_baseline: Optional[RiskBaselineTracker],
        market_watch: AutotraderMarketDataWatch,
        cadence: AutotraderCadenceWork,
        trading_days: AutotraderTradingDays,
        logger: ScenarioLogger,
        state: AutotraderLoopState,
    ):
        self._config = config
        self._executor = executor
        self._worker_orchestrator = worker_orchestrator
        self._decision_logic = decision_logic
        # #141 Part 2a: filled by a live signal transport on its own thread, drained here
        # once per pass. Present ONLY when the startup resolved a LIVE signal source — a
        # mounted (mock / simulation) session has none, so every drain below is a no-op and the
        # loop behaves exactly as before.
        self._signal_inbox = signal_inbox
        self._trading_model = trading_model
        self._booking = booking
        self._safety = safety
        self._risk_baseline = risk_baseline
        self._market_watch = market_watch
        self._cadence = cadence
        self._trading_days = trading_days
        self._logger = logger
        self._state = state

        # #360 ghost-pass observability — proves the heartbeat decision pass fires
        # (and how often it acts), reported once at session end.
        self._ghost_pass_count: int = 0
        self._ghost_action_count: int = 0

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

    # === The loop core's hooks (#458) — this session's own steps inside a pass ===

    def refresh_signals(self, off_tick: bool) -> None:
        """
        Fold in the signal envelopes received since the last pass (#141).

        Args:
            off_tick: True on a pass with no tick, False on a tick
        """
        self._drain_signal_inbox(off_tick)

    def close_sealed_days(self) -> None:
        """
        Close the trading days the boundary check just sealed, then move the session log onto the
        day that opened (#476).
        """
        self._trading_days.close_sealed_days()

    def observe_market(self, tick: TickData) -> None:
        """
        The tick span, the market-data status and the account's value, once the broker path has
        seen a tick.

        Args:
            tick: The tick the broker path just applied
        """
        first_tick = self._state.first_tick_time is None
        if first_tick:
            self._state.first_tick_time = tick.timestamp
        self._state.last_tick_time = tick.timestamp

        # #436, #444, #451: what the tick means for the market data — a stale episode ended, a
        # planned window advanced, the observer counted it
        self._market_watch.on_tick()

        # Capture spot equity baseline on the session's first tick (first live price available);
        # a slice can hand several ticks at once (#461), so the loop's tick count is no witness
        if first_tick and self._trading_model == TradingModel.SPOT:
            first_price = tick.mid
            self._initial_spot_equity = self._executor.portfolio.get_spot_equity(first_price)
        # #356 — the RISK baseline, which is a different question and answers it once:
        # `ensure_taken` does nothing when a record was restored from the predecessor, so
        # a restart mid-drawdown keeps the reference it had instead of re-anchoring at
        # the drawn-down value. `observe` then advances a high-water mark, and only that.
        # The booking period observes the same value; its boundary was checked above,
        # before this tick moved the account (#476).
        self._observe_account_value(tick)

    def guard_account(self) -> None:
        """
        Value the account after the decision and run the safety layer on that value: the soft
        breaker, the hard stop and its closes (#356).
        """
        tick_passes = self._state.tick_passes
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
            # word means. (#515 will add the stop-out pass — it rides this
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
                safety_value, safety_baseline, tick_passes)
        self._safety.drive_emergency_flatten(tick_passes)

    def entries_blocked(self) -> bool:
        """
        Whether the safety layer blocks new entries, which turns a decision into FLAT.

        Returns:
            True when entries are blocked
        """
        return self._safety.entries_blocked()

    def record_order(self, order_result: Optional[AlgoOrderResult], decision: Decision) -> None:
        """
        Note what a tick's decision sent, for the display's rejections.

        Args:
            order_result: What the decision sent, or None
            decision: The decision that sent it
        """
        self._record_rejection(order_result, decision)

    def evaluate_staleness(self) -> None:
        """Judge the age of the last real tick on a pass with no tick (#436)."""
        self._market_watch.evaluate_staleness()

    def reconcile(self) -> None:
        """Compare the book with the venue's — the timeline's reconcile timer fell due (#151, #461)."""
        self._cadence.reconcile_now(self._state.ticks_processed)

    def count_ghost_pass(self) -> None:
        """Count a ghost pass that returned a decision (#360), for the session's GHOST line."""
        self._ghost_pass_count += 1

    def record_ghost_order(self, order_result: Optional[AlgoOrderResult],
                           decision: Decision) -> None:
        """
        Note what a decision between two ticks sent: whether it acted, and a rejection.

        Args:
            order_result: What the decision sent, or None
            decision: The decision that sent it
        """
        if order_result is not None:
            self._ghost_action_count += 1
        self._record_rejection(order_result, decision)

    def log_new_maxima(self) -> None:
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

    def get_recent_rejections(self) -> List[RejectionEntry]:
        """
        Returns:
            The latest rejections, oldest first, for the display
        """
        return list(self._recent_rejections)

    def get_rejection_count(self) -> int:
        """
        Returns:
            How many orders were refused this session
        """
        return self._rejection_count

    def get_initial_spot_equity(self) -> float:
        """
        Returns:
            The account's value at the session's first price — the display's spot reference;
            0.0 before the first tick and at margin
        """
        return self._initial_spot_equity

    def get_ghost_pass_count(self) -> int:
        """
        Returns:
            How many ghost passes returned a decision (#360)
        """
        return self._ghost_pass_count

    def get_ghost_action_count(self) -> int:
        """
        Returns:
            How many of them sent an order
        """
        return self._ghost_action_count

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

    def _record_rejection(self, order_result: Optional[AlgoOrderResult], decision: Decision) -> None:
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
            quantities = self._trading_days.spot_quantities()

        self._risk_baseline.ensure_taken(value, mark_price, quantities)
        self._risk_baseline.observe(value, mark_price, quantities)
        # The day's baseline is struck where the day rolled — at the seal, from the value it
        # read; this takes the first day's, and a day's whose seal could not value the account
        self._safety.ensure_day_baseline(value, mark_price, quantities)
        # The report's equity curve too: a seal reads the curve's running figures (peak, deepest
        # decline), and the simulation samples on every tick as well. The safety pass samples
        # again after the decision, which changes nothing unless an order filled within this tick.
        self._executor.portfolio.sample_equity(value)
