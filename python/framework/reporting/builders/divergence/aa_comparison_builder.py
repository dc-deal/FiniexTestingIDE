"""
The comparison of an A/A (#512): two backtests of one window, run on two code stands with the same
inputs, held against each other record by record.

Replay is deterministic, so the two must agree exactly: every order event in the same order with
every value, every trade with every value. Measured on 2026-11-10 over twelve weeks with fifteen
trades, two runs of different code stands agreed in every field — no field a run writes of its own
reaches either stream, so nothing is left out. A trade field only one stand writes is a change of
the report's shape, not of what was computed: it is named and not compared.
"""

import json
from typing import AbstractSet, Any, List, Mapping, Optional, Sequence, Tuple

from python.framework.types.api.divergence_report_types import AaDifference
from python.framework.types.trading_env_types.order_event_types import OrderEventType

# The streams an A/A compares, by the name a difference carries
ORDER_EVENTS_STREAM = 'order_events'
TRADES_STREAM = 'trades'
# The field a difference names where one stream holds a record the other lacks
WHOLE_RECORD = '(record)'

_FILLS = {OrderEventType.FILLED.value, OrderEventType.PARTIALLY_FILLED.value}


def compare_order_events(release: Sequence[Mapping[str, Any]],
                         current: Sequence[Mapping[str, Any]]) -> List[AaDifference]:
    """
    Every value two streams of order events disagree on, in stream order. A field a record leaves
    out is null, as the stream writes it.

    Args:
        release: The release's events of the scenario, in `seq` order
        current: The current tree's, the same way

    Returns:
        The differences; empty where the two agree
    """
    return _compare(ORDER_EVENTS_STREAM, release, current, fields=None)


def compare_trades(release: Sequence[Mapping[str, Any]], current: Sequence[Mapping[str, Any]]
                   ) -> Tuple[List[AaDifference], List[str]]:
    """
    Every value two trade histories disagree on, over the fields both write.

    Args:
        release: The release's trades of the scenario, in their order
        current: The current tree's, the same way

    Returns:
        (the differences, the fields only one side writes)
    """
    release_fields = {key for row in release for key in row}
    current_fields = {key for row in current for key in row}
    shared = release_fields & current_fields
    differences = _compare(TRADES_STREAM, release, current, fields=shared)
    return differences, sorted(release_fields ^ current_fields)


def fills_of(events: Sequence[Mapping[str, Any]]) -> int:
    """
    How many of a stream's order events are fills.

    Args:
        events: The events

    Returns:
        The count
    """
    return sum(1 for event in events if event.get('event_type') in _FILLS)


def _compare(stream: str, release: Sequence[Mapping[str, Any]],
             current: Sequence[Mapping[str, Any]],
             fields: Optional[AbstractSet[str]]) -> List[AaDifference]:
    """
    The values two streams disagree on, record by record.

    Args:
        stream: The streams' name
        release: The release's records
        current: The current tree's records
        fields: The fields compared; None for every field either record holds

    Returns:
        The differences, in stream order
    """
    differences: List[AaDifference] = []
    for position in range(max(len(release), len(current))):
        left = release[position] if position < len(release) else None
        right = current[position] if position < len(current) else None
        if left is None or right is None:
            differences.append(AaDifference(
                stream=stream, position=position + 1, field=WHOLE_RECORD,
                release_value=_json(left) if left is not None else None,
                current_value=_json(right) if right is not None else None))
            continue
        keys = fields if fields is not None else set(left) | set(right)
        for key in sorted(keys):
            if left.get(key) != right.get(key):
                differences.append(AaDifference(
                    stream=stream, position=position + 1, field=key,
                    release_value=_json(left.get(key)), current_value=_json(right.get(key))))
    return differences


def _json(value: Any) -> str:
    """
    A value as JSON, keys sorted.

    Args:
        value: The value

    Returns:
        Its JSON text
    """
    return json.dumps(value, sort_keys=True, default=str)
