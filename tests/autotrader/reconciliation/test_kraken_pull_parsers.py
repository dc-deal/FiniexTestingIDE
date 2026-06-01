"""
KrakenAdapter broker truth-pull parsers (#151) — pure, offline.

Feeds raw Kraken payloads directly to the _parse_* layers (no network, no
credentials). Construction uses the shipped Kraken spot config so the reverse
pair→symbol mapping resolves (XETHZUSD → ETHUSD).
"""

import json

import pytest

from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderType

_CONFIG_PATH = 'configs/brokers/kraken/kraken_spot_broker_config.json'


@pytest.fixture
def kraken_adapter() -> KrakenAdapter:
    with open(_CONFIG_PATH, 'r') as f:
        return KrakenAdapter(json.load(f))


def test_parse_openorders(kraken_adapter):
    raw = {
        'open': {
            'OABC-123': {
                'status': 'open',
                'vol': '0.01',
                'descr': {
                    'pair': 'XETHZUSD',
                    'type': 'buy',
                    'ordertype': 'limit',
                    'price': '2000.0',
                },
            }
        }
    }
    orders = kraken_adapter._parse_openorders_response(raw)
    assert len(orders) == 1
    o = orders[0]
    assert o.broker_ref == 'OABC-123'
    assert o.symbol == 'ETHUSD'            # reverse-mapped from XETHZUSD
    assert o.direction == OrderDirection.LONG
    assert o.order_type == OrderType.LIMIT
    assert o.lots == 0.01
    assert o.price == 2000.0
    assert o.status == BrokerOrderStatus.PENDING


def test_parse_openorders_sell_maps_short(kraken_adapter):
    raw = {'open': {'O1': {'status': 'open', 'vol': '0.02',
                           'descr': {'pair': 'XETHZUSD', 'type': 'sell',
                                     'ordertype': 'limit', 'price': '2500.0'}}}}
    orders = kraken_adapter._parse_openorders_response(raw)
    assert orders[0].direction == OrderDirection.SHORT


def test_parse_openorders_dryrun_sentinel_empty(kraken_adapter):
    raw = {kraken_adapter._DRY_RUN_SENTINEL: 'openorders'}
    assert kraken_adapter._parse_openorders_response(raw) == []


def test_parse_openorders_empty(kraken_adapter):
    assert kraken_adapter._parse_openorders_response({'open': {}}) == []


def test_parse_balance_drops_zero(kraken_adapter):
    raw = {'ZUSD': '100.50', 'XETH': '0.0200', 'XXBT': '0.0'}
    balances = kraken_adapter._parse_balance_response(raw)
    assert balances == {'ZUSD': 100.5, 'XETH': 0.02}


def test_parse_balance_dryrun_sentinel_empty(kraken_adapter):
    raw = {kraken_adapter._DRY_RUN_SENTINEL: 'balance'}
    assert kraken_adapter._parse_balance_response(raw) == {}


def test_parse_openpositions(kraken_adapter):
    raw = {
        'TPOS-1': {
            'pair': 'XETHZUSD',
            'type': 'buy',
            'vol': '0.01',
            'cost': '20.0',
            'net': '1.5',
        }
    }
    positions = kraken_adapter._parse_openpositions_response(raw)
    assert len(positions) == 1
    p = positions[0]
    assert p.broker_ref == 'TPOS-1'
    assert p.symbol == 'ETHUSD'
    assert p.lots == 0.01
    assert p.entry_price == 2000.0       # cost / vol = 20.0 / 0.01
    assert p.unrealized_pnl == 1.5


def test_parse_openpositions_dryrun_sentinel_empty(kraken_adapter):
    raw = {kraken_adapter._DRY_RUN_SENTINEL: 'openpositions'}
    assert kraken_adapter._parse_openpositions_response(raw) == []
