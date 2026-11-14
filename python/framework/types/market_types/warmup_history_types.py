"""
FiniexTestingIDE - Warmup History Types (#588)
Where a warmup history came from, how it falls short of what the workers require, and how a run
records the one it started from.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class WarmupSource(str, Enum):
    """Where a session's warmup history was read from."""

    # The local bar archive — a backtest, a mock session
    ARCHIVE = 'archive'
    # The venue's own bar history — a live-adapter session
    VENUE = 'venue'
    # Another run's recorded history — a backtest of a session's window, started from the bars
    # the session started from (#512)
    RECORD = 'record'


@dataclass(frozen=True)
class WarmupShortfall:
    """
    One timeframe whose warmup history holds fewer bars than its workers require.

    Args:
        timeframe: The timeframe
        available: Bars the history holds (or can hold, for a capacity shortfall)
        required: Bars the timeframe's most demanding worker requires
    """

    timeframe: str
    available: int
    required: int

    def is_empty(self) -> bool:
        """
        Whether the timeframe has no history at all.

        Returns:
            True when not a single bar is available
        """
        return self.available == 0


@dataclass(frozen=True)
class WarmupTimeframeRecord:
    """
    One timeframe's warmup history as a run recorded it.

    Args:
        required: Bars the timeframe's most demanding worker requires
        bars: The bars, oldest first, each as
            [opening instant (ISO-8601 UTC), open, high, low, close, volume, tick count]
    """

    required: int
    bars: List[Tuple[str, float, float, float, float, float, int]]


@dataclass(frozen=True)
class WarmupHistoryRecord:
    """
    The warmup history a run started from — written to `io/warmup_history.json` in its directory.

    A live session's warmup comes from the venue and exists nowhere else afterwards; this record
    is what a backtest of the same window is compared against (#512).

    Args:
        symbol: Trading symbol
        source: Where the bars were read from
        read_at: The instant the history was first selected for (the boot read, or the first
            tick for the archive)
        selected_for: The instant the recorded bars are complete at — the first tick once the
            seam has closed
        first_tick: The session's first tick; None until it has arrived
        reread_at_first_tick: Timeframes read again at the first tick (live only)
        timeframes: The history per timeframe
    """

    symbol: str
    source: WarmupSource
    read_at: str
    selected_for: str
    first_tick: Optional[str]
    reread_at_first_tick: List[str]
    timeframes: Dict[str, WarmupTimeframeRecord]

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'WarmupHistoryRecord':
        """
        Read a record back from the JSON its run wrote.

        Every key is required: a record missing one was not written by this version of the
        writer, and reading it with a guess would warm a backtest up on something else than what
        the session started from.

        Args:
            data: The parsed `io/warmup_history.json`

        Returns:
            The record
        """
        missing = [key for key in ('symbol', 'source', 'read_at', 'selected_for', 'first_tick',
                                   'reread_at_first_tick', 'timeframes') if key not in data]
        if missing:
            raise ValueError(f'warmup history record lacks {", ".join(missing)}')
        return WarmupHistoryRecord(
            symbol=data['symbol'],
            source=WarmupSource(data['source']),
            read_at=data['read_at'],
            selected_for=data['selected_for'],
            first_tick=data['first_tick'],
            reread_at_first_tick=list(data['reread_at_first_tick']),
            timeframes={
                timeframe: WarmupTimeframeRecord(
                    required=int(entry['required']),
                    bars=[(str(row[0]), float(row[1]), float(row[2]), float(row[3]),
                           float(row[4]), float(row[5]), int(row[6])) for row in entry['bars']],
                )
                for timeframe, entry in data['timeframes'].items()
            },
        )
