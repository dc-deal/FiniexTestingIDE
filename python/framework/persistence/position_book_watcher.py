"""
FiniexTestingIDE - Position Book Watcher (#355)

Answers one question per pass: does the open book need writing down again?

The framework carry-over used to be written at boot and at shutdown only. For the session key
that is enough — it is minted once. For the open book it is not: a position opened at 09:00
and lost to a hard kill at 11:00 would be a position the successor never hears about, and the
successor would then read the holding as flat and open a second one beside it.

**Two classes of change, because they cost differently and are worth differently.** Measured
on this project's own tree (`/app/data`, a bridged mount where a file operation costs 65-616x
what it does on a local disk, §42): one carry-over write is **11 ms**, and the store's index
rebuild on top of it another **26-40 ms**.

    STRUCTURAL   which positions exist, how much of each is left, their status
                 → rare (a fill, a close, a partial close) and NOT recoverable
                 → written immediately

    DRIFT        the exit levels and the excursion extrema
                 → frequent — a trailing stop moves on every new high, so this fires on
                   nearly every tick of a trend — and either self-correcting (the algo
                   re-derives its exits on the next pass) or bounded-loss (the extrema lose
                   at most one interval of history)
                 → written on a cadence

Without that split a trailing stop turns the seam into a 37 ms stall per tick, which is worse
than the loss it prevents. With it, the immediate write happens a handful of times a day.

The state is only ADVANCED by `accept()`, which the caller calls after a write that actually
succeeded. A watcher that advanced on the query would drop the trigger for good whenever a
write failed — the change would be reported once, to a caller that could not act on it.
"""

from typing import Iterable, Optional, Tuple

from python.framework.types.portfolio_types.portfolio_types import Position

# What a position IS: its identity, how much of it is left, and whether it is whole.
_Structure = Tuple[Tuple[str, float, str], ...]
# What a position is DOING: where its exits sit and how far it has travelled.
_Drift = Tuple[Tuple[str, Optional[float], Optional[float], float, float], ...]


class PositionBookWatcher:
    """
    Change detection over the open position book, split by what a change costs.

    Args:
        seed: The book as it stood when it was last persisted, or None for "nothing written
            yet" — in which case the first look already counts as a change
    """

    def __init__(self, seed: Optional[Iterable[Position]] = None):
        self._structure: Optional[_Structure] = None
        self._drift: Optional[_Drift] = None
        if seed is not None:
            self.accept(seed)

    def has_changed(self, positions: Iterable[Position], drift_due: bool = False) -> bool:
        """
        Whether the book differs from the last state that was successfully written.

        Pure: it answers and changes nothing. The caller advances the watcher with `accept()`
        once its write has gone through, so a failed write is retried on the next pass instead
        of being silently forgotten.

        Args:
            positions: The currently open positions
            drift_due: Whether the cadence window for exit levels and excursion extrema is
                open. False means only a structural change counts

        Returns:
            True when the book should be written
        """
        snapshot = list(positions)
        if self._structure != self._sign_structure(snapshot):
            return True

        return drift_due and self._drift != self._sign_drift(snapshot)

    def accept(self, positions: Iterable[Position]) -> None:
        """
        Record this book as the state that is now on disk.

        Args:
            positions: The positions that were just written down
        """
        snapshot = list(positions)
        self._structure = self._sign_structure(snapshot)
        self._drift = self._sign_drift(snapshot)

    @staticmethod
    def _sign_structure(positions: Iterable[Position]) -> _Structure:
        """
        Reduce the book to what cannot be recovered if it is lost.

        An open, a close and a partial close all move one of these three, and none of them can
        be reconstructed afterwards: the venue does not know our positions, and a size we
        never wrote down is a size nobody can testify to.

        Args:
            positions: The positions to describe

        Returns:
            A sorted, hashable description
        """
        return tuple(sorted(
            (p.position_id, p.lots, p.status.value) for p in positions
        ))

    @staticmethod
    def _sign_drift(positions: Iterable[Position]) -> _Drift:
        """
        Reduce the book to what moves often and survives being one interval stale.

        Exit levels are re-derived by the algo on its next pass, so a stale one is corrected
        rather than believed. The excursion extrema (#389) cannot be recomputed, but losing
        the last interval of a running maximum understates a figure rather than inventing one.

        Args:
            positions: The positions to describe

        Returns:
            A sorted, hashable description
        """
        return tuple(sorted(
            (p.position_id, p.stop_loss, p.take_profit, p.mae_pnl, p.mfe_pnl)
            for p in positions
        ))
