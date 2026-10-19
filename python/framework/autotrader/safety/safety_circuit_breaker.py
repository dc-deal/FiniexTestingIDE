"""
FiniexTestingIDE - Safety Circuit Breaker (#356, #314)

The safety layer of an AutoTrader session: the soft breaker that blocks new entries, the hard stop
that closes everything and ends the session, the daily loss limit with its own day baseline, and
the record of what the breaker saw for the end-of-session report. The tick loop hands it one
account value per tick and asks whether entries are blocked; everything the breaker decides on
lives here, so the layer #358 certifies with real money is one unit.
"""

from datetime import date
from typing import Callable, List, Optional

from python.framework.autotrader.operator_alerts.alert_episode import (
    AlertEpisode,
    all_clear_detail,
)
from python.framework.autotrader.operator_alerts.operator_alerts import OperatorAlerts
from python.framework.autotrader.safety.risk_baseline_tracker import RiskBaselineTracker
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_display_types import SafetyState
from python.framework.types.autotrader_types.operator_alert_types import (
    OperatorAlertKind,
    OperatorAlertLevel,
)
from python.framework.types.autotrader_types.safety_session_types import (
    FlattenOutcome,
    SafetyDayRecord,
    SafetySessionRecord,
)
from python.framework.types.config_types.market_config_types import TradingModel
from python.framework.types.decision_event_types import SessionEndSeverity
from python.framework.types.persistence_types import (
    BaselineKind,
    BaselineOrigin,
    BaselineQuantities,
)

# How many ticks the hard stop keeps draining before it ends the session with positions
# still open (#356). A close is asynchronous: it is sent on one tick and its fill arrives on
# a later one, so ending immediately would report a flat book that is not flat. The window
# is generous because the alternative — exiting early — is the failure it exists to prevent,
# and it is BOUNDED because a venue that never answers must not hold the session open for
# the rest of the month. What happens at the end of it is not a silent exit: every position
# still open is named into the error pot (§35).
_FLATTEN_DRAIN_TICKS = 200


class SafetyCircuitBreaker:
    """
    The session's circuit breaker: soft block, hard stop, daily loss, and their record.

    Args:
        config: AutoTrader configuration — its `safety` block and the capital declaration
        executor: The session's executor — the portfolio it values, the closes it sends, the
            session end it requests, and the canonical clock
        trading_model: SPOT or MARGIN — which floor applies and whether the hard stop may sell
        risk_baseline: The session denominator every limit measures against, None without one
        logger: Session logger — what the breaker does reaches the error pot (§35)
        operator_alerts: The out-of-band channels to the operator (#235)
        all_clear_after_s: How long the breaker must stay clear before its all-clear goes out
        trading_day: The trading day the canonical clock stands in, None before it is first set
        on_hard_stop: Called with the reason the moment the hard stop fires — the seam #590's
            latch attaches to; None while nothing listens
    """

    def __init__(
        self,
        config: AutoTraderConfig,
        executor: AbstractTradeExecutor,
        trading_model: TradingModel,
        risk_baseline: Optional[RiskBaselineTracker],
        logger: ScenarioLogger,
        operator_alerts: OperatorAlerts,
        all_clear_after_s: float,
        trading_day: Callable[[], Optional[date]],
        on_hard_stop: Optional[Callable[[str], None]] = None,
    ):
        self._config = config
        self._executor = executor
        self._trading_model = trading_model
        self._risk_baseline = risk_baseline
        self._logger = logger
        self._operator_alerts = operator_alerts
        self._trading_day = trading_day
        self._on_hard_stop = on_hard_stop
        # One episode for the breaker: its opening alert, then the all-clear only once it stayed
        # clear for the quiet period — a breaker flapping at its threshold is one episode
        self._safety_alert = AlertEpisode(all_clear_after_s)

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
        self._flatten_outcome: Optional[FlattenOutcome] = None
        self._flatten_unconfirmed: List[str] = []
        self._flatten_kept: List[str] = []

    def get_display_state(self) -> SafetyState:
        """Bundle the current circuit-breaker state for the display exporter."""
        return SafetyState(
            blocked=self._safety_blocked,
            reason=self._safety_reason,
            current_value=self._safety_current_value,
            drawdown_pct=self._safety_drawdown_pct,
        )

    def baseline_value(self) -> float:
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

    def update_day_baseline(
        self,
        value: float,
        mark_price: Optional[float],
        quantities: Optional[BaselineQuantities],
    ) -> None:
        """
        Start a new daily baseline when the market crosses into a new trading day (#314).

        The day comes from the canonical clock and this market's anchor (#476), never from
        the wall clock and no longer from the tick stamp: both event sources advance that
        clock, so a mock session and a live-adapter session still answer alike, and a quiet
        feed over the boundary no longer hides it. The check stays deliberately independent of
        the session log's rotation (`SessionLogRotator`): that one rotates a LOG file and returns
        early when there is no run directory, and a loss limit must not depend on whether logs
        are being written.

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

    def observe_excursion(self, current_value: float, baseline: float) -> None:
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

    def check_safety(self, current_value: float, initial_balance: float) -> None:
        """
        Evaluate circuit breaker conditions and update safety state.

        Soft stop: sets _safety_blocked flag, and while it holds the tick loop forces each
        decision's action to FLAT — no new entry, and no close the strategy expresses as an
        action. The breaker closes nothing itself.

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
                    f'max_drawdown_pct ({drawdown_pct:.1f}% > {safety.max_drawdown_pct:.1f}%)')
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
            if self._safety_alert.opens(self._executor.get_current_time()):
                self._operator_alerts.send(
                    OperatorAlertKind.SAFETY_BLOCKED, OperatorAlertLevel.WARNING,
                    'Safety circuit breaker — new entries blocked',
                    f'{self._safety_reason} · '
                    f'{len(self._executor.portfolio.get_open_positions())} position(s) open, kept')
        elif was_blocked and not self._safety_blocked:
            self._logger.info('✅ Safety circuit breaker cleared')
            self._safety_alert.clears(self._executor.get_current_time())
        if (self._safety_alert.is_open()
                and self._safety_alert.all_clear_due(self._executor.get_current_time())):
            self._operator_alerts.send(
                OperatorAlertKind.SAFETY_CLEARED, OperatorAlertLevel.INFO,
                'Safety circuit breaker cleared — entries allowed again',
                all_clear_detail(self._safety_alert))

    def check_emergency_flatten(
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
        `drive_emergency_flatten` for why that is a second step rather than the next line.

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
        # #590's latch attaches here, so a restart does not trade on after a hard stop; nothing
        # passes the seam until that issue is built
        if self._on_hard_stop is not None:
            self._on_hard_stop(reason)
        self._send_emergency_closes(reason, ticks_processed)

    def drive_emergency_flatten(self, ticks_processed: int) -> None:
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
            self._flatten_outcome = FlattenOutcome.COMPLETE
            self._executor.request_session_end(
                f'emergency flatten complete ({self._flatten_reason})',
                SessionEndSeverity.EMERGENCY)
            self._flatten_sent_at_tick = None
            return

        if self._flatten_sent_at_tick < 0:
            # Spot holdings kept on purpose (safety.spot_liquidate_to_quote is off): no close was
            # sent, and the first error line already said the holdings are not sold. The session
            # ends — without a second alarm that would invite selling by hand, which realises
            # exactly the loss the switch exists to keep unrealised (#235). The record says KEPT,
            # never "not confirmed": that phrase asks the operator to check the account by hand.
            self._flatten_outcome = FlattenOutcome.HOLDINGS_KEPT
            self._flatten_kept = [p.position_id for p in open_positions]
            self._executor.request_session_end(
                f'emergency stop, spot holdings kept ({self._flatten_reason})',
                SessionEndSeverity.EMERGENCY)
            self._flatten_sent_at_tick = None
            return

        waited = ticks_processed - self._flatten_sent_at_tick
        if waited >= _FLATTEN_DRAIN_TICKS:
            self._flatten_outcome = FlattenOutcome.UNCONFIRMED
            self._flatten_unconfirmed = [p.position_id for p in open_positions]
            still_open = ', '.join(p.position_id for p in open_positions)
            self._logger.error(
                f'🚨 EMERGENCY STOP ending the session with {len(open_positions)} '
                f'position(s) still open: {still_open}. They were not confirmed closed '
                f'within the drain window — check the account by hand.')
            self._operator_alerts.send(
                OperatorAlertKind.EMERGENCY_STOP, OperatorAlertLevel.CRITICAL,
                'EMERGENCY STOP incomplete — check the account by hand',
                f'{len(open_positions)} position(s) not confirmed closed: {still_open}')
            self._executor.request_session_end(
                f'emergency flatten incomplete ({self._flatten_reason})',
                SessionEndSeverity.EMERGENCY)
            self._flatten_sent_at_tick = None

    def entries_blocked(self) -> bool:
        """
        Whether a decision is forced FLAT now — by the soft block, or once the hard stop fired.

        The hard stop blocks entries as well (#356): it is the stronger stop, and must not need a
        soft threshold configured to do what the soft one does. Its closes are draining, and an
        entry during the drain would keep the book from ever reaching flat.

        Returns:
            True while every decision's action is forced FLAT — no new entry, and no close the
            strategy expresses as an action
        """
        return self._safety_blocked or bool(self._flatten_reason)

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
        flatten_outcome = self._flatten_outcome
        flatten_unconfirmed = list(self._flatten_unconfirmed)
        if flatten_fired and flatten_outcome is None:
            flatten_outcome = FlattenOutcome.UNCONFIRMED
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
            flatten_outcome=flatten_outcome,
            flatten_unconfirmed=flatten_unconfirmed,
            flatten_kept=list(self._flatten_kept),
        )

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
                f'stays unrealized.')
            self._operator_alerts.send(
                OperatorAlertKind.EMERGENCY_STOP, OperatorAlertLevel.CRITICAL,
                'EMERGENCY STOP — the session ends',
                f'{reason}. {len(positions)} spot holding(s) are NOT sold '
                f'(safety.spot_liquidate_to_quote is off).')
            self._flatten_sent_at_tick = -1
            return

        self._logger.error(
            f'🚨 EMERGENCY STOP: {reason}. Closing {len(positions)} open position(s) now, '
            f'then ending the session. This is the HARD stop, not the entry block.')
        self._operator_alerts.send(
            OperatorAlertKind.EMERGENCY_STOP, OperatorAlertLevel.CRITICAL,
            'EMERGENCY STOP — closing everything, then the session ends',
            f'{reason}. Closing {len(positions)} open position(s).')
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
                    f'max_daily_loss_pct ({daily_loss_pct:.1f}% > '
                    f'{safety.max_daily_loss_pct:.1f}%)')
                self._day_limit_hit = True
