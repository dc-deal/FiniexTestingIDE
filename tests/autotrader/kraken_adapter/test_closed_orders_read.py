"""
Kraken Adapter — Reading Orders That Are No Longer Working (#487)

`get_broker_orders` answers about OPEN orders only, so an order that filled is invisible to
it and "filled" and "the venue never took it" arrive identically. Resolving a write whose
answer was lost is exactly the case where no reference came back, so a reference lookup
cannot stand in either — what is left is the time range and our own key.

Measured 2026-09-13 (`python/experiments/venue_probes/probe_kraken_order_identity.py`), and
it is the reason this route exists rather than a second QueryOrders call:

    QueryOrders   {'cl_ord_id': K}              → EGeneral:Invalid arguments
    QueryOrders   {'txid': T, 'cl_ord_id': K}   → answers, keyed by T
    ClosedOrders  {'cl_ord_id': K}              → answers, and returned TWO orders for one
                                                  key before a close minted its own counter

Pure — no network, no credentials.
"""

import json
from datetime import datetime, timezone

import pytest

from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderType

_KRAKEN_CONFIG = 'configs/brokers/kraken/kraken_spot_broker_config.json'

_START = datetime(2026, 9, 8, 7, 0, tzinfo=timezone.utc)
_END = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def adapter() -> KrakenAdapter:
    """Tier-1 adapter — no credentials, no network."""
    with open(_KRAKEN_CONFIG, encoding='utf-8') as handle:
        return KrakenAdapter(json.load(handle))


def _entry(status: str, ordertype: str = 'limit', vol: str = '0.01',
           vol_exec: str = '0.0', key: str = 'p5669_1') -> dict:
    """One Kraken closed-order body, in the shape the venue reports it."""
    return {
        'status': status,
        'vol': vol,
        'vol_exec': vol_exec,
        'cl_ord_id': key,
        'descr': {'pair': 'XBTUSD', 'type': 'buy', 'ordertype': ordertype,
                  'price': '40000.0'},
    }


class TestThePayload:
    """What goes on the wire — the half a probe measured."""

    def test_the_range_is_sent_as_unix_seconds(self, adapter):
        payload = adapter._build_closedorders_payload(_START, _END)
        assert payload['start'] == str(int(_START.timestamp()))
        assert payload['end'] == str(int(_END.timestamp()))

    def test_no_key_means_no_key_field(self, adapter):
        payload = adapter._build_closedorders_payload(_START, _END)
        assert 'cl_ord_id' not in payload, (
            'an empty key must not be sent — the venue would answer about the whole range '
            'while the caller believes it asked about one order')

    def test_the_key_narrows_the_range(self, adapter):
        payload = adapter._build_closedorders_payload(_START, _END, 'p5669_1')
        assert payload['cl_ord_id'] == 'p5669_1'

    def test_the_key_is_truncated_to_the_venue_limit(self, adapter):
        payload = adapter._build_closedorders_payload(_START, _END, 'x' * 40)
        assert len(payload['cl_ord_id']) == 18, (
            'Kraken allows 18 ASCII characters and refuses a longer one at runtime')


class TestTheAnswer:
    """Reading the venue's own words back."""

    def test_a_filled_order_is_visible_where_the_open_pull_is_blind(self, adapter):
        orders = adapter._parse_closedorders_response({
            'closed': {'OD6OFT-CXNPX-Z6GVNP': _entry('closed', vol_exec='0.01')},
            'count': 1,
        })

        assert len(orders) == 1
        assert orders[0].broker_ref == 'OD6OFT-CXNPX-Z6GVNP'
        assert orders[0].status == BrokerOrderStatus.FILLED
        assert orders[0].filled_lots == 0.01
        assert orders[0].client_order_id == 'p5669_1'

    def test_a_cancelled_order_is_not_a_filled_one(self, adapter):
        orders = adapter._parse_closedorders_response({
            'closed': {'OCU3L6-C56FR-5BBVGF': _entry('canceled')},
        })
        assert orders[0].status == BrokerOrderStatus.CANCELLED
        assert orders[0].filled_lots == 0.0

    def test_two_orders_can_answer_to_one_key(self, adapter):
        """
        The measured shape, and the reason a close now mints its own counter.

        Before that, one key named an entry AND its close, so the resolution could not say
        which order the venue was describing.
        """
        orders = adapter._parse_closedorders_response({
            'closed': {
                'OCU3L6-C56FR-5BBVGF': _entry('canceled'),
                'OD6OFT-CXNPX-Z6GVNP': _entry('closed', vol_exec='0.01'),
            },
        })
        assert len({o.client_order_id for o in orders}) == 1
        assert len({o.broker_ref for o in orders}) == 2

    def test_an_unnameable_status_is_never_read_as_still_working(self, adapter):
        """
        The inversion `parse_query_response` was corrected for on 2026-09-08.

        The venue put this order in the CLOSED bucket. A status word we cannot map means we
        cannot name the state — it does NOT mean the order is pending.
        """
        orders = adapter._parse_closedorders_response({
            'closed': {'OZZZZZ-ZZZZZ-ZZZZZZ': _entry('some-new-kraken-word')},
        })
        assert orders[0].status == BrokerOrderStatus.UNKNOWN

    def test_an_empty_range_is_an_empty_list(self, adapter):
        assert adapter._parse_closedorders_response({'closed': {}, 'count': 0}) == []
        assert adapter._parse_closedorders_response({}) == []

    def test_a_stop_reports_its_trigger_not_a_limit_price(self, adapter):
        """Same convention as the open pull — the shared parse is what guarantees it."""
        orders = adapter._parse_closedorders_response({
            'closed': {'OSTOP1-AAAAA-BBBBBB': _entry('canceled', ordertype='stop-loss')},
        })
        assert orders[0].order_type == OrderType.STOP
        assert orders[0].stop_price == 40000.0
        assert orders[0].price is None
        assert orders[0].direction == OrderDirection.LONG


class TestDryRun:
    """A dry run reaches no venue and must answer nothing rather than something."""

    def test_dry_run_answers_empty(self, adapter):
        adapter._dry_run = True
        raw = adapter._do_request_closedorders(
            adapter._build_closedorders_payload(_START, _END))
        assert adapter._parse_closedorders_response(raw) == []
