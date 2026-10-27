"""
Booking-period recorder (#537) — the state a booking period needs while it is still open.

The ledger is written from records, but it cannot be written from records ALONE: a period's
equity band is a running observation, its boundary is an event, and its number continues across
a restart. That state has to live somewhere during the run, and it has to live in ONE place —
the two tick loops are shaped differently (the AutoTrader's is a class, the simulation's a
function), so a recorder each would be two implementations of one rule, diverging on the day
somebody fixes only the one they are looking at (§19).

**Both event sources drive it.** `check_boundary` is called from the tick path AND the heartbeat,
because in an illiquid gap only the heartbeat advances the canonical clock — a feed that goes
quiet across a rollover would otherwise book a period that silently spans two days.

**Where a period ends (#476).** A trading day ends at its boundary instant, BEFORE the first event
at or after it is applied: that event's fills, trades and valuation belong to the new day, on the
tick path and the heartbeat alike, so it no longer matters which of the two crossed the boundary.
The seal is stamped with the boundary itself rather than with the moment it was noticed, and it
reads the account as the last event left it. Which days get a period is the market calendar's
answer: every day for a market that never closes, only its trading days for one that does — and a
trading day the run never heard from still gets its row, so a backtest without heartbeats and a
live session over the same days book the same rows.

**The write is NOT here.** The recorder collects; the report coordinator writes every period at
once when the run ends. That keeps the parquet write out of what the throughput benchmark
measures, and it is the only shape the simulation can have at all — a scenario runs in a
subprocess and carries its periods back over the process bridge like every other result.

What deliberately stays in the loop is the ARITHMETIC: a period is derived at its seal, while the
records it comes from are still in the bounded deque. Derived afterwards, a long run's first
periods would be periods whose records are gone.
"""

from datetime import date, datetime, timedelta
from typing import Callable, List, Optional

from python.framework.reporting.builders.booking_period_builder import (
    PeriodSnapshot,
    derive_booking_period,
    describe_segment,
)
from python.framework.types.config_types.market_config_types import DayAnchorConfig
from python.framework.types.run_results_types import BookingPeriod, PeriodCloseReason
from python.framework.utils.market_calendar import MarketCalendar
from python.framework.utils.trading_day_anchor import (
    boundary_opening,
    trade_date_of,
    trading_day_of,
)

# How far ahead the next trading day is looked for. The calendar closes a market for a weekend
# and a holiday at most — three dates in a row — so a search that runs out has met a calendar
# that is wrong, and saying so beats booking a period that spans a fortnight.
_TRADING_DAY_SEARCH_LIMIT = 14


class BookingPeriodRecorder:
    """
    Collects one run unit's booking periods while the unit is still running.

    Args:
        unit_name: The run unit this books for — a scenario in the simulation, the session in
            live. It is part of the period's identity, not a label: a run's scenarios cover
            different windows, so only "day 1 of this unit" is a thing
        anchor: Where this market's trading day flips (§47), resolved once by the caller. None
            when the unit declares no market — an internal harness builds scenarios without a
            broker, and a unit with no market has no trading day to book against. The recorder
            then collects nothing rather than refusing: refusing would make a legitimate test
            fixture unrunnable to protect a figure it was never going to produce
        carried_period_no: The FLOOR this unit continues from. An AutoTrader session inherits
            it from the cold-start carry-over so a deployment's numbering survives a restart; a
            simulation scenario starts at 0, because a backtest has no history to inherit
        log: Where the one line per seal goes, or None to stay silent. That line carries every
            figure the ledger row will — it is the safety net for the deferred write, so a
            process that dies before the report phase still leaves its periods recoverable
        on_seal: Called with each period once it is filed, or None. A live session renders it
            for its operator (#235) and rolls its daily loss limit on it; the simulation passes
            nothing
        weekend_closure: Whether this market closes — resolved by the caller from the market's
            rules, because the calendar does not know the market type. True books only the
            calendar's trading days; False books every day
    """

    def __init__(
        self,
        unit_name: str,
        anchor: Optional[DayAnchorConfig],
        carried_period_no: int = 0,
        log: Optional[Callable[[str], None]] = None,
        on_seal: Optional[Callable[[BookingPeriod], None]] = None,
        weekend_closure: bool = False,
    ):
        self._unit_name = unit_name
        self._anchor = anchor
        self._period_no = carried_period_no
        self._log = log
        self._on_seal = on_seal
        self._weekend_closure = weekend_closure
        self._segments: List[BookingPeriod] = []
        self._opened_at: Optional[datetime] = None
        # The trading day the open period books. After a market's closed days it is the next
        # day the market trades, while the period itself opened where the last one ended.
        self._current_day: Optional[date] = None
        # The instant the CURRENT trading day ends, cached so the per-tick check is a datetime
        # comparison rather than a timezone conversion. Derived from `_current_day`, so the two
        # are set together and never apart.
        self._next_boundary: Optional[datetime] = None
        # The band INSIDE the current period. The peak is kept separately from the maximum
        # because a drawdown is measured against the peak that stood AT THE TIME, while the
        # maximum is the period's own high — the two diverge the moment the account recovers.
        self._peak_equity: Optional[float] = None
        self._max_equity: float = 0.0
        self._min_equity: float = 0.0
        # What the open period began with: the previous period's close, or — for a unit's first
        # period — its first observed value. Set at a seal from the value the seal READ, so it
        # does not depend on which event crossed the boundary: a tick is checked before it is
        # applied, and a heartbeat seal observes nothing.
        self._opening_equity: Optional[float] = None
        self._max_drawdown: float = 0.0
        # How many ticks the open period applied — zero says the market was not heard in it
        self._tick_count: int = 0

    def get_highest_period_no(self) -> int:
        """
        The largest period number sealed so far, including the inherited floor.

        Deliberately not the number of collected periods: a restart inherits a floor, so the
        first period of the second session is 4 when the first ended at 3, and the list holds
        one entry.

        Returns:
            The high-water mark
        """
        return self._period_no

    def get_sealed_periods(self) -> List[BookingPeriod]:
        """
        Every period sealed so far, oldest first — what a running session writes to its ledger
        fragment at each seal (#476).

        Returns:
            A copy of the list
        """
        return list(self._segments)

    def get_current_day(self) -> Optional[date]:
        """
        The trading day the open period books — the day a session's log and its daily loss
        limit run on too, so the three cannot disagree about where a day ends.

        Returns:
            The trading day's label, or None before the first period opened
        """
        return self._current_day

    def observe_tick(self, value: Optional[float]) -> None:
        """
        Record a tick the period applied, and where the account stands after it.

        The VALUATION half of a period, and it is what the realised figures cannot say: a
        position opened on Monday and closed on Tuesday books its whole result on Tuesday, while
        the account moved on both days.

        The low is TRACKED rather than derived. A period that ran 100 → 90 → 120 has a peak of
        120, a decline of 10 against the peak that stood then, and a low of 90 — so
        `peak - drawdown` would answer 110, a value that never occurred.

        Args:
            value: The account value after the tick, or None when the holdings cannot be valued
                yet — the tick still counts, because the market was heard
        """
        self._tick_count += 1
        if value is None:
            return
        if self._opening_equity is None:
            self._opening_equity = value
        self._fold(value)

    def _fold(self, value: float) -> None:
        """
        Fold one reading of the account into the open period's band.

        Args:
            value: The account value at this instant
        """
        if self._peak_equity is None:
            self._peak_equity = value
            self._max_equity = value
            self._min_equity = value
        self._peak_equity = max(self._peak_equity, value)
        self._max_equity = max(self._max_equity, value)
        self._min_equity = min(self._min_equity, value)
        # A MAGNITUDE, like `account_max_drawdown`, `largest_mae` and `largest_mfe` beside it.
        # It was the one signed figure among them until #539. The ledger combined it correctly
        # either way (`Reduction.MAX_ABS` combines by magnitude), so nothing was ever wrong in the
        # arithmetic — what it cost was READING: a consumer renders this table beside the
        # portfolio block, and one shared formatter turns one of the two into its own opposite
        # without anything going red. The sign is a DISPLAY decision and lives in the renderers.
        decline = self._peak_equity - value
        if decline > self._max_drawdown:
            self._max_drawdown = decline

    def check_boundary(self, now: Optional[datetime], snapshot_for_seal) -> None:
        """
        Open the first period, or seal the running one when its trading day has ended.

        Called from BOTH event sources, on EVERY tick, which is what shapes the code below.
        Nothing happens before the canonical clock has been set — a period needs an instant to
        open at, and a clock that was never set has none. Nothing happens without an anchor
        either: a unit with no declared market cannot say where its trading day flips, so it
        never opens a period and therefore never seals one.

        **Called BEFORE the event at `now` is applied (#476).** A day ends at its boundary, and
        the seal reads the account as the last event left it — so the event that crossed the
        boundary, its fills and its valuation all fall into the day it opened. The period is
        stamped closed AT the boundary, whichever event noticed it and however late.

        **Every trading day gets its period.** An instant several boundaries past the last one —
        a data gap, a feed that came back after days — seals the day that was running and then
        one period for each trading day in between, with nothing realised, the valuation it was
        left at and no tick. The closed days of a market are skipped: they are not trading
        days, and the period after them opens where the last trading day ended, so no stretch
        of the run falls between two periods.

        **The cached boundary is the whole point of this method's shape.** Asking
        `trading_day_of` per tick means a timezone conversion per tick: measured 1.52 µs at UTC
        and 1.87 µs at America/New_York, which over a 1.5-million-tick benchmark run is 2.3-2.8 s
        against a 23.4 s baseline — about a tenth of the tick loop, spent re-deriving a date that
        changes once a day. The instant at which the current day ENDS is computed once per period
        instead, and the hot path is one datetime comparison.

        Args:
            now: The canonical clock, set to the instant of the event about to be applied; None
                before the clock was first set
            snapshot_for_seal: Callable returning the `PeriodSnapshot` and the records, invoked
                only when a seal actually happens — so a boundary check inside the day costs
                nothing beyond that one comparison
        """
        if now is None or self._anchor is None:
            return
        # The ordinary tick: still inside the running trading day, nothing to do.
        if self._next_boundary is not None and now < self._next_boundary:
            return

        if self._opened_at is None:
            self._opened_at = now
            self._set_day(self._first_trading_day_from(trading_day_of(now, self._anchor)))
            return
        while now >= self._next_boundary:
            period = self._file(PeriodCloseReason.ANCHOR, self._next_boundary, snapshot_for_seal)
            self._set_day(self._first_trading_day_from(self._current_day + timedelta(days=1)))
            self._hand_on(period)

    def _first_trading_day_from(self, day: date) -> date:
        """
        The first trading day at or after a day's label — the label itself on a market that
        never closes.

        Args:
            day: Where to start looking

        Returns:
            The label of the first day the market trades on
        """
        candidate = day
        for _ in range(_TRADING_DAY_SEARCH_LIMIT):
            if self._is_trading_day(candidate):
                return candidate
            candidate += timedelta(days=1)
        raise ValueError(
            f'No trading day within {_TRADING_DAY_SEARCH_LIMIT} days from {day.isoformat()} for '
            f'{self._unit_name} — the market calendar closes the market for longer than any '
            f'weekend and holiday together')

    def _is_trading_day(self, day: date) -> bool:
        """
        Whether the market trades on the trading day with this label.

        The calendar is asked about the date the day trades FOR — Monday for the forex session
        that opens Sunday evening in New York — and only for a market that closes; one that
        never does trades every day.

        Args:
            day: The trading day's label

        Returns:
            True when the day gets a period
        """
        if not self._weekend_closure:
            return True
        return MarketCalendar.is_trading_date(trade_date_of(day, self._anchor))

    def _set_day(self, day: date) -> None:
        """
        Adopt a trading day and cache the instant it ends at.

        The two are set together because the cache is only as correct as the day it was derived
        from — and `boundary_opening` is DST-aware per date, so computing it once per day is
        also what keeps a spring-forward boundary right. A day ends where the next label
        opens, whether or not the market trades then: the forex week's last day ends at the
        Friday close.

        Args:
            day: The trading day now running
        """
        self._current_day = day
        self._next_boundary = boundary_opening(day + timedelta(days=1), self._anchor)

    def _file(self, reason: PeriodCloseReason, now: datetime, snapshot_for_seal) -> BookingPeriod:
        """
        File the period that is ending, and open the next one at the same instant.

        The counter is advanced only AFTER the period is in the list. A crash between the two
        re-seals the same number, which is a visible duplicate; the other order leaves an
        invisible hole. The same rule the position-book watcher follows.

        Args:
            reason: What closed the period
            now: When it closed, from the canonical clock — the day's boundary for an anchor seal
            snapshot_for_seal: Callable returning `(trades, PeriodSnapshot)` for this instant

        Returns:
            The filed period
        """
        # A period is never filed inside out. Nothing downstream checks the order of the two
        # instants — not the builder, not the ledger, not the renderer — so an inverted pair
        # would travel as far as a chart and draw a bar running backwards. The canonical clock
        # is clamped forward since #539, which is what makes this unreachable rather than
        # merely unlikely; it stays as the assertion that the clamp holds, and it SAYS so
        # rather than repairing the stamp silently.
        if now < self._opened_at and self._log is not None:
            self._log(
                f'⚠️ Booking period {self._period_no + 1} of {self._unit_name} would close '
                f'BEFORE it opened ({now.isoformat()} < {self._opened_at.isoformat()}) — the '
                f'canonical clock moved backwards. Sealed at its opening instant instead.')
        closed_at = max(now, self._opened_at)
        trades, snapshot = snapshot_for_seal()
        # The seal's own reading belongs to the band it closes: a fill a heartbeat resolved
        # moved the account without a tick, and a closing value outside its own band would
        # describe a day that never stood there.
        if snapshot.final_equity is not None:
            self._fold(snapshot.final_equity)
        # A band nothing was folded into is unknown, not a range from 0.00 to 0.00 (#476): a
        # spot holding sealed before the first tick priced it has neither a value nor a band
        observed = self._peak_equity is not None
        snapshot.period_max_equity = self._max_equity if observed else None
        snapshot.period_min_equity = self._min_equity if observed else None
        snapshot.period_max_drawdown = self._max_drawdown if observed else None
        snapshot.period_opening_equity = self._opening_equity

        period = derive_booking_period(
            unit_name=self._unit_name,
            period_no=self._period_no + 1,
            opened_at=self._opened_at,
            closed_at=closed_at,
            reason=reason,
            trades=trades,
            snapshot=snapshot,
            tick_count=self._tick_count,
            trading_day=self._current_day,
        )
        self._segments.append(period)
        self._period_no += 1
        if self._log is not None:
            self._log(describe_segment(period))

        self._opened_at = closed_at
        self._start_band(snapshot.final_equity)
        return period

    def _start_band(self, opening: Optional[float]) -> None:
        """
        Open the next period's band at the value the seal read.

        That value is where the account stood when the new period began, so it is the band's
        first point: a drop between the boundary and the next tick is then the new day's
        decline rather than nobody's (#476). Unknown, the band starts empty and its first
        reading opens it.

        Args:
            opening: The account value the seal read, or None when it could not be read
        """
        self._opening_equity = opening
        self._peak_equity = None
        self._max_equity = 0.0
        self._min_equity = 0.0
        self._max_drawdown = 0.0
        self._tick_count = 0
        if opening is not None:
            self._fold(opening)

    def _hand_on(self, period: BookingPeriod) -> None:
        """
        Hand a filed period to the listener, once the recorder is consistent again.

        After the next trading day is adopted, so a listener asking for the current day reads
        the one that has begun — which is what a daily loss limit rolling on this seal needs.

        Args:
            period: The period just filed
        """
        if self._on_seal is not None:
            self._on_seal(period)

    def close(self, now: Optional[datetime], snapshot_for_seal) -> List[BookingPeriod]:
        """
        Seal the period that is still running and hand over everything collected.

        The final period is filed like every other rather than dropped for having no successor —
        a unit that ends before its first boundary would otherwise book nothing at all, which is
        the hole this feature exists to close.

        Args:
            now: The canonical clock's last instant
            snapshot_for_seal: Callable returning `(trades, PeriodSnapshot)`

        Returns:
            The periods, oldest first
        """
        if self._opened_at is not None and now is not None:
            self._hand_on(self._file(PeriodCloseReason.SESSION_END, now, snapshot_for_seal))
        self._opened_at = None
        return list(self._segments)


def snapshot_from_executor(executor):
    """
    The records and the stock reading a seal needs, taken from a unit's executor and its portfolio.

    Shared by both loops so the two pipelines read the same figures from the same accessors — a
    second transcription here is exactly how a simulation and an AutoTrader session come to disagree
    about what a period held. It TAKES the period's order counts, which starts the next period's
    count, so it is called once per filed period — which is what the recorder does.

    Args:
        executor: The unit's trade executor

    Returns:
        `(trade records, PeriodSnapshot)` — the band fields are left at zero and filled by the
        recorder, which is the only thing that knows them. An account value that cannot be read
        stays None, and so do the curve figures before the curve's first valuation: a zero in
        either would be a measurement nobody took (#476)
    """
    portfolio = executor.portfolio
    stats = portfolio.get_portfolio_statistics()
    curve = portfolio.is_equity_curve_established()
    open_positions = portfolio.get_open_positions()
    return portfolio.get_trade_history(), PeriodSnapshot(
        currency=stats.currency,
        final_equity=portfolio.get_account_value(),
        # Summed over the open positions, which is where the figure lives — the portfolio keeps
        # no total of it, and inventing one here would be a second answer to a question the
        # portfolio report already answers the same way.
        unrealized_pnl=sum(p.unrealized_pnl for p in open_positions),
        open_position_count=len(open_positions),
        account_max_drawdown=stats.account_max_drawdown if curve else None,
        max_equity=stats.max_equity if curve else None,
        account_max_dd_pct=stats.account_max_drawdown_pct if curve else None,
        execution=executor.take_period_execution_stats(),
    )
