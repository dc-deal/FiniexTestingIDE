"""
FiniexTestingIDE - Abstract Live Pass Hooks (#458)

The AutoTrader runner's own steps inside a pass of the loop core.
"""

from abc import ABC, abstractmethod
from typing import Optional

from python.framework.trading_env.algo_masks.algo_order_result import AlgoOrderResult
from python.framework.types.decision_logic_types import Decision
from python.framework.types.market_types.market_data_types import TickData


class AbstractLivePassHooks(ABC):
    """
    The steps of an AutoTrader pass that belong to its runner rather than to the loop core.

    The loop core owns the order and calls these at fixed points of a tick pass, an idle pass and
    a stop's wait pass. Only points where the runner has work exist, so a pass makes no call that
    does nothing.
    """

    @abstractmethod
    def refresh_signals(self, off_tick: bool) -> None:
        """
        Fold in the signal envelopes received since the last pass (#141).

        Args:
            off_tick: True on a pass with no tick, which refreshes the workers itself; False on a
                tick, whose worker pass picks the new snapshot up
        """

    @abstractmethod
    def close_sealed_days(self) -> None:
        """
        Close the trading days the boundary check just sealed, then move the session log onto the
        day that opened (#476).
        """

    @abstractmethod
    def observe_market(self, tick: TickData) -> None:
        """
        Everything the runner observes once the broker path has seen a tick, before the bars
        render: the tick span, the market-data status, the account's value.

        Args:
            tick: The tick the broker path just applied
        """

    @abstractmethod
    def log_new_maxima(self) -> None:
        """
        Log a worker's or the decision's new longest computation, after the algo computed.
        """

    @abstractmethod
    def guard_account(self) -> None:
        """
        Value the account after the decision and run the safety layer on that value, before the
        decision executes (#356).
        """

    @abstractmethod
    def entries_blocked(self) -> bool:
        """
        Whether the safety layer blocks new entries, which turns a decision into FLAT.

        Returns:
            True when entries are blocked
        """

    @abstractmethod
    def record_order(self, order_result: Optional[AlgoOrderResult], decision: Decision) -> None:
        """
        Note what a tick's decision sent, for the display's rejections.

        Args:
            order_result: What the decision sent, or None
            decision: The decision that sent it
        """

    @abstractmethod
    def evaluate_staleness(self) -> None:
        """
        Judge the age of the last real tick on a pass with no tick (#436), before the decision
        runs, so it sees the status in this pass.
        """

    @abstractmethod
    def reconcile(self) -> None:
        """
        Compare the book with the venue's when its cadence is due (#151).
        """

    @abstractmethod
    def count_ghost_pass(self) -> None:
        """
        Count a ghost pass that returned a decision (#360) — not every pass: one whose
        decision is None is not counted.
        """

    @abstractmethod
    def record_ghost_order(self, order_result: Optional[AlgoOrderResult],
                           decision: Decision) -> None:
        """
        Note what a decision between two ticks sent: whether it acted, and a rejection.

        Args:
            order_result: What the decision sent, or None
            decision: The decision that sent it
        """
