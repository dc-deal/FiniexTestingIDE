"""
Shared harness for the protective-level suites (#503).

A protective order is the first order in this project that is meant to REST at the venue
and outlive the process. The stock `MockBrokerAdapter` cannot represent that: in
INSTANT_FILL it fills everything on arrival — including the stop, which then closes the
position it was placed to protect — and it has no book at all, so the first poll of an
order it did not mint itself comes back REJECTED and tears the order down one tick after
it was confirmed.

Both are venue behaviours the mock never needed before. They live here rather than in one
test file because two suites need the same venue.
"""

from dataclasses import replace
from typing import Any, Dict, Optional, Tuple

from python.framework.exceptions.connection_errors import ConnectionAttemptFailedError
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus
from python.framework.types.trading_env_types.order_types import (
    OrderCapabilities,
    OrderType,
)


class VenueHoldsProtectionMock(MockBrokerAdapter):
    """
    A mock venue that takes a standalone protective order and lets it REST.

    Two capabilities, and they answer different questions: `venue_held_protective_orders`
    decides whether the framework may ask for a venue-held level at all,
    `stop_orders` whether this venue would accept the STOP that carries one.

    It can also END a stop the way the venue does without being heard: a cancel it carries
    out and whose answer is then lost, or a cancel or expiry nobody asked for.
    """

    _STATUS_MAP = {**MockBrokerAdapter._STATUS_MAP, 'EXPIRED': BrokerOrderStatus.EXPIRED}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The refs this mock is holding as resting stops. Without a book it cannot tell
        # "an order I minted" from "an order I never heard of", and answers the second.
        self._resting_stops = set()
        # Stops that ended at the venue: ref → (status, executed lots, price)
        self._ended_stops: Dict[str, Tuple[str, float, Optional[float]]] = {}
        self.lose_next_cancel_answer = False

    def get_order_capabilities(self) -> OrderCapabilities:
        return replace(super().get_order_capabilities(),
                       venue_held_protective_orders=True,
                       stop_orders=True)

    def hold_resting_stop(self, broker_ref: str) -> None:
        """
        Adopt a reference a test placed by hand into this mock's book.

        Lets a test set up "the venue is already holding a stop for this position"
        without driving a placement first.

        Args:
            broker_ref: The reference the test used
        """
        self._resting_stops.add(broker_ref)

    def do_request_submit(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Rest a STOP instead of filling it on arrival.

        Args:
            payload: The mock submit payload

        Returns:
            A resting answer for a STOP, the mock's own behaviour for anything else
        """
        self._raise_injected_fault('submit')
        if payload.get('order_type') == OrderType.STOP:
            self._order_counter += 1
            broker_ref = f'MOCK-{self._order_counter:06d}'
            self._resting_stops.add(broker_ref)
            return {'status': 'PENDING', 'broker_ref': broker_ref}
        return super().do_request_submit(payload)

    def do_request_query(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        A resting stop keeps answering PENDING until something ends it.

        Args:
            payload: The mock query payload

        Returns:
            A resting answer for a stop this mock holds, else the mock's own
        """
        self._raise_injected_fault('query')
        if payload.get('broker_ref') in self._resting_stops:
            return {'status': 'PENDING', 'broker_ref': payload['broker_ref']}
        ended = self._ended_stops.get(payload.get('broker_ref'))
        if ended is not None:
            status, lots, price = ended
            return {'status': status, 'broker_ref': payload['broker_ref'],
                    'filled_lots': lots, 'fill_price': price}
        return super().do_request_query(payload)

    def do_request_cancel(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Cancelling a resting stop takes it out of this mock's book.

        Args:
            payload: The mock cancel payload

        Returns:
            A cancelled answer for a stop this mock holds, else the mock's own
        """
        # An injected transport fault applies to THIS route too. Without it the override
        # short-circuits the base's check, and a test asking for an unanswered cancel gets
        # a clean CANCELLED instead — passing while asserting the wrong state (#487).
        self._raise_injected_fault('cancel')
        broker_ref = payload.get('broker_ref')
        if broker_ref in self._resting_stops:
            self._resting_stops.discard(broker_ref)
            self._ended_stops[broker_ref] = ('CANCELLED', 0.0, None)
            if self.lose_next_cancel_answer:
                # Carried out at the venue — only the answer does not come back
                self.lose_next_cancel_answer = False
                raise ConnectionAttemptFailedError('read timeout after CancelOrder',
                                                   terminal=False)
            return {'status': 'CANCELLED', 'broker_ref': broker_ref}
        return super().do_request_cancel(payload)

    def end_at_venue(
        self,
        broker_ref: str,
        status: str = 'CANCELLED',
        filled_lots: float = 0.0,
        fill_price: Optional[float] = None,
    ) -> None:
        """
        End a resting stop the way the venue does on its own — nobody asked this bot.

        Args:
            broker_ref: The stop's reference
            status: 'CANCELLED' or 'EXPIRED'
            filled_lots: What it executed before it ended
            fill_price: At what price
        """
        self._resting_stops.discard(broker_ref)
        self._ended_stops[broker_ref] = (status, filled_lots, fill_price)
