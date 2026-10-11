"""
FiniexTestingIDE - Kraken Reports the Fee in Its Answer (#508)

The answer that says an order filled carries the order's total fee — measured 2026-10-11 with
real orders: QueryOrders' `fee` is present in the first answer that says closed and does not
change afterwards. The adapter reads it beside the executed volume and the price, declares that
it does, and asks for the fee in the quote currency on every order, because the spot books charge
it there.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict

import pytest

from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus
from python.framework.types.trading_env_types.order_types import (
    FeeReport,
    OrderDirection,
    OrderType,
)

_KRAKEN_CONFIG = 'configs/brokers/kraken/kraken_spot_broker_config.json'
_TS = datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc)
_TXID = 'ORVS4M-TG7MT-BELUXN'


def _config() -> Dict[str, Any]:
    """
    The tracked Kraken broker configuration.

    Returns:
        The parsed configuration
    """
    with open(_KRAKEN_CONFIG, encoding='utf-8') as handle:
        return json.load(handle)


class _LiveKraken(KrakenAdapter):
    """Kraken as a session holds it with dry run off — no credentials needed to ask what it declares."""

    def __init__(self, broker_config: Dict[str, Any]):
        super().__init__(broker_config)
        self._dry_run = False


@pytest.fixture
def adapter() -> KrakenAdapter:
    """Tier-1 adapter — no credentials, no network."""
    return KrakenAdapter(_config())


class TestTheFeeIsReadFromTheStatusAnswer:
    """`fill_fee` sits beside `filled_lots` and `fill_price`, read wherever volume is."""

    def test_a_closed_order_carries_its_total_fee(self, adapter):
        # The shape of the measured answer of 2026-10-11, the buy of the probe
        raw = {_TXID: {'status': 'closed', 'vol': '0.00200000', 'vol_exec': '0.00200000',
                       'cost': '4.99726', 'fee': '0.03998', 'price': '2498.63',
                       'oflags': 'fciq'}}

        response = adapter.parse_query_response(raw, _TXID, _TS)

        assert response.status == BrokerOrderStatus.FILLED
        assert response.filled_lots == pytest.approx(0.002)
        assert response.fill_fee == pytest.approx(0.03998)

    def test_an_open_order_with_executed_volume_carries_the_fee_of_that_volume(self, adapter):
        raw = {_TXID: {'status': 'open', 'vol_exec': '0.00100000', 'price': '2498.60',
                       'fee': '0.01999'}}

        response = adapter.parse_query_response(raw, _TXID, _TS)

        assert response.filled_lots == pytest.approx(0.001)
        assert response.fill_fee == pytest.approx(0.01999)

    def test_nothing_executed_reports_no_fee(self, adapter):
        raw = {_TXID: {'status': 'open', 'vol_exec': '0.00000000', 'fee': '0.00000'}}

        assert adapter.parse_query_response(raw, _TXID, _TS).fill_fee is None

    def test_an_answer_with_volume_and_no_fee_field_reports_none(self, adapter):
        """None, never zero: a zero would be booked as a fee the venue did not charge."""
        raw = {_TXID: {'status': 'closed', 'vol_exec': '0.00200000', 'price': '2498.63'}}

        assert adapter.parse_query_response(raw, _TXID, _TS).fill_fee is None


class TestEveryOrderAsksForTheFeeInTheQuote:
    """`oflags=fciq` on every AddOrder — the documentation's sell-side default is the base."""

    @pytest.mark.parametrize('order_type, kwargs', [
        (OrderType.MARKET, {}),
        (OrderType.LIMIT, {'limit_price': 2400.0}),
        (OrderType.STOP, {'stop_price': 2600.0}),
        (OrderType.STOP_LIMIT, {'stop_price': 2600.0, 'limit_price': 2610.0}),
    ], ids=['market', 'limit', 'stop', 'stop_limit'])
    @pytest.mark.parametrize('direction', [OrderDirection.LONG, OrderDirection.SHORT],
                             ids=['buy', 'sell'])
    def test_the_payload_carries_fciq(self, adapter, order_type, kwargs, direction):
        payload = adapter.build_submit_payload(
            symbol='ETHUSD', direction=direction, lots=0.002, order_type=order_type, **kwargs)

        assert payload['oflags'] == 'fciq'


class TestTheCapabilityIsDeclared:
    """What a session reads to know where the fee is — and whether its absence is news."""

    def test_a_session_against_the_venue_reads_it_from_the_status_answer(self):
        assert _LiveKraken(_config()).get_order_capabilities().fee_report is (
            FeeReport.STATUS_ANSWER)

    def test_a_dry_run_declares_the_venue_too(self, adapter):
        """
        A session caches the capabilities before it knows whether it is a dry run, so they
        describe the venue; the executor tells a dry run's answers apart by their source.
        """
        assert adapter.get_order_capabilities().fee_report is FeeReport.STATUS_ANSWER
        assert not adapter.reads_venue_account()
