"""
Fill slippage (#631) — how far each fill landed from the price it is measured against, derived at
report time from a unit's order-event stream, in both pipelines.

A fill is joined to its order by `submitted_seq`. What it is measured against depends on what the
order was:
- a stop-loss or take-profit close → the level that fired, which the executor keeps for every
  protective fill by the fill's own `seq` as it books it — every part of a close filled in parts,
  and past the trade history's cap. The simulation fills such a close AT the level, so it reads 0;
  a session reads the real distance — the difference a parity check measures. Against the mid it
  would show how far the tick that crossed the level overshot it;
- a limit → the limit in force at the fill, which the fill event carries, a modify included;
- a stop or a stop-limit → the trigger in force: the fill event's own, else the latest one the
  order's earlier events carry — the simulation turns a triggered stop-limit into a limit, and its
  fill then carries none;
- anything else, a market order above all → the mid when the order was sent, set before the order
  could move the market, so every part of a split fill is measured against the same benchmark.

The formula is `adverse_slippage`: positive is a cost. A fill whose reference was not captured is
counted as not measured.
"""

from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from python.framework.types.fill_slippage_types import FillSlippage, SlippageReference
from python.framework.types.trading_env_types.order_event_types import OrderEvent, OrderEventType
from python.framework.types.trading_env_types.order_types import (
    OrderType,
    direction_to_side,
)
from python.framework.utils.trading_math.slippage_math import adverse_slippage

_FILL_EVENTS = (OrderEventType.FILLED, OrderEventType.PARTIALLY_FILLED)
_SUBMISSION_EVENTS = (OrderEventType.SUBMITTED, OrderEventType.ADOPTED)
_STOP_TYPES = (OrderType.STOP, OrderType.STOP_LIMIT)


def derive_fill_slippage(order_events: List[OrderEvent],
                         protective_fill_levels: Dict[int, float]) -> Dict[int, FillSlippage]:
    """
    Every fill's slippage in a unit's order-event stream.

    Args:
        order_events: The unit's order events, in the order they were recorded
        protective_fill_levels: The level each stop-loss or take-profit close fired at, by its
            fill event's `seq`, as the unit's executor kept it

    Returns:
        One entry per fill event, keyed by the event's `seq`
    """
    by_order: Dict[int, List[OrderEvent]] = defaultdict(list)
    for event in order_events:
        if event.submitted_seq is not None:
            by_order[event.submitted_seq].append(event)
    return {
        event.seq: _fill_slippage(event, by_order[event.submitted_seq],
                                  protective_fill_levels.get(event.seq))
        for event in order_events
        if event.event_type in _FILL_EVENTS and event.submitted_seq is not None
    }


def _fill_slippage(fill: OrderEvent, order_events: List[OrderEvent],
                   protective_level: Optional[float]) -> FillSlippage:
    """
    One fill's slippage against its order's reference.

    Args:
        fill: A filled or partially filled event
        order_events: Every event of its order, in order
        protective_level: The level it closed at, where it is a protective close

    Returns:
        The fill's slippage
    """
    measured_against, reference = _reference_of(fill, order_events, protective_level)
    side = (direction_to_side(fill.direction, fill.action)
            if fill.direction is not None and fill.action is not None else None)
    slippage, slippage_pct = (adverse_slippage(fill.fill_price, reference, side)
                              if fill.fill_price is not None else (None, None))
    return FillSlippage(measured_against=measured_against, reference_price=reference,
                        slippage=slippage, slippage_pct=slippage_pct)


def _reference_of(fill: OrderEvent, order_events: List[OrderEvent],
                  protective_level: Optional[float]) -> Tuple[SlippageReference, Optional[float]]:
    """
    What a fill is measured against, and that price.

    Args:
        fill: A filled or partially filled event
        order_events: Every event of its order, in order
        protective_level: The level it closed at, where it is a protective close

    Returns:
        (the reference kind, its price — None where it was not captured)
    """
    if protective_level is not None:
        return SlippageReference.PROTECTIVE_LEVEL, protective_level
    submission = next(
        (event for event in order_events if event.event_type in _SUBMISSION_EVENTS), None)
    order_type = fill.order_type or (submission.order_type if submission is not None else None)
    if order_type is OrderType.LIMIT:
        return SlippageReference.LIMIT_PRICE, (
            fill.limit_price if fill.limit_price is not None
            else _latest_price(order_events, fill, 'limit_price'))
    if order_type in _STOP_TYPES:
        return SlippageReference.TRIGGER_PRICE, (
            fill.trigger_price if fill.trigger_price is not None
            else _latest_price(order_events, fill, 'trigger_price'))
    return SlippageReference.SUBMISSION_MID, (
        submission.submission_mid if submission is not None else None)


def _latest_price(order_events: List[OrderEvent], fill: OrderEvent, field: str) -> Optional[float]:
    """
    The latest price of one kind an order's events carried before a fill — the one in force.

    Args:
        order_events: Every event of the order, in order
        fill: The fill
        field: `limit_price` or `trigger_price`

    Returns:
        The price, or None when no earlier event carried one
    """
    earlier = [getattr(event, field) for event in order_events
               if event.seq < fill.seq and getattr(event, field) is not None]
    return earlier[-1] if earlier else None
