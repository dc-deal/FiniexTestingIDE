"""
FiniexTestingIDE - Algo Bar
The forming bar as an algo sees it (#654).

The bar renderer builds the current bar of every timeframe tick by tick — each tick moves its
close, perhaps its high or low, and adds to its volume — and the same object is handed to every
worker. An algo therefore reads the forming bar through this mask: the bar's fields as they stand at
the moment of the read, nothing it could change. The renderer makes a new bar when a period closes,
so a mask shows one period; a closed bar reaches the history as a frozen Bar, not through a mask.
"""

from typing import Any, Dict, Tuple

from python.framework.trading_env.algo_masks.abstract_algo_mask import AbstractAlgoMask
from python.framework.types.market_types.market_data_types import Bar, FormingBar


class AlgoBar(AbstractAlgoMask):
    """A read-only view of one forming bar, every field read from the bar when asked."""

    __slots__ = ()

    def __init__(self, bar: FormingBar):
        """
        Wrap one forming bar.

        Args:
            bar: The renderer's forming bar
        """
        super().__init__(bar)

    def __copy__(self) -> Bar:
        """
        The forming bar keeps changing, so a copy is the bar as it stands now, frozen.

        Returns:
            A Bar with the bar's current fields
        """
        return self._source.to_bar()

    def __deepcopy__(self, memo: Dict[int, Any]) -> Bar:
        """
        A deep copy is the bar as it stands now, frozen — as a copy is.

        Args:
            memo: The deep copy's memo

        Returns:
            A Bar with the bar's current fields
        """
        return self._source.to_bar()

    @property
    def symbol(self) -> str:
        """The traded symbol."""
        return self._source.symbol

    @property
    def timeframe(self) -> str:
        """The bar's timeframe, such as M5."""
        return self._source.timeframe

    @property
    def timestamp(self) -> str:
        """The instant the bar's period opens, as an ISO string in UTC."""
        return self._source.timestamp

    @property
    def open(self) -> float:
        """The first price of the period."""
        return self._source.open

    @property
    def high(self) -> float:
        """The highest price so far."""
        return self._source.high

    @property
    def low(self) -> float:
        """The lowest price so far."""
        return self._source.low

    @property
    def close(self) -> float:
        """The latest price."""
        return self._source.close

    @property
    def volume(self) -> float:
        """The volume so far."""
        return self._source.volume

    @property
    def tick_count(self) -> int:
        """The ticks the bar holds so far."""
        return self._source.tick_count

    @property
    def is_complete(self) -> bool:
        """Whether the period has ended by the latest tick's time."""
        return self._source.is_complete

    @property
    def ohlc(self) -> Tuple[float, float, float, float]:
        """Open, high, low and close."""
        return self._source.ohlc

    def to_dict(self) -> Dict[str, Any]:
        """
        The bar's fields as plain values, as Bar.to_dict() gives them.

        Returns:
            The fields by name
        """
        return self._source.to_dict()
