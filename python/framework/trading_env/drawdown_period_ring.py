"""
FiniexTestingIDE - Drawdown Period Ring (#529)

The deepest drawdown periods of an account's equity curve, kept as the curve is sampled. One number
— the deepest decline — cannot tell an account that fell 12 % once from one that fell 11 % five
times; the five deepest can. The portfolio decides per sample when a period opens, deepens or
closes, inline, because that is the hot path; this ring only takes a period when it has closed —
at a recovery, never per tick — and keeps the five deepest, ranked by their share of the peak they
fell from, the way established tools rank their worst drawdown periods.
"""

from typing import List, Optional, Tuple

from python.framework.types.portfolio_types.portfolio_aggregation_types import DrawdownPeriod

# How many periods the ring keeps — the "three or five largest" a drawdown comparison rests on
DRAWDOWN_PERIODS_KEPT = 5


def _rank_key(period: DrawdownPeriod) -> Tuple[int, float, float]:
    """
    The order the periods are ranked in: deepest share first, then the larger amount. A period whose
    peak was not above zero has no share to rank by; it ranks behind every period that has one, by
    its amount — its depth was measured, so it is not left out, and no share is made up for it.

    Args:
        period: One drawdown period

    Returns:
        A sort key, ascending for the deepest first
    """
    if period.depth_pct is None:
        return (1, 0.0, -period.depth)
    return (0, -period.depth_pct, -period.depth)


class DrawdownPeriodRing:
    """
    The closed drawdown periods of one curve, the deepest kept.
    """

    def __init__(self):
        self._kept: List[DrawdownPeriod] = []

    def admit(self, period: DrawdownPeriod) -> None:
        """
        Take a closed period, if it is among the deepest.

        Args:
            period: A period that has recovered
        """
        if (len(self._kept) >= DRAWDOWN_PERIODS_KEPT
                and _rank_key(period) >= _rank_key(self._kept[-1])):
            return
        self._kept.append(period)
        self._kept.sort(key=_rank_key)
        del self._kept[DRAWDOWN_PERIODS_KEPT:]

    def get_kept(self) -> List[DrawdownPeriod]:
        """
        The closed periods kept, deepest first.

        Returns:
            At most the kept number of periods
        """
        return list(self._kept)

    def get_deepest(self, open_period: Optional[DrawdownPeriod]) -> List[DrawdownPeriod]:
        """
        The deepest periods of the curve, the one still open among the candidates.

        Args:
            open_period: The period open right now, None when the curve stands at its peak

        Returns:
            At most the kept number of periods, deepest first
        """
        candidates = self._kept if open_period is None else self._kept + [open_period]
        return sorted(candidates, key=_rank_key)[:DRAWDOWN_PERIODS_KEPT]

    def restore(self, periods: List[DrawdownPeriod]) -> None:
        """
        Continue a predecessor's kept periods.

        Args:
            periods: The closed periods a carry-over brought back
        """
        self._kept = sorted(periods, key=_rank_key)[:DRAWDOWN_PERIODS_KEPT]

    def clear(self) -> None:
        """Forget every kept period."""
        self._kept = []
