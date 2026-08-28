"""
FiniexTestingIDE - Signal Field Watch
Names the envelope fields the producer sends that our models do not read (#141, #468).

The producer ships an additive field as a MINOR, which tells us the shape GREW but not
WHAT grew — and our models discard whatever they do not declare, so the new field stays
invisible until something depends on it. This makes it visible once per distinct set: a
grown shape becomes one notice rather than one per arrival.

Deliberately a NOTICE and nothing more. The values are still discarded, nothing is stored
on the snapshot and nothing reaches the parquet — the projection stays lean on purpose,
and a diagnosis needs the field's NAME, not its data.

Shared by both live transports rather than copied into each: the whole job of this watch
is to notice that the producer's shape changed, and two copies of it are two things that
can disagree about what counts as changed.
"""

from typing import Any, Dict, List, Set

from python.framework.types.signal_data_types import SentimentResult, SignalSnapshot


class SignalFieldWatch:
    """
    Remembers which unread field names have already been announced this session.

    Stateful for exactly one reason — suppression. A transport receives the same grown
    shape on every arrival, and an operator who is told about it every ten minutes stops
    reading the channel that would have carried the next real finding.
    """

    def __init__(self):
        """Initialize a watch that has announced nothing."""
        self._announced: Set[str] = set()

    def take_new(self, payload: Dict[str, Any]) -> List[str]:
        """
        Field names in this payload that we do not read and have not announced yet.

        Computed against the raw payload rather than the parsed object because that is
        the only place both shapes exist at once — the parsed model has already dropped
        exactly the fields this is looking for.

        Args:
            payload: The envelope as it arrived, before parsing

        Returns:
            The newly-seen unread names, sorted; empty when nothing is new
        """
        unread = set(payload) - set(SignalSnapshot.model_fields)
        for row in (payload.get('result') or []):
            if isinstance(row, dict):
                unread |= {f'result.{key}'
                           for key in set(row) - set(SentimentResult.model_fields)}
        unread -= self._announced
        self._announced |= unread
        return sorted(unread)
