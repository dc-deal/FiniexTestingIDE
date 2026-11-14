"""
FiniexTestingIDE - Algo Order Result
An order's outcome as an algo sees it (#654).

The engine keeps every booked result in its order history and hands the same object to every
listener of the outcome. An algo receives this mask in its place — from send_order and
close_position, and as the result of every decision event — so it reads the outcome but can change
none of what the engine and the other listeners keep. A result does not change once it is booked, so
the mask simply reads it; the engine's own bookkeeping on it — the submission's details, the free
metadata and the order-event sequence number — stays out of the algo's reach.
"""

from datetime import datetime
from typing import Optional

from python.framework.trading_env.algo_masks.abstract_algo_mask import AbstractAlgoMask
from python.framework.types.trading_env_types.broker_types import FeeSource
from python.framework.types.trading_env_types.order_types import (
    CloseType,
    OrderAction,
    OrderDirection,
    OrderEndReason,
    OrderInitiator,
    OrderResult,
    OrderStatus,
    OrderType,
    RejectionReason,
)


class AlgoOrderResult(AbstractAlgoMask):
    """A read-only view of one order's outcome."""

    __slots__ = ()

    def __init__(self, result: OrderResult):
        """
        Wrap one engine result.

        Args:
            result: The engine's order result
        """
        super().__init__(result)

    @property
    def order_id(self) -> str:
        """The order's identifier."""
        return self._source.order_id

    @property
    def status(self) -> OrderStatus:
        """Where the order stands: pending, executed, denied, rejected, cancelled, expired."""
        return self._source.status

    @property
    def is_success(self) -> bool:
        """Whether the order executed."""
        return self._source.is_success

    @property
    def is_refused(self) -> bool:
        """Whether the order was refused — here (denied) or by the venue (rejected)."""
        return self._source.is_refused

    @property
    def executed_price(self) -> Optional[float]:
        """The price it executed at; None while nothing executed."""
        return self._source.executed_price

    @property
    def executed_lots(self) -> Optional[float]:
        """The lots executed; None while nothing executed."""
        return self._source.executed_lots

    @property
    def execution_time(self) -> Optional[datetime]:
        """When it executed, on the canonical clock; None while nothing executed."""
        return self._source.execution_time

    @property
    def commission(self) -> float:
        """The fee it was charged, in the account currency."""
        return self._source.commission

    @property
    def fee_source(self) -> Optional[FeeSource]:
        """Whether the fee is the venue's own figure or the model's estimate."""
        return self._source.fee_source

    @property
    def rejection_reason(self) -> Optional[RejectionReason]:
        """Why it was refused; None where it was not."""
        return self._source.rejection_reason

    @property
    def rejection_message(self) -> str:
        """The refusal in words; '' where it was not refused."""
        return self._source.rejection_message

    @property
    def position_id(self) -> Optional[str]:
        """The position it opened or closed."""
        return self._source.position_id

    @property
    def action(self) -> Optional[OrderAction]:
        """Whether it opened, closed or partly closed a position."""
        return self._source.action

    @property
    def symbol(self) -> Optional[str]:
        """The traded symbol."""
        return self._source.symbol

    @property
    def direction(self) -> Optional[OrderDirection]:
        """LONG or SHORT — a close's is the closed position's."""
        return self._source.direction

    @property
    def requested_lots(self) -> Optional[float]:
        """The lots the order asked for."""
        return self._source.requested_lots

    @property
    def close_type(self) -> Optional[CloseType]:
        """For a close, whether it closed the position in full or in part."""
        return self._source.close_type

    @property
    def order_type(self) -> Optional[OrderType]:
        """The type the order was asked as."""
        return self._source.order_type

    @property
    def initiator(self) -> Optional[OrderInitiator]:
        """Who ended the order, where it ended other than by a fill."""
        return self._source.initiator

    @property
    def end_reason(self) -> Optional[OrderEndReason]:
        """Why the order ended, where it ended other than by a fill."""
        return self._source.end_reason

    @property
    def joined_in_flight_close(self) -> bool:
        """For a close asked while another close of the same position was already on its way:
        True — the request joined that close and describes it, nothing new was sent."""
        return bool(self._source.metadata.get('joined_in_flight_close'))
