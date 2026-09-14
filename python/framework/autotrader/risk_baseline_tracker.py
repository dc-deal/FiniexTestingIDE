"""
FiniexTestingIDE - Risk Baseline Tracker

Owns the one number every risk limit measures against, and the record that describes it
(#356).

The baseline used to be a float captured on the first tick and held in memory. A restart
mid-drawdown re-captured it at the already drawn-down value, so the safety margin followed
the bot down and the accumulated loss was silently forgotten:

    deploy: equity 10 000  ->  baseline 10 000
    drawdown to 9 200 (-8 %)  ->  RESTART  ->  baseline 9 200 (-0 %)

A thirty-day unattended run will restart. This tracker is what makes the baseline survive
one — and what makes every drawdown figure able to say which denominator produced it.

It decides nothing about breaching: the circuit breaker reads the value and compares. This
unit answers only "what is the denominator, where did it come from, and when was it taken".
"""

from datetime import datetime, timezone
from typing import Callable, Optional

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.types.persistence_types import (
    BaselineKind,
    BaselineOrigin,
    BaselineQuantities,
    RiskBaseline,
)


class RiskBaselineTracker:
    """
    Takes, restores and advances the risk baseline for one live session.

    Args:
        mode: Which denominator this session uses — SESSION_FIXED or HIGH_WATER_MARK
        restored: The predecessor's record, or None on a first run
        spot_mode: Whether the account holds inventory (decides mark_price / quantities)
        exclusive_account: The #489 declaration, stamped onto every record taken here
        clock_fn: The canonical clock, which answers None before the first event
        logger: Session logger — the operator must see which baseline a session is on
    """

    def __init__(
        self,
        mode: BaselineKind,
        restored: Optional[RiskBaseline],
        spot_mode: bool,
        exclusive_account: bool,
        clock_fn: Callable[[], Optional[datetime]],
        logger: AbstractLogger,
    ):
        self._mode = mode
        self._spot_mode = spot_mode
        self._exclusive_account = exclusive_account
        self._clock_fn = clock_fn
        self._logger = logger
        self._baseline: Optional[RiskBaseline] = None
        if restored is not None:
            self._adopt_restored(restored)

    # ============================================
    # Taking and advancing
    # ============================================

    def ensure_taken(
        self,
        value: float,
        mark_price: Optional[float] = None,
        quantities: Optional[BaselineQuantities] = None,
        origin: BaselineOrigin = BaselineOrigin.FIRST_TICK,
    ) -> None:
        """
        Take the session's baseline if it does not have one yet.

        Idempotent on purpose, and that is the whole restart fix: a session that restored a
        record does nothing here, so the first tick cannot re-anchor the denominator at
        whatever the account happens to be worth after a drawdown.

        Args:
            value: The account value to record
            mark_price: The price the holdings were valued at (spot only)
            quantities: What was held at that instant (spot only)
            origin: How this record came to be — FIRST_TICK, or BOOT_BALANCES before one
        """
        if self._baseline is not None:
            return
        self._baseline = self._build(self._mode, value, mark_price, quantities, origin)
        self._logger.info(
            f'🎯 Risk baseline taken: {self._describe(self._baseline)}')

    def observe(
        self,
        value: float,
        mark_price: Optional[float] = None,
        quantities: Optional[BaselineQuantities] = None,
    ) -> None:
        """
        Offer the current account value to the baseline.

        Only a HIGH_WATER_MARK baseline moves, and only upward. Every advance is a NEW
        record with its own stamp, because a peak without its date cannot answer how long a
        drawdown has lasted — which is the next question anyone asks after how deep it is.

        A SESSION_FIXED baseline ignores this entirely; that is what fixed means.

        Args:
            value: The current account value
            mark_price: The price the holdings are valued at (spot only)
            quantities: What is held right now (spot only)
        """
        if self._baseline is None or self._mode is not BaselineKind.HIGH_WATER_MARK:
            return
        if value <= self._baseline.value:
            return
        self._baseline = self._build(
            BaselineKind.HIGH_WATER_MARK, value, mark_price, quantities,
            BaselineOrigin.HWM_UPDATE)

    # ============================================
    # Reading
    # ============================================

    def get_baseline(self) -> Optional[RiskBaseline]:
        """
        The record every risk figure should name beside its number.

        Returns:
            The current baseline, or None before one has been taken
        """
        return self._baseline

    def get_value(self) -> float:
        """
        The denominator, or 0.0 when there is none yet.

        Zero is the value the circuit breaker already reads as "no baseline, no drawdown
        check" — so a session before its first tick behaves exactly as it did, rather than
        measuring against a number nobody took.

        Returns:
            The baseline value, or 0.0
        """
        return self._baseline.value if self._baseline is not None else 0.0

    # ============================================
    # Internals
    # ============================================

    def _adopt_restored(self, restored: RiskBaseline) -> None:
        """
        Take the predecessor's record as our own, unchanged except for its origin.

        `taken_at_utc` is deliberately NOT re-stamped. The stamp says when the denominator
        was struck, and a restart does not strike a new one — re-stamping it is the drift
        bug wearing the shape of a fresh start.

        A restored record of a DIFFERENT kind than this session configures is kept and said
        out loud rather than silently converted: the operator changed the mode between
        runs, and a high-water mark quietly becoming a fixed baseline (or the reverse)
        changes what every limit means without anyone asking for it. Keeping it means the
        session ADOPTS the restored kind as its mode — carrying the old label while running
        the new behaviour would convert the record on its first advance, which is the same
        silent conversion one step later.

        Args:
            restored: The record read from the carry-over
        """
        configured = self._mode
        self._baseline = restored.model_copy(
            update={'origin': BaselineOrigin.RESTORED_CARRY_OVER})
        # The restored KIND becomes this session's mode, not just the record's label. Keeping
        # the configured mode while carrying the old kind would convert the record on the
        # first advance — a `session_fixed` baseline silently becoming a high-water mark,
        # which is exactly what the warning below promises does not happen.
        self._mode = restored.kind
        self._logger.info(
            f'🎯 Risk baseline restored from the previous session: '
            f'{self._describe(self._baseline)} — the drawdown continues from there rather '
            f'than starting over')
        if restored.kind is not configured:
            self._logger.warning(
                f'⚠️ The restored baseline is a {restored.kind.value} record while this '
                f'session is configured for {configured.value}. The RESTORED kind is kept '
                f'and governs this session, because converting it would change what every '
                f'limit measures without being asked. Set persist_baseline=false for one '
                f'run to start fresh.')

    def _build(
        self,
        kind: BaselineKind,
        value: float,
        mark_price: Optional[float],
        quantities: Optional[BaselineQuantities],
        origin: BaselineOrigin,
    ) -> RiskBaseline:
        """
        Assemble one record, stamped with the right kind of time.

        Before the first event there is no canonical time, so the stamp is a wall-clock
        read. That is legitimate PROVENANCE (§9) and `origin` is what tells a reader which
        of the two they are holding — the field exists for exactly this.

        Args:
            kind: Which denominator this record is
            value: The account value
            mark_price: The valuation price (spot only)
            quantities: The holdings (spot only)
            origin: How the record came to be

        Returns:
            The assembled RiskBaseline
        """
        now = self._clock_fn() or datetime.now(timezone.utc)
        return RiskBaseline(
            kind=kind,
            value=value,
            taken_at_utc=now.isoformat(),
            origin=origin,
            mark_price=mark_price if self._spot_mode else None,
            quantities=quantities if self._spot_mode else None,
            exclusive_account=self._exclusive_account,
        )

    @staticmethod
    def _describe(baseline: RiskBaseline) -> str:
        """
        One line an operator can read without opening the record.

        Args:
            baseline: The record to describe

        Returns:
            kind, value, origin and stamp in one string
        """
        return (f'{baseline.kind.value} = {baseline.value:.2f} '
                f'({baseline.origin.value}, taken {baseline.taken_at_utc})')
