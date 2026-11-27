"""
FiniexTestingIDE - AutoTrader Trading Days
How an AutoTrader session closes its trading days: the booking periods, their seal and what follows.
"""

from datetime import date
from pathlib import Path
from python.framework.autotrader.event_loop.autotrader_cadence_work import AutotraderCadenceWork
from python.framework.types.autotrader_types.autotrader_loop_state_types import AutotraderLoopState
from typing import Callable, List, Optional

from python.framework.autotrader.operator_alerts.operator_alert_messages import (
    day_closed_text,
    days_closed_unheard_text,
)
from python.framework.autotrader.operator_alerts.operator_alerts import OperatorAlerts
from python.framework.autotrader.safety.safety_circuit_breaker import SafetyCircuitBreaker
from python.framework.autotrader.session_log_rotator import SessionLogRotator
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.reporting.booking_period_recorder import (
    BookingPeriodRecorder,
    snapshot_from_executor,
)
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.types.autotrader_types.operator_alert_types import (
    OperatorAlertKind,
    OperatorAlertLevel,
)
from python.framework.types.config_types.market_config_types import DayAnchorConfig, TradingModel
from python.framework.types.log_level import LogLevel
from python.framework.types.persistence_types import BaselineQuantities
from python.framework.types.run_results_types import BookingPeriod, PeriodCloseReason
from python.framework.utils.trading_day_anchor import boundary_opening


class AutotraderTradingDays:
    """
    The trading days of an AutoTrader session (#476, #537).

    The booking period recorder owns the day; this closes what its seal sets off — the daily loss
    limit's day rolls, the ledger and the carry-over book the day, the session's streams are
    sealed into day records, the session log moves to the next day, and the operator hears how the
    day closed (#235).

    Args:
        executor: The session's executor — its portfolio, its records and its clock
        booking: The session's booking period recorder
        safety: The safety layer, whose daily loss limit rolls with the day
        cadence: Writes the carry-over a sealed day's period number has to reach
        trading_model: Margin or spot
        day_anchor: Where this market's trading day flips
        run_dir: The session's run directory, for the session log's rotation
        operator_alerts: What the operator hears out of band (#235)
        daily_digest: Whether the operator hears each trading day's close
        book_sealed_periods: Writes the booking periods sealed so far into the results ledger;
            None writes nothing before the session ends
        seal_streams: Seals the session's streams into day records, given the day that ended and
            the day that opened; None seals nothing
        logger: The session logger
        state: What the event loop's units share while it runs
    """

    def __init__(
        self,
        executor: AbstractTradeExecutor,
        booking: BookingPeriodRecorder,
        safety: SafetyCircuitBreaker,
        cadence: AutotraderCadenceWork,
        trading_model: TradingModel,
        day_anchor: DayAnchorConfig,
        run_dir: Optional[Path],
        operator_alerts: OperatorAlerts,
        daily_digest: bool,
        book_sealed_periods: Optional[Callable[[List[BookingPeriod]], None]],
        seal_streams: Optional[Callable[[date, date], None]],
        logger: ScenarioLogger,
        state: AutotraderLoopState,
    ):
        self._executor = executor
        self._booking = booking
        self._safety = safety
        self._cadence = cadence
        self._trading_model = trading_model
        self._day_anchor = day_anchor
        self._operator_alerts = operator_alerts
        # Each trading day's close, rendered for the operator from the period it sealed
        self._daily_digest = daily_digest
        self._book_sealed_periods = book_sealed_periods
        self._seal_streams = seal_streams
        self._logger = logger
        self._state = state
        # The trading days one boundary check sealed, closed together once the check is done:
        # a check several days late seals one period per day, and booking each on its own would
        # rewrite the ledger fragment once per day inside one pass (#476)
        self._sealed_in_check: List[BookingPeriod] = []
        # The session log rotates on this market's trading day — the same day the booking
        # period and the daily loss limit read
        self._log_rotator = SessionLogRotator(run_dir, logger, self.get_trading_day)

    def get_trading_day(self) -> Optional[date]:
        """
        The trading day the session runs on — the one its open booking period books (#476).

        The log rotation and the daily loss limit read it here, so all three agree on where a
        day ends, and on which days a market that closes has at all.

        Returns:
            The trading day's date, or None before the first period opened
        """
        return self._booking.get_current_day()

    def seal_source(self):
        """
        The records and the stock reading a seal needs, at this instant.

        Handed to the recorder as a CALLABLE rather than as values: a boundary check runs on
        every pass and almost never seals, so the portfolio is only read, and the period's order
        counts only taken, when a period actually closes.

        Returns:
            (trade records, PeriodSnapshot)
        """
        return snapshot_from_executor(self._executor)

    def get_highest_period_no(self) -> int:
        """
        The largest booking period this session has sealed (#537).

        Read by the carry-over so the next session continues the count.

        Returns:
            The high-water mark, which is the inherited floor when nothing was sealed
        """
        return self._booking.get_highest_period_no()

    def on_period_sealed(self, period: BookingPeriod) -> None:
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

    def close_sealed_days(self) -> None:
        """
        Close the trading days the boundary check just sealed, then move the session log onto the
        day that opened (#476).
        """
        self._close_sealed_days()
        # === DAILY LOG ROTATION ===
        # On the booking period's day, which the boundary check before this hook advanced —
        # after it, so the line that sealed the day stays in that day's file. On a tick pass
        # it runs before the tick, so what the tick logs (a fill, a triggered stop) is in the
        # file of the day it is booked to; on an idle or a wait pass it runs before the
        # heartbeat, so a feed that goes quiet across the rollover still rotates. In a
        # live-adapter session the idle pass has almost always rotated first; the tick pass
        # covers a replay whose queue never runs empty. A booking does not depend on whether
        # logs are written.
        self._log_rotator.check_daily_rotation()

    def _close_sealed_days(self) -> None:
        """
        What the trading days one boundary check sealed set off, once for all of them (#476).

        The days are booked into the ledger and their number into the carry-over, the session's
        streams are sealed into day records, the log buffer lets go of their ordinary lines, and
        the operator hears how the day closed (#235). Once and not per day: a check several days
        late — a machine that slept, a replay whose clock jumped to the present (#616) — seals
        one period per day, and each would otherwise rewrite the whole ledger fragment and send
        its own message within one pass.
        """
        if not self._sealed_in_check:
            return
        sealed, self._sealed_in_check = self._sealed_in_check, []
        self._book_sealed_day()
        self._seal_streams_of(sealed[0])
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
        self._cadence.write_carry_over()

    def _seal_streams_of(self, period: BookingPeriod) -> None:
        """
        Seal the session's streams into the day records of the first trading day a check sealed
        (#476) — once, however many days the check sealed: in the days a late check passed over
        the loop did not run, so nothing was written in them.

        Args:
            period: The first booking period the check sealed
        """
        next_day = self._booking.get_current_day()
        if self._seal_streams is None or period.trading_day is None or next_day is None:
            return
        self._seal_streams(period.trading_day, next_day)

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
                and self._state.last_real_tick is not None):
            mark_price = self._state.last_real_tick.mid
            quantities = self.spot_quantities()
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
            self._executor.get_current_time_if_set(), self.seal_source)

    def has_opened_day_log(self) -> bool:
        """
        Whether a pass of this loop has opened a trading day's session log.

        Returns:
            True once the first day's log is open
        """
        return self._log_rotator.has_opened_day_log()

    def spot_quantities(self) -> BaselineQuantities:
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
