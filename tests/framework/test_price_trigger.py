"""
Price triggers — the contract of the shared predicate (§45).

`price_trigger.py` answers two questions that both worlds ask and used to answer separately:
has the market reached this order's price, and which side of the book does this direction
trade at. The simulation asks with a tick it holds; the dry-run simulator asks from inside an
adapter's parse layer where no executor state exists — which is exactly why the functions take
primitives and keep no state.

Both callers exercise it, and between them all four direction × type combinations are covered.
That is not the same as pinning the module's OWN contract: coverage spread across two suites
tells you the callers work, not what this module promises. So the promise is written down here,
in one place, including the boundary case that no caller happens to hit.

The DIRECTIONS are the part worth pinning, because they are easy to reason about backwards:

    a LIMIT waits for a BETTER price than the market offers
        buy  → sits BELOW the ask, fills when the ask falls to it
        sell → sits ABOVE the bid, fills when the bid rises to it

    a STOP waits for a WORSE one, because it exists to get out or to follow a breakout
        buy  → sits ABOVE the ask, triggers when the ask rises to it
        sell → sits BELOW the bid, triggers when the bid falls to it

Pure arithmetic — no tick, no order, no I/O.
"""

import pytest

from python.framework.types.trading_env_types.order_types import OrderDirection
from python.framework.utils.trading_math.price_trigger import (
    is_limit_reached,
    is_stop_reached,
    taken_price,
)

_BID = 3999.0
_ASK = 4001.0


class TestALimitWaitsForABetterPrice:
    """A limit order never fills worse than its own price. That is what makes it a limit."""

    @pytest.mark.parametrize('limit, reached', [
        (4100.0, True),    # the ask is already below the limit — fills at once
        (4001.0, True),    # exactly AT the ask: reached, not "almost"
        (3900.0, False),   # 100 below the ask — waits
    ])
    def test_a_buy_limit_reads_the_ask(self, limit, reached):
        assert is_limit_reached(OrderDirection.LONG, limit, _BID, _ASK) is reached

    @pytest.mark.parametrize('limit, reached', [
        (3900.0, True),    # the bid is already above the limit
        (3999.0, True),    # exactly AT the bid
        (4100.0, False),   # 100 above the bid — waits
    ])
    def test_a_sell_limit_reads_the_bid(self, limit, reached):
        assert is_limit_reached(OrderDirection.SHORT, limit, _BID, _ASK) is reached

    def test_a_limit_INSIDE_the_spread_is_reached_by_neither_side(self):
        """
        The fact that follows from "better than the market offers", and the reason a limit
        placed inside the spread simply rests: a buy there is below the ask and a sell there
        is above the bid, so neither side has come to it. Written down because it reads as a
        contradiction until you say it out loud.
        """
        inside = 4000.0

        assert is_limit_reached(OrderDirection.LONG, inside, _BID, _ASK) is False
        assert is_limit_reached(OrderDirection.SHORT, inside, _BID, _ASK) is False

    def test_outside_the_spread_the_two_directions_diverge(self):
        """A direction handled backwards is the defect this module prevents being written twice."""
        below, above = 3900.0, 4100.0

        assert is_limit_reached(OrderDirection.LONG, below, _BID, _ASK) is False
        assert is_limit_reached(OrderDirection.SHORT, below, _BID, _ASK) is True
        assert is_limit_reached(OrderDirection.LONG, above, _BID, _ASK) is True
        assert is_limit_reached(OrderDirection.SHORT, above, _BID, _ASK) is False


class TestAStopWaitsForAWorsePrice:
    """The inverse, and the one that protects a position."""

    @pytest.mark.parametrize('trigger, reached', [
        (3900.0, True),    # the ask is already through it
        (4001.0, True),    # exactly AT the ask
        (4100.0, False),   # 100 above — not yet
    ])
    def test_a_buy_stop_reads_the_ask(self, trigger, reached):
        assert is_stop_reached(OrderDirection.LONG, trigger, _BID, _ASK) is reached

    @pytest.mark.parametrize('trigger, reached', [
        (4100.0, True),    # the bid is already through it
        (3999.0, True),    # exactly AT the bid
        (3900.0, False),   # 100 below — not yet
    ])
    def test_a_sell_stop_reads_the_bid(self, trigger, reached):
        assert is_stop_reached(OrderDirection.SHORT, trigger, _BID, _ASK) is reached

    def test_a_stop_and_a_limit_of_one_direction_are_opposites(self):
        """
        Whatever a buy LIMIT says at a price, a buy STOP says the reverse — except exactly
        at the price, where both are true. The pair is the whole point of having two
        functions instead of one flag.
        """
        for price in (3900.0, 4100.0):
            assert (is_limit_reached(OrderDirection.LONG, price, _BID, _ASK)
                    is not is_stop_reached(OrderDirection.LONG, price, _BID, _ASK))

        at_the_ask = _ASK
        assert is_limit_reached(OrderDirection.LONG, at_the_ask, _BID, _ASK) is True
        assert is_stop_reached(OrderDirection.LONG, at_the_ask, _BID, _ASK) is True


class TestTheSideOfTheBookADirectionTradesAt:
    """
    Trivial, and centralized because it was written out inline in at least three places in the
    execution layer and a fourth was caught in review before it landed (#244 collapses them).
    """

    def test_a_buy_pays_the_ask(self):
        assert taken_price(OrderDirection.LONG, _BID, _ASK) == _ASK

    def test_a_sell_receives_the_bid(self):
        assert taken_price(OrderDirection.SHORT, _BID, _ASK) == _BID

    def test_it_costs_the_spread_to_turn_around(self):
        """
        Buying and selling at once loses exactly the spread — the property that makes this
        the right function for a fill price, and the one a hand-written `bid`/`ask` pick
        gets wrong when it is inverted.
        """
        paid = taken_price(OrderDirection.LONG, _BID, _ASK)
        received = taken_price(OrderDirection.SHORT, _BID, _ASK)

        assert paid - received == pytest.approx(_ASK - _BID)
        assert paid > received


class TestAZeroSpreadQuoteIsAnswerable:
    """
    `bid == ask` is not hypothetical here: Kraken's trade channel delivers exactly that, which
    is what #244 exists for. The predicates must not depend on a spread being present.
    """

    def test_both_predicates_still_decide(self):
        flat = 4000.0

        assert is_limit_reached(OrderDirection.LONG, flat, flat, flat) is True
        assert is_stop_reached(OrderDirection.SHORT, flat, flat, flat) is True
        assert is_limit_reached(OrderDirection.LONG, flat - 1, flat, flat) is False

    def test_and_a_round_trip_then_costs_nothing(self):
        flat = 4000.0

        assert (taken_price(OrderDirection.LONG, flat, flat)
                == taken_price(OrderDirection.SHORT, flat, flat))
