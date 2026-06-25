"""
Abstract Splitter
=================
The split-strategy contract: turn a symbol's data coverage into a `WindowSet`.

A splitter produces the **data/time axis** of a backtest (which windows, with what regime /
session metadata) and is deliberately parameter-agnostic — it never reads strategy parameters.
That separation is what lets the same `WindowSet` be reused by every parameter combination of a
sweep (#32). Walk-forward optimization (#32 Phase 4) is the cross product of this data axis with
the parameter axis: a future fold-producing splitter returns a `WindowSet` whose windows carry a
fold grouping — the contract here is intentionally shaped to allow that without a model rework
(see WalkForwardSplit, the registered extension point).
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from python.framework.types.scenario_types.window_set_types import WindowSet


class AbstractSplitter(ABC):
    """Base contract for all split strategies — one symbol's coverage → one WindowSet."""

    @abstractmethod
    def split(
        self,
        broker_type: str,
        symbol: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        count_max: Optional[int] = None,
    ) -> WindowSet:
        """
        Split a symbol's data coverage into a WindowSet.

        Args:
            broker_type: Broker type identifier (e.g. 'mt5', 'kraken_spot')
            symbol: Trading symbol
            start_time: Optional lower time bound (splitters that span the full coverage ignore it)
            end_time: Optional upper time bound
            count_max: Optional cap on the number of windows (where the strategy supports it)

        Returns:
            WindowSet for the symbol
        """
        ...
