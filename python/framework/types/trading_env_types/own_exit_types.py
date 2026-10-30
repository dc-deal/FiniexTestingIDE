"""
FiniexTestingIDE - Own Exit Types (#625)

A decision logic's own stop and target for a position, and the close it makes at one of
them. A venue holds such a level only where its API takes one — Kraken's takes neither
(its API team, 2026-10-27) — and the framework does not rebuild what a venue lacks, so the
logic checks its levels itself and closes through its own close_position.
"""

from dataclasses import dataclass
from typing import Optional

from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason


@dataclass(frozen=True)
class OwnExit:
    """
    A close a decision logic makes at its own stop or target.

    Passed to `DecisionTradingApi.close_position(..., exit=...)`. Nothing is placed anywhere:
    it tells the record why the position was closed and which level fired, so the trade keeps
    its stop-loss or take-profit reason and its fill is measured against that level, exactly
    as a level the venue holds would be. The position itself carries no level, so the trade's
    R-multiple stays empty.

    Args:
        reason: SL_TRIGGERED or TP_TRIGGERED
        level: The stop or target the logic found reached
    """
    reason: CloseReason
    level: float

    def __post_init__(self) -> None:
        """
        Refuse a reason no level makes, and a level that is no price.
        """
        if self.reason not in (CloseReason.SL_TRIGGERED, CloseReason.TP_TRIGGERED):
            raise ValueError(
                f'OwnExit: reason must be SL_TRIGGERED or TP_TRIGGERED, not '
                f'{self.reason.name} — a close no level made needs no OwnExit')
        if not self.level > 0.0:
            raise ValueError(f'OwnExit: level must be a positive price, not {self.level}')


@dataclass
class PositionExitLevels:
    """
    One position's own stop and target, as a decision logic keeps them.

    Args:
        stop: The stop — the position is closed when the market reaches it, None when the
            logic keeps no stop for it
        target: The target, None when the logic keeps none
    """
    stop: Optional[float] = None
    target: Optional[float] = None
