"""
FiniexTestingIDE - Algo Order
A resting order as an algo sees it (#654).

A limit or stop order rests in the engine until it fills, is cancelled or expires, and the engine
keeps changing it while it rests: a modify moves its price, a partial fill at a venue lowers what
is left, an operation in flight marks it. An algo therefore never holds the engine's own order: it
reads the order through this mask, which shows the declared fields as they stand at the moment of
the read and writes nothing. A mask an algo keeps across passes stays current.
"""

from datetime import datetime
from typing import Optional

from python.framework.trading_env.algo_masks.abstract_algo_mask import AbstractAlgoMask
from python.framework.types.trading_env_types.latency_simulator_types import (
    PendingOperation,
    PendingOrder,
    PendingOrderAction,
)
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderType


class AlgoOrder(AbstractAlgoMask):
    """A read-only view of one resting order, every field read from the order when asked."""

    __slots__ = ()

    def __init__(self, order: PendingOrder):
        """
        Wrap one engine order.

        Args:
            order: The engine's resting order
        """
        super().__init__(order)

    @property
    def pending_order_id(self) -> str:
        """The order's identifier — the one modify_limit_order, modify_stop_order and the cancels
        take."""
        return self._source.pending_order_id

    @property
    def order_action(self) -> Optional[PendingOrderAction]:
        """Whether the order opens a position or closes one."""
        return self._source.order_action

    @property
    def order_type(self) -> Optional[OrderType]:
        """LIMIT, STOP or STOP_LIMIT."""
        return self._source.order_type

    @property
    def symbol(self) -> Optional[str]:
        """The traded symbol."""
        return self._source.symbol

    @property
    def direction(self) -> Optional[OrderDirection]:
        """LONG or SHORT — the direction of the position it opens."""
        return self._source.direction

    @property
    def lots(self) -> Optional[float]:
        """The lots still working — lowered by a partial fill at a venue."""
        return self._source.lots

    @property
    def filled_lots(self) -> float:
        """The lots already executed of it — 0 while nothing filled."""
        return self._source.fills.cumulative_filled_lots

    @property
    def entry_price(self) -> Optional[float]:
        """The price it rests at: a limit's limit price, a stop's trigger price."""
        return self._source.entry_price

    @property
    def limit_price(self) -> Optional[float]:
        """A stop-limit's limit price, which applies once its trigger is reached; None for the
        others."""
        return self._order_argument('limit_price')

    @property
    def stop_loss(self) -> Optional[float]:
        """The stop loss the position it opens will carry; None where none was asked for."""
        return self._order_argument('stop_loss')

    @property
    def take_profit(self) -> Optional[float]:
        """The take profit the position it opens will carry; None where none was asked for."""
        return self._order_argument('take_profit')

    @property
    def entry_time(self) -> Optional[datetime]:
        """When the order was placed, on the canonical clock."""
        return self._source.entry_time

    @property
    def in_flight_operation(self) -> PendingOperation:
        """A submit, modify or cancel still on its way — NONE in steady state. While a modify is
        in flight the fields above still show the values it would replace."""
        return self._source.execution_state.in_flight_operation

    def _order_argument(self, name: str) -> Optional[float]:
        """
        One price the order was placed with, from its order arguments.

        Args:
            name: The argument's name

        Returns:
            Its value; None where the order carries none
        """
        arguments = self._source.order_kwargs
        return arguments.get(name) if arguments else None
