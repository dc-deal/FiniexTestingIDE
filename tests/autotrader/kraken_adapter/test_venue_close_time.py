"""
FiniexTestingIDE - Kraken's Own Time of a Closed Order (#461)

QueryOrders names when the venue closed an order — `closetm`, epoch seconds, on an order it reports
closed, cancelled or expired. The adapter carries it as the answer's `ts_event`, beside `ts_init`,
our receipt of the answer; an open order carries none.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict

import pytest

from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus

_KRAKEN_CONFIG = 'configs/brokers/kraken/kraken_spot_broker_config.json'
_RECEIVED = datetime(2026, 10, 11, 9, 0, 1, tzinfo=timezone.utc)
_CLOSED = 1760173200.5678
_TXID = 'OQCLML-BW3P3-BUCMWZ'


@pytest.fixture
def adapter() -> KrakenAdapter:
    """Tier-1 adapter — no credentials, no network."""
    with open(_KRAKEN_CONFIG, encoding='utf-8') as handle:
        config: Dict[str, Any] = json.load(handle)
    return KrakenAdapter(config)


class TestTheVenuesCloseTime:

    def test_a_closed_order_carries_when_the_venue_closed_it(self, adapter):
        raw = {_TXID: {'status': 'closed', 'vol_exec': '0.00200000', 'price': '2498.63',
                       'fee': '0.03998', 'opentm': 1760173200.1234, 'closetm': _CLOSED}}

        response = adapter.parse_query_response(raw, _TXID, _RECEIVED)

        assert response.status == BrokerOrderStatus.FILLED
        assert response.ts_event == datetime.fromtimestamp(_CLOSED, tz=timezone.utc)
        assert response.ts_init == _RECEIVED, 'our receipt stays beside it'

    @pytest.mark.parametrize('kraken_status', ['canceled', 'expired'])
    def test_an_order_the_venue_ended_carries_it_too(self, adapter, kraken_status):
        raw = {_TXID: {'status': kraken_status, 'vol_exec': '0.00000000',
                       'opentm': 1760173200.1234, 'closetm': _CLOSED}}

        response = adapter.parse_query_response(raw, _TXID, _RECEIVED)

        assert response.ts_event == datetime.fromtimestamp(_CLOSED, tz=timezone.utc)

    def test_an_open_order_carries_none(self, adapter):
        """Kraken reports `closetm` 0 on an order still open; when it opened is no event here."""
        raw = {_TXID: {'status': 'open', 'vol_exec': '0.00100000', 'price': '2498.60',
                       'opentm': 1760173200.1234, 'closetm': 0}}

        response = adapter.parse_query_response(raw, _TXID, _RECEIVED)

        assert response.ts_event is None

    def test_an_answer_without_the_field_carries_none(self, adapter):
        raw = {_TXID: {'status': 'closed', 'vol_exec': '0.00200000', 'price': '2498.63'}}

        response = adapter.parse_query_response(raw, _TXID, _RECEIVED)

        assert response.ts_event is None
