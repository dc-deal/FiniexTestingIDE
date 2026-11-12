"""
FiniexTestingIDE - Abstract Backtest Pass Hooks (#458)

The backtest runner's own steps inside a tick pass of the loop core.
"""

from abc import ABC, abstractmethod

from python.framework.types.market_types.market_data_types import TickData


class AbstractBacktestPassHooks(ABC):
    """
    The steps of a backtest tick pass that belong to its runner rather than to the loop core.

    The loop core owns the order and calls these at fixed points of the pass. Only points where
    the backtest has work exist, so a pass makes no call that does nothing.
    """

    @abstractmethod
    def observe_market(self) -> None:
        """
        Sample the account after the market moved — before the clipping gate, because a clipped
        tick still happened and the account really was where it now stands.
        """

    @abstractmethod
    def prepare_algo(self, tick: TickData) -> None:
        """
        Bring the market-data status up to the tick, before the algo computes — after the
        clipping gate, so only a tick the algo sees moves it.

        Args:
            tick: The tick the algo is about to see
        """
