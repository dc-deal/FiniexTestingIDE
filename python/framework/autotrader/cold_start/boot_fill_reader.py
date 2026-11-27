"""
FiniexTestingIDE - The Boot Read of the Venue's Closed Orders (#652)

A session killed between a market submit and its fill leaves the coins at the venue and nothing in its
book. The next start used to read only the venue's OPEN orders — a filled market order is closed, so it
was never seen — restored a flat book and bought again. This read asks the venue which orders of ours
it finished since the carry-over was written, and files each fill against the book the carry-over
holds. It books nothing: a fill the book does not account for holds the session's entries, and booking
it is #652-B's.

What the book holds is decided by the write. The carry-over is written after every structural change of
the book, so a fill missing from it either closed after the write, or belongs to an order the session
held open at the write — an answer not yet arrived, a resting fill the poll had not read yet. The
carry-over records those orders, and the read reaches back to the oldest of them. Our write instant and
the venue's close instants are two wall clocks, so a margin around the write absorbs their skew, and a
fill inside it that nothing else explains is unclear rather than accounted for.
"""

from datetime import datetime, timedelta
from typing import List, Set, Tuple

from python.framework.trading_env.adapters.abstract_adapter import AbstractAdapter
from python.framework.types.autotrader_types.cold_start_types import (
    BootFill,
    BootFillClass,
    BootRead,
)
from python.framework.types.live_types.reconciliation_types import BrokerOrder
from python.framework.types.persistence_types import OpenOrderCarryOver, PositionCarryOver
from python.framework.types.trading_env_types.order_types import OrderDirection
from python.framework.utils.connection_ladder import ConnectionLadder, run_with_ladder
from python.framework.utils.run_id_utils import parse_client_order_id


class BootFillReader:
    """
    Reads the venue's closed orders since the carry-over and files each fill of ours (#652).
    """

    def __init__(
        self,
        adapter: AbstractAdapter,
        ladder: ConnectionLadder,
        skew: timedelta,
        book_dust: float,
    ):
        """
        Args:
            adapter: The venue adapter whose closed orders are read
            ladder: The shared REST ladder the read runs under
            skew: The margin around the write that absorbs the skew between our clock and the venue's
            book_dust: Below this, in base units, two amounts are the same
        """
        self._adapter = adapter
        self._ladder = ladder
        self._skew = skew
        self._book_dust = book_dust

    def read(
        self,
        written_at: datetime,
        open_orders_at_write: List[OpenOrderCarryOver],
        book: List[PositionCarryOver],
        known_keys: Set[str],
        now: datetime,
    ) -> BootRead:
        """
        Read the venue's closed orders over the range the write leaves open, and file each fill of ours.

        Args:
            written_at: The write the book is measured against
            open_orders_at_write: The orders the session held open at that write
            book: The notes this start restores
            known_keys: The session keys whose orders are ours
            now: The end of the range — the wall-clock now of the start

        Returns:
            The read: its range, whether it completed, and every fill of ours it found, filed
        """
        read_from = self._read_from(written_at, open_orders_at_write)
        answer = run_with_ladder(lambda: self._adapter.read_closed_orders(read_from, now), self._ladder)
        if answer is None:
            return BootRead(written_at=written_at, read_from=read_from, read_to=now, complete=False,
                            incomplete_reason='the venue could not be read — the ladder gave up')

        open_keys = {order.client_order_id for order in open_orders_at_write}
        fills = [self._file(order, written_at, open_keys, book)
                 for order in answer.orders if self._is_ours_with_a_fill(order, known_keys)]
        return BootRead(
            written_at=written_at,
            read_from=read_from,
            read_to=now,
            complete=answer.complete,
            incomplete_reason='' if answer.complete else (
                f'the read stopped at its cap of {answer.pages} page(s) before the end of the range'),
            fills=fills,
        )

    def _read_from(self, written_at: datetime, open_orders_at_write: List[OpenOrderCarryOver]) -> datetime:
        """
        Where the read begins: the earliest of the write and the oldest order open at it, minus the skew.

        Args:
            written_at: The write the book is measured against
            open_orders_at_write: The orders the session held open at that write

        Returns:
            The start of the range
        """
        sent = [datetime.fromisoformat(order.submitted_at_utc)
                for order in open_orders_at_write if order.submitted_at_utc]
        return min([written_at] + sent) - self._skew

    @staticmethod
    def _is_ours_with_a_fill(order: BrokerOrder, known_keys: Set[str]) -> bool:
        """
        Whether a closed order was sent under one of our session keys and executed anything.

        Args:
            order: One order the venue reported closed
            known_keys: The session keys whose orders are ours

        Returns:
            True for an order of ours that the venue filled, wholly or in part
        """
        parsed = parse_client_order_id(order.client_order_id)
        return parsed is not None and parsed[0] in known_keys and order.filled_lots > 0.0

    def _file(
        self,
        order: BrokerOrder,
        written_at: datetime,
        open_keys: Set[str],
        book: List[PositionCarryOver],
    ) -> BootFill:
        """
        File one fill of ours against the book written at the write.

        Args:
            order: The closed order, ours, with a fill
            written_at: The write the book is measured against
            open_keys: The wire keys of the orders open at the write
            book: The notes this start restores

        Returns:
            The fill, filed with the reason
        """
        fill_class, reason = self._classify(order, written_at, open_keys, book)
        return BootFill(
            client_order_id=order.client_order_id or '',
            broker_ref=order.broker_ref,
            symbol=order.symbol,
            direction=order.direction,
            lots=order.lots,
            filled_lots=order.filled_lots,
            closed_at=order.ts_event,
            fill_class=fill_class,
            reason=reason,
        )

    def _classify(
        self,
        order: BrokerOrder,
        written_at: datetime,
        open_keys: Set[str],
        book: List[PositionCarryOver],
    ) -> Tuple[BootFillClass, str]:
        """
        Decide how one fill of ours stands against the book.

        Args:
            order: The closed order, ours, with a fill
            written_at: The write the book is measured against
            open_keys: The wire keys of the orders open at the write
            book: The notes this start restores

        Returns:
            (its class, the reason in a few words)
        """
        if any(record.broker_ref and record.broker_ref == order.broker_ref for record in book):
            return BootFillClass.ACCOUNTED_FOR, 'the entry of a position the book holds'
        open_at_write = order.client_order_id in open_keys
        closed_at = order.ts_event
        if closed_at is None:
            return BootFillClass.UNCLEAR, 'the venue reported no close instant'
        if not open_at_write and closed_at < written_at - self._skew:
            return BootFillClass.ACCOUNTED_FOR, 'closed and answered before the write'
        if order.lots - order.filled_lots > self._book_dust:
            return BootFillClass.UNCLEAR, f'a partial fill of {order.filled_lots} of {order.lots}'
        if not open_at_write and closed_at <= written_at + self._skew:
            return BootFillClass.UNCLEAR, 'closed within the clock-skew margin of the write'
        if self._position_can_take(order, book):
            return BootFillClass.UNBOOKED_CLOSE, 'it closes a position the book still holds'
        return BootFillClass.UNBOOKED_ENTRY, 'it opens a position the book does not hold'

    def _position_can_take(self, order: BrokerOrder, book: List[PositionCarryOver]) -> bool:
        """
        Whether a restored position on the order's instrument could be closed by it.

        A sell closes a long and a buy a short; the comparison is in base units, within the dust.

        Args:
            order: The closed order, ours, with a fill
            book: The notes this start restores

        Returns:
            True when a restored position of the opposite side holds at least what the order filled
        """
        closes = OrderDirection.SHORT if order.direction == OrderDirection.LONG else OrderDirection.LONG
        return any(
            record.symbol == order.symbol and record.direction == closes.value
            and order.filled_lots * record.contract_size - record.lots * record.contract_size
            <= self._book_dust
            for record in book)
