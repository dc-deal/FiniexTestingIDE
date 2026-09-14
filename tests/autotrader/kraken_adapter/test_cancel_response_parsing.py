"""
Kraken Adapter — the Cancel Answer Is Read, Not Assumed (#487)

`parse_cancel_response` used to return CANCELLED unconditionally and inspect nothing,
while the read path two hundred lines away modelled "the answer says something else"
properly. Kraken reports how many orders it actually cancelled: a `count` of 0 with no
error means the cancel named nothing, and booking that as CANCELLED drops a resting order
from our books while the venue keeps working it.

Measured 2026-09-13 (`python/experiments/venue_probes/probe_kraken_order_identity.py`):
Kraken does not in fact answer `count: 0` for an order that is already gone — it raises
`EOrder:Unknown order`, which the §43 ladder classifies before this parser is reached. So
the zero branch is unreachable on THIS venue and is here for the contract Kraken documents
and for the second adapter (#209). That is exactly why it needs a test: nothing else can
reach it.

Pure — no network, no credentials.
"""

import json
from datetime import datetime, timezone

import pytest

from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus

_KRAKEN_CONFIG = 'configs/brokers/kraken/kraken_spot_broker_config.json'
_REF = 'OCU3L6-C56FR-5BBVGF'


@pytest.fixture
def adapter() -> KrakenAdapter:
    """Tier-1 adapter — no credentials, no network."""
    with open(_KRAKEN_CONFIG, encoding='utf-8') as handle:
        return KrakenAdapter(json.load(handle))


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)


class TestWhatTheCountMeans:
    """One cancelled order is a cancel; none is not."""

    def test_a_cancelled_order_is_cancelled(self, adapter, now):
        response = adapter.parse_cancel_response({'count': 1}, _REF, now)
        assert response.status == BrokerOrderStatus.CANCELLED

    def test_nothing_cancelled_is_not_a_cancel(self, adapter, now):
        response = adapter.parse_cancel_response({'count': 0}, _REF, now)
        assert response.status == BrokerOrderStatus.UNKNOWN, (
            'the venue cancelled nothing — booking CANCELLED forgets an order it may '
            'still be working')

    def test_an_answer_without_a_count_says_nothing_either_way(self, adapter, now):
        """
        A missing field is not a zero.

        Defaulting it would invent a refusal out of an answer that simply does not carry
        the number — the same mistake `parse_query_response` was corrected for on
        2026-09-08, where an absent entry became "it is still working".
        """
        response = adapter.parse_cancel_response({}, _REF, now)
        assert response.status == BrokerOrderStatus.CANCELLED

    def test_the_raw_payload_survives_for_forensics(self, adapter, now):
        raw = {'count': 0, 'pending': False}
        response = adapter.parse_cancel_response(raw, _REF, now)
        assert response.raw_response == raw
        assert response.broker_ref == _REF
