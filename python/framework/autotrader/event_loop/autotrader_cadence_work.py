"""
FiniexTestingIDE - AutoTrader Cadence Work
What an AutoTrader session does on a cadence: the reconcile, the algo state's save and the carry-over.
"""

from typing import Callable, Optional

from python.framework.autotrader.safety.risk_baseline_tracker import RiskBaselineTracker
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.loop.intake.live_event_intake import LiveEventIntake
from python.framework.persistence.algo_state_store import AlgoStateStore
from python.framework.persistence.position_book_watcher import PositionBookWatcher
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.trading_env.live.reconciler import Reconciler
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.loop_types.timer_kind_types import TimerKind


class AutotraderCadenceWork:
    """
    The cadence work of an AutoTrader session: comparing the book with the venue's (#151), saving
    the algo state (#354) and writing the carry-over (#355, #356).

    The reconcile and the save each have two gates — a tick count, checked after an instant with
    ticks, and a time, which is a timer on the session's timeline (#461). Whichever gate ran it, a
    run starts its timer again. The carry-over is written when the open book or the risk baseline
    has fallen behind.

    Args:
        config: AutoTrader configuration
        executor: The session's executor — its portfolio and the venue's references it takes back
        decision_logic: Whose state snapshot is saved
        reconciler: Compares the book with the venue's; None when the session has none
        state_store: Saves the algo state; None when the algo did not opt in
        risk_baseline: The risk baseline the carry-over carries; None without one
        persist_carry_over: Writes the carry-over and says whether it succeeded; None when there
            is no carry-over store
        logger: The session logger
        intake: The session's timeline, which the timers are armed on; None before the loop
            built it
        axis_now_msc: The receipt axis now, in UTC epoch milliseconds
    """

    def __init__(
        self,
        config: AutoTraderConfig,
        executor: AbstractTradeExecutor,
        decision_logic: AbstractDecisionLogic,
        reconciler: Optional[Reconciler],
        state_store: Optional[AlgoStateStore],
        risk_baseline: Optional[RiskBaselineTracker],
        persist_carry_over: Optional[Callable[[], bool]],
        logger: ScenarioLogger,
        intake: Callable[[], Optional[LiveEventIntake]],
        axis_now_msc: Callable[[], int],
    ):
        self._executor = executor
        self._decision_logic = decision_logic
        self._reconciler = reconciler
        self._state_store = state_store
        self._risk_baseline = risk_baseline
        self._logger = logger
        self._intake = intake
        self._axis_now_msc = axis_now_msc
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

    def reconcile_if_due(self, ticks_processed: int) -> None:
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
        self.reconcile_now(ticks_processed)

    def reconcile_now(self, ticks_processed: int) -> None:
        """
        Pull broker truth now, act on what came back, and schedule the next reconcile by time.

        Every run starts the timer again, whichever gate called it — the timeline's timer or the
        tick count — so the two never fire back to back.

        Args:
            ticks_processed: Current tick counter
        """
        if self._reconciler is None:
            return
        result = self._reconciler.reconcile(ticks_processed)
        if result.attributed_orders:
            self._executor.apply_order_attributions(result.attributed_orders)
        if result.broker_truth_due:
            self._executor.record_reconcile_truth(result)
        intake = self._intake()
        if intake is not None:
            intake.arm(TimerKind.RECONCILE, self._axis_now_msc() + round(
                self._reconciler.get_min_interval_seconds() * 1000))

    def persist_state_if_due(self, ticks_processed: int) -> None:
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
        self.persist_state_now(ticks_processed)

    def persist_state_now(self, ticks_processed: int) -> None:
        """
        Save the algo state snapshot now, and schedule the next save by time (#354, #461).

        Args:
            ticks_processed: Current tick counter
        """
        if self._state_store is None:
            return
        try:
            self._state_store.save(
                self._decision_logic.get_state_snapshot(), ticks_processed)
        except Exception as e:
            self._logger.error(f'Algo state save failed (continuing): {e}')
        intake = self._intake()
        if intake is not None:
            intake.arm(TimerKind.PERSISTENCE, self._axis_now_msc() + round(
                self._state_store.get_save_interval_seconds() * 1000))

    def persist_carry_over_if_needed(self, ticks_processed: int = 0) -> None:
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

    def write_carry_over(self) -> None:
        """
        Write the carry-over now, whatever changed — a sealed trading day's period number has to
        reach it (#476). A write that succeeded carried the book and the baseline as they stand,
        so neither is behind afterwards.
        """
        if self._persist_carry_over is not None and self._persist_carry_over():
            # The write carried the book and the baseline as they stand, so neither is behind
            self._book_watcher.accept(self._executor.portfolio.get_open_positions())
            self._persisted_baseline = (
                self._risk_baseline.get_baseline() if self._risk_baseline is not None else None)

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
