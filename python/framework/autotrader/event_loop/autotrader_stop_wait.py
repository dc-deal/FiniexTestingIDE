"""
FiniexTestingIDE - AutoTrader Stop Wait
How a stopping AutoTrader session waits for the orders on their way (#630).
"""

import time
from datetime import datetime, timedelta, timezone
from python.framework.autotrader.event_loop.autotrader_cadence_work import AutotraderCadenceWork
from python.framework.autotrader.operator_alerts.loop_progress import LoopProgress
from typing import Callable, List, Optional

from python.framework.autotrader.operator_alerts.operator_alert_messages import (
    orders_unanswered_at_stop_text,
)
from python.framework.autotrader.operator_alerts.operator_alerts import OperatorAlerts
from python.framework.autotrader.shutdown_status_holder import ShutdownStatusHolder
from python.framework.autotrader.stop_budget import StopBudget
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.loop.abstract_live_pass_hooks import AbstractLivePassHooks
from python.framework.loop.loop_context import LoopContext
from python.framework.loop.loop_event_dispatcher import (
    dispatch_live_order_wait,
)
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.trading_env.decision_event_dispatcher import DecisionEventDispatcher
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.operator_alert_types import (
    OperatorAlertKind,
    OperatorAlertLevel,
)
from python.framework.types.autotrader_types.shutdown_status_types import endings_by_order
from python.framework.types.decision_event_types import SessionEndSeverity
from python.framework.types.trading_env_types.awaited_order_types import AwaitedOrder


class AutotraderStopWait:
    """
    A normal stop's wait for the orders on their way, before the session-end event and after it
    (#630, #642).

    Wait passes run on the heartbeat's cadence until nothing is left or the stop's bound runs out;
    what is left then is booked as given up while the logic can still hear it, and the operator
    is told. Both waits keep to one bound.

    Args:
        config: AutoTrader configuration — its shutdown settings
        executor: The session's executor — the orders on their way and their endings
        loop_context: What every pass of the loop core works with
        hooks: The session's steps inside a pass, handed to the loop core
        cadence: Writes the carry-over a wait pass may have moved
        shutdown_status: What every surface of the stop reads, which the wait writes to
        open_stop_budget: Opens what a stop may still spend, from the instant it was asked for;
            None opens one at the moment it is needed, from the configured settings
        operator_alerts: What the operator hears out of band (#235)
        loop_progress: The progress the liveness ping vouches for, stamped on every wait pass
        decision_event_dispatcher: Delivers what a give-up booked to the logic; None without one
        push_pulse_frame: Hands the display a pulse frame, given the session's tick count
        heartbeat_interval_s: How long a wait pass sleeps before the next one
        logger: The session logger
    """

    def __init__(
        self,
        config: AutoTraderConfig,
        executor: AbstractTradeExecutor,
        loop_context: LoopContext,
        hooks: AbstractLivePassHooks,
        cadence: AutotraderCadenceWork,
        shutdown_status: ShutdownStatusHolder,
        open_stop_budget: Optional[Callable[[], StopBudget]],
        operator_alerts: OperatorAlerts,
        loop_progress: LoopProgress,
        decision_event_dispatcher: Optional[DecisionEventDispatcher],
        push_pulse_frame: Callable[[int], None],
        heartbeat_interval_s: float,
        logger: ScenarioLogger,
    ):
        self._config = config
        self._executor = executor
        self._loop_context = loop_context
        self._hooks = hooks
        self._cadence = cadence
        self._shutdown_status = shutdown_status
        self._open_stop_budget = open_stop_budget or self._stop_budget_from_now
        self._operator_alerts = operator_alerts
        self._loop_progress = loop_progress
        self._decision_event_dispatcher = decision_event_dispatcher
        self._push_pulse_frame = push_pulse_frame
        self._heartbeat_interval_s = heartbeat_interval_s
        self._logger = logger
        # What a stop's waits for orders have spent: the wait after the session-end event keeps to
        # the same bound as the one before it, and does not wait once that one ran out
        self._order_wait_spent_s = 0.0
        self._order_wait_ran_out = False

    def wait_for_orders(self, ticks_processed: int, after_session_end: bool = False) -> None:
        """
        Let the orders on their way settle before the session-end event, and what the logic sent
        from that event before the cleanup.

        With nothing on its way this costs no pass and no line. Otherwise wait passes run on the
        idle pass's cadence until nothing is left or the bound runs out. The bound is re-read on
        each pass, because what a declared kill leaves shrinks as the wait runs; a pass can overrun
        it, because a heartbeat's reads of the venue are synchronous. The fill timeout does not act
        once a stop is under way: this bound decides.

        No gate on new orders: a hook may still send one, and the same bound then covers it. The
        wait after the session-end event goes on from the time the one before it spent, so both
        keep to one bound, and it tells the logic nothing.

        Args:
            ticks_processed: The session's tick count
            after_session_end: Whether this is the wait after the session-end event
        """
        if not self._stop_waits_for_orders():
            return
        awaited = self._executor.get_awaited_orders()
        if not awaited:
            return

        settings = self._config.shutdown
        stop_budget = self._open_stop_budget()
        still_to_come_s = settings.bounded_waits_s()
        # Never the wall clock here: a replayed session's clock stands on its last tick, and a
        # wall-clock reading would seal one booking period per day up to today. The last event
        # plus the time elapsed is the same thing in a live session, near enough — and what a
        # replay's idle pass reckons too (#616).
        last_event = self._executor.get_current_time()
        run_began = time.monotonic()
        spent_before_s = self._order_wait_spent_s
        self._shutdown_status.order_wait_began(
            awaited, run_began - spent_before_s,
            spent_before_s + stop_budget.order_wait_bound_s(
                settings.order_wait_s - spent_before_s, still_to_come_s),
            after_session_end, spent_before_s)
        hear = not after_session_end
        while awaited and self._stop_waits_for_orders():
            waited_s = spent_before_s + time.monotonic() - run_began
            bound_s = stop_budget.order_wait_bound_s(
                settings.order_wait_s - waited_s, still_to_come_s)
            if bound_s <= 0.0:
                self._order_wait_spent_s = waited_s
                self._order_wait_ran_out = True
                self._give_up_on_awaited_orders(waited_s, stop_budget, hear)
                return
            self._run_order_wait_pass(
                ticks_processed, last_event + timedelta(seconds=time.monotonic() - run_began),
                hear)
            before_pass = awaited
            awaited = self._executor.get_awaited_orders()
            # Read after the pass: a pass that holds a read of the venue takes its time
            waited_s = spent_before_s + time.monotonic() - run_began
            bound_s = stop_budget.order_wait_bound_s(
                settings.order_wait_s - waited_s, still_to_come_s)
            self._report_order_wait(awaited, before_pass, waited_s, bound_s, ticks_processed)
            if awaited and bound_s > 0.0:
                time.sleep(min(self._heartbeat_interval_s, bound_s))
        self._order_wait_spent_s = spent_before_s + time.monotonic() - run_began
        self._shutdown_status.order_wait_ended(self._order_wait_spent_s)

    def has_run_out(self) -> bool:
        """
        Whether a wait ran out of its bound — the wait after the session-end event then does not
        wait at all.

        Returns:
            True once a wait gave up on what was left
        """
        return self._order_wait_ran_out

    def _stop_budget_from_now(self) -> StopBudget:
        """
        The stop budget of a loop nobody handed an opener, from this instant.

        Returns:
            The budget, under the configured settings
        """
        settings = self._config.shutdown
        asked_at_s = time.monotonic()
        self._shutdown_status.note_asked(
            self._executor.get_session_end_reason(), datetime.now(timezone.utc), asked_at_s)
        return StopBudget(settings.supervisor_kill_after_s, settings.reserve_for_records_s,
                          asked_at_s)

    def _stop_waits_for_orders(self) -> bool:
        """
        Whether this end of the loop waits for the orders on their way (#630).

        A session-end request of normal severity does: a SIGTERM, a first Ctrl+C, a strategy's own
        end. A hard stop's EMERGENCY has its drain of its own and does not, and a tick source that
        ran out made no request, so a replayed mock session ends as it always did.

        Returns:
            True when it waits
        """
        return (self._executor.is_session_end_requested()
                and self._executor.get_session_end_severity() is SessionEndSeverity.NORMAL)

    def _run_order_wait_pass(self, ticks_processed: int, now: datetime, hear: bool) -> None:
        """
        One pass of a stop's wait: what an idle pass does for the orders, and nothing for the algo.

        Stamps the liveness, so the monitor stays quiet, and hands the pass to the loop core (#458).
        No tick, no ghost pass, no signal inbox, no reconcile and no state frame: the decisions have
        stopped, and a write is never repeated.

        Args:
            ticks_processed: The session's tick count
            now: The clock of this pass
            hear: Whether the logic hears what this pass settles; not after the session-end event
        """
        self._loop_progress.stamp()
        dispatch_live_order_wait(now, self._loop_context, self._hooks, hear)
        self._cadence.persist_carry_over_if_needed(ticks_processed)

    def _report_order_wait(self, awaited: List[AwaitedOrder], before_pass: List[AwaitedOrder],
                           waited_s: float, bound_s: float, ticks_processed: int) -> None:
        """
        Say what a wait pass knows — the one place a surface of the stop reads the wait from.

        Args:
            awaited: What is still on its way after this pass
            before_pass: What was on its way before it
            waited_s: How long the wait has run, read after the pass
            bound_s: How much longer it may run, from then
            ticks_processed: The session's tick count
        """
        self._push_pulse_frame(ticks_processed)
        still_on_its_way = {row.key() for row in awaited}
        settled = [row for row in before_pass if row.key() not in still_on_its_way]
        endings = (endings_by_order([row.key() for row in settled],
                                    self._executor.get_order_history())
                   if settled else {})
        self._shutdown_status.order_wait_passed(
            awaited, [(row, endings[row.key()]) for row in settled], waited_s, bound_s)

    def _give_up_on_awaited_orders(self, waited_s: float, stop_budget: StopBudget,
                                   hear: bool = True) -> None:
        """
        A stop's wait ran out: book what is left while the logic can still hear it, and say so.

        Args:
            waited_s: How long the stop waited
            stop_budget: What the stop may still spend
            hear: Whether the logic hears what the give-up booked; not after the session-end event
        """
        self._shutdown_status.order_wait_ran_out(self._executor.get_awaited_orders(), waited_s)
        given_up = self._executor.give_up_on_awaited_orders(waited_s)
        if hear:
            self._drain_decision_events()
        # A hook's order sent just now cannot go out, because the worker has stopped: it is
        # withdrawn, and the logic hears that with the session-end event
        self._executor.withdraw_unsent_orders()
        left = self._executor.get_awaited_orders()
        self._shutdown_status.order_wait_given_up(given_up, left, waited_s)
        if not given_up and not left:
            return
        status = self._shutdown_status.get()
        try:
            title, detail = orders_unanswered_at_stop_text(
                given_up, left, waited_s, status.kill_text() if status is not None else None)
        except Exception as error:   # noqa: BLE001 — a message must never end a stop
            self._logger.error(
                f'📣 The message for the orders unanswered at the stop could not be written '
                f'({type(error).__name__}: {error})')
            return
        self._operator_alerts.send(
            OperatorAlertKind.ORDERS_UNANSWERED_AT_STOP, OperatorAlertLevel.WARNING, title, detail)

    def _drain_decision_events(self) -> None:
        """Drain buffered decision events to the algo hooks, if a dispatcher is active (#348)."""
        if self._decision_event_dispatcher is not None:
            self._decision_event_dispatcher.drain()
