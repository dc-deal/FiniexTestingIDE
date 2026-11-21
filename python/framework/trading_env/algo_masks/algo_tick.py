"""
FiniexTestingIDE - Algo Tick
A tick as an algo sees it (#654).

The engine hands one tick object to the executor, the bar renderer, every worker and the decision,
and the engine's tick carries fields an algo has no use for — when the collector received it, whether
the processing budget clipped it. An algo therefore reads the tick through this mask: the declared
market fields, nothing it could change, and no field the engine adds later until the mask declares
it. A tick does not change once it is built, so the mask simply reads it.
"""

from datetime import datetime
from typing import Any, Dict, Optional

from python.framework.trading_env.algo_masks.abstract_algo_mask import AbstractAlgoMask
from python.framework.types.market_types.market_data_types import TickData


class AlgoTick(AbstractAlgoMask):
    """A read-only view of one tick's market fields."""

    __slots__ = ()

    def __init__(self, tick: TickData):
        """
        Wrap one engine tick.

        Args:
            tick: The engine's tick
        """
        super().__init__(tick)

    def __copy__(self) -> 'AlgoTick':
        """
        A tick does not change once it is built, so its mask is its own copy.

        Returns:
            This mask
        """
        return self

    def __deepcopy__(self, memo: Dict[int, Any]) -> 'AlgoTick':
        """
        A tick does not change once it is built, so its mask is its own deep copy too.

        Args:
            memo: The deep copy's memo

        Returns:
            This mask
        """
        return self

    @property
    def timestamp(self) -> datetime:
        """When the tick happened at its source, in UTC."""
        return self._source.timestamp

    @property
    def symbol(self) -> str:
        """The traded symbol."""
        return self._source.symbol

    @property
    def bid(self) -> float:
        """The best bid."""
        return self._source.bid

    @property
    def ask(self) -> float:
        """The best ask."""
        return self._source.ask

    @property
    def mid(self) -> float:
        """The midpoint of bid and ask — what a valuation uses."""
        return self._source.mid

    @property
    def price(self) -> float:
        """The strategy's price: the traded price where the venue prints one, else the midpoint."""
        return self._source.price

    @property
    def last(self) -> Optional[float]:
        """The price this tick traded at; None where the venue prints none."""
        return self._source.last

    @property
    def volume(self) -> float:
        """The volume the tick carries; 0.0 where the venue reports none."""
        return self._source.volume
