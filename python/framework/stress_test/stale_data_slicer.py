"""
FiniexTestingIDE - Stale-Data Slicer
Carves planned stale windows out of refined source data (#436 stale-data stress).
"""

from datetime import datetime
from typing import List, Tuple

from python.framework.types.signal_data_types import SignalSeries


class StaleDataSlicer:
    """
    Carves planned stale windows out of a scenario's refined data — SOURCE-level
    by design: an outage hits a feed, so every consumer of that source sees the
    same gap. Runs once at data-preparation time, never on the tick path.

    v0 carves SIGNAL series only (data-plane: snapshots inside a window are
    removed, so lookups resolve as-of the window start and the real #434 chain
    fires). The TICK source is deliberately never carved — a dead feed does not
    freeze the market (carving ticks would also freeze simulated broker-side
    SL/TP resolution, and a replay tick gap is indistinguishable from data);
    tick-source windows run on the status plane instead (StaleDataStressDriver).
    """

    @staticmethod
    def carve_signal_series(
        series: SignalSeries,
        windows: List[Tuple[datetime, datetime]],
    ) -> SignalSeries:
        """
        Remove all snapshots whose collected_msc falls inside a stale window.

        Args:
            series: Refined, time-ordered series for one source
            windows: (start, end) UTC windows, [start, end) semantics

        Returns:
            New SignalSeries without the carved snapshots (input unchanged)
        """
        if not windows:
            return series
        kept = [
            s for s in series.snapshots
            if not any(start <= s.collected_msc < end for start, end in windows)
        ]
        return series.model_copy(update={'snapshots': kept})
