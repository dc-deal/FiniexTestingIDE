"""
Booking-segment recorder (#537) — the state a booking period needs while it is still open.

The Hauptbuch is written from records, but it cannot be written from records ALONE: a period's
equity band is a running observation, its boundary is an event, and its number continues across
a restart. That state has to live somewhere during the run, and it has to live in ONE place —
the two tick loops are shaped differently (live is a class, the simulation is a function), so a
recorder each would be two implementations of one rule, diverging on the day somebody fixes only
the one they are looking at (§19).

**Both event sources drive it.** `check_boundary` is called from the tick path AND the heartbeat,
because in an illiquid gap only the heartbeat advances the canonical clock — a feed that goes
quiet across a rollover would otherwise book a period that silently spans two days.

**The write is NOT here.** The recorder collects; the report coordinator writes every period at
once when the run ends. That keeps the parquet write out of what the throughput benchmark
measures, and it is the only shape the simulation can have at all — a scenario runs in a
subprocess and carries its periods back over the process bridge like every other result.

What deliberately stays in the loop is the ARITHMETIC: a period is derived at its seal, while the
records it comes from are still in the bounded deque. Derived afterwards, a long run's first
periods would be periods whose records are gone.
"""

from datetime import date, datetime
from typing import Callable, List, Optional

from python.framework.reporting.builders.booking_segment_builder import (
    SegmentSnapshot,
    derive_booking_segment,
    describe_segment,
)
from python.framework.types.config_types.market_config_types import DayAnchorConfig
from python.framework.types.run_results_types import BookingSegment, SegmentCloseReason
from python.framework.utils.trading_day_anchor import trading_day_of


class BookingSegmentRecorder:
    """
    Collects one run unit's booking periods while the unit is still running.

    Args:
        unit_name: The run unit this books for — a scenario in the simulation, the session in
            live. It is part of the period's identity, not a label: a run's scenarios cover
            different windows, so only "day 1 of this unit" is a thing
        anchor: Where this market's trading day flips (§47), resolved once by the caller
        carried_segment_no: The FLOOR this unit continues from. A live session inherits it from
            the cold-start carry-over so a deployment's numbering survives a restart; a
            simulation scenario starts at 0, because a backtest has no history to inherit
        log: Where the one line per seal goes, or None to stay silent. That line carries every
            figure the ledger row will — it is the safety net for the deferred write, so a
            process that dies before the report phase still leaves its periods recoverable
    """

    def __init__(
        self,
        unit_name: str,
        anchor: DayAnchorConfig,
        carried_segment_no: int = 0,
        log: Optional[Callable[[str], None]] = None,
    ):
        self._unit_name = unit_name
        self._anchor = anchor
        self._segment_no = carried_segment_no
        self._log = log
        self._segments: List[BookingSegment] = []
        self._opened_at: Optional[datetime] = None
        self._current_day: Optional[date] = None
        # The band INSIDE the current period. The peak is kept separately from the maximum
        # because a drawdown is measured against the peak that stood AT THE TIME, while the
        # maximum is the period's own high — the two diverge the moment the account recovers.
        self._peak_equity: Optional[float] = None
        self._max_equity: float = 0.0
        self._min_equity: float = 0.0
        self._max_drawdown: float = 0.0

    def get_highest_segment_no(self) -> int:
        """
        The largest period number sealed so far, including the inherited floor.

        Deliberately not the number of collected periods: a restart inherits a floor, so the
        first period of the second session is 4 when the first ended at 3, and the list holds
        one entry.

        Returns:
            The high-water mark
        """
        return self._segment_no

    def observe_equity(self, value: float) -> None:
        """
        Record where the account stands inside the current period.

        The VALUATION half of a period, and it is what the realised figures cannot say: a
        position opened on Monday and closed on Tuesday books its whole result on Tuesday, while
        the account moved on both days.

        The low is TRACKED rather than derived. A period that ran 100 → 90 → 120 has a peak of
        120, a decline of 10 against the peak that stood then, and a low of 90 — so
        `peak - drawdown` would answer 110, a value that never occurred.

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
        decline = self._peak_equity - value
        if decline > abs(self._max_drawdown):
            self._max_drawdown = -decline

    def check_boundary(self, now: Optional[datetime], snapshot_for_seal) -> None:
        """
        Open the first period, or seal the running one when the trading day flips.

        Called from BOTH event sources. Nothing happens before the canonical clock has been set
        — a period needs an instant to open at, and a clock that was never set has none.

        Args:
            now: The canonical clock's current instant, or None before it was first set
            snapshot_for_seal: Callable returning the `SegmentSnapshot` and the records, invoked
                only when a seal actually happens — so a boundary check on a quiet tick costs
                nothing beyond a date comparison
        """
        if now is None:
            return
        current_day = trading_day_of(now, self._anchor)
        if self._opened_at is None:
            self._opened_at = now
            self._current_day = current_day
            return
        if current_day != self._current_day:
            self.seal(SegmentCloseReason.ANCHOR, now, snapshot_for_seal)
            self._current_day = current_day

    def seal(self, reason: SegmentCloseReason, now: Optional[datetime], snapshot_for_seal) -> None:
        """
        File the period that is ending, then open the next one.

        The counter is advanced only AFTER the period is in the list. A crash between the two
        re-seals the same number, which is a visible duplicate; the other order leaves an
        invisible hole. The same rule the position-book watcher follows.

        Args:
            reason: What closed the period
            now: When it closed, from the canonical clock
            snapshot_for_seal: Callable returning `(trades, SegmentSnapshot)` for this instant
        """
        if self._opened_at is None or now is None:
            return
        trades, snapshot = snapshot_for_seal()
        snapshot.segment_max_equity = self._max_equity
        snapshot.segment_min_equity = self._min_equity
        snapshot.segment_max_drawdown = self._max_drawdown

        segment = derive_booking_segment(
            unit_name=self._unit_name,
            segment_no=self._segment_no + 1,
            opened_at=self._opened_at,
            closed_at=now,
            reason=reason,
            trades=trades,
            snapshot=snapshot,
        )
        self._segments.append(segment)
        self._segment_no += 1
        if self._log is not None:
            self._log(describe_segment(segment))

        self._opened_at = now
        self._peak_equity = None
        self._max_equity = 0.0
        self._min_equity = 0.0
        self._max_drawdown = 0.0

    def close(self, now: Optional[datetime], snapshot_for_seal) -> List[BookingSegment]:
        """
        Seal the period that is still running and hand over everything collected.

        The final period is filed like every other rather than dropped for having no successor —
        a unit that ends before its first boundary would otherwise book nothing at all, which is
        the hole this feature exists to close.

        Args:
            now: The canonical clock's last instant
            snapshot_for_seal: Callable returning `(trades, SegmentSnapshot)`

        Returns:
            The periods, oldest first
        """
        self.seal(SegmentCloseReason.SESSION_END, now, snapshot_for_seal)
        self._opened_at = None
        return list(self._segments)


def snapshot_from_portfolio(portfolio):
    """
    The records and the stock reading a seal needs, taken from a portfolio manager.

    Shared by both loops so the two pipelines read the same figures from the same accessors — a
    second transcription here is exactly how a simulation and a live session come to disagree
    about what a period held.

    Args:
        portfolio: The unit's PortfolioManager

    Returns:
        `(trade records, SegmentSnapshot)` — the band fields are left at zero and filled by the
        recorder, which is the only thing that knows them
    """
    stats = portfolio.get_portfolio_statistics()
    value = portfolio.get_account_value()
    open_positions = portfolio.get_open_positions()
    return portfolio.get_trade_history(), SegmentSnapshot(
        currency=stats.currency,
        final_equity=value if value is not None else 0.0,
        # Summed over the open positions, which is where the figure lives — the portfolio keeps
        # no total of it, and inventing one here would be a second answer to a question the
        # portfolio report already answers the same way.
        unrealized_pnl=sum(p.unrealized_pnl for p in open_positions),
        open_position_count=len(open_positions),
        account_max_drawdown=stats.account_max_drawdown,
        max_equity=stats.max_equity,
        account_max_dd_pct=stats.account_max_drawdown_pct,
    )
