"""
FiniexTestingIDE - Kraken Adapter Live Integration Test — Fill Validation

Validates that KrakenAdapter correctly reads fill responses from the Kraken API:
fill_price, filled_lots, and BrokerOrderStatus.FILLED from QueryOrders.

Flow: MARKET buy 0.001 ETHUSD → poll until filled → MARKET sell 0.001 ETHUSD → poll until filled.
Net exposure: ~0. Cost: ~$0.012 in maker/taker fees.

Run explicitly:
    pytest tests/live_adapters/ -v -m live_adapter

Skipped by default and excluded from the unified test runner.
Requires: user_configs/credentials/kraken_credentials.json
          Active Kraken account (funds not required beyond fees)
"""

import json
from pathlib import Path

import pytest

from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderType
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus, BrokerResponse


_BROKER_CONFIG_PATH = Path('configs/brokers/kraken/kraken_spot_broker_config.json')
_BROKER_SETTINGS_PATH = Path('configs/broker_settings/kraken_spot.json')
_CREDENTIALS_PATH = Path('user_configs/credentials/kraken_credentials.json')

_POLL_MAX = 10  # max check_order_status attempts before giving up


@pytest.fixture(scope='module')
def live_adapter_fill():
    """
    KrakenAdapter with dry_run=False for fill validation.

    Returns:
        KrakenAdapter with Tier 3 enabled and dry_run disabled
    """
    if not _CREDENTIALS_PATH.exists():
        pytest.skip(f'Kraken credentials not found: {_CREDENTIALS_PATH}')

    with open(_BROKER_CONFIG_PATH, 'r') as f:
        broker_config = json.load(f)

    with open(_BROKER_SETTINGS_PATH, 'r') as f:
        broker_settings = json.load(f)

    broker_settings['dry_run'] = False
    broker_settings['rate_limit_interval_s'] = 0.5

    adapter = KrakenAdapter(broker_config)
    adapter.enable_live(broker_settings)
    return adapter


def _poll_until_filled(adapter: KrakenAdapter, txid: str) -> BrokerResponse:
    """
    Poll check_order_status until FILLED or _POLL_MAX exhausted.

    Args:
        adapter: Live KrakenAdapter instance
        txid: Broker order reference to poll

    Returns:
        Last BrokerResponse (FILLED or final status after timeout)
    """
    response = adapter.check_order_status(txid)
    for _ in range(_POLL_MAX - 1):
        if response.status == BrokerOrderStatus.FILLED:
            return response
        response = adapter.check_order_status(txid)
    return response


class TestKrakenAdapterOrderLifecycleFill:
    """
    Fill validation: MARKET buy → poll FILLED → MARKET sell → poll FILLED.

    Verifies that check_order_status() correctly parses Kraken's QueryOrders
    response for filled orders: fill_price > 0, filled_lots populated.
    Net exposure is ~0. Cost: ~$0.012 in fees (0.001 ETH × ~$2300 × 0.26% × 2).
    """

    def test_market_order_fill_roundtrip(self, live_adapter_fill):
        """MARKET buy 0.001 ETHUSD → verify fill → MARKET sell → verify fill."""
        # 1. Buy
        buy_response = live_adapter_fill.execute_order(
            symbol='ETHUSD',
            direction=OrderDirection.LONG,
            lots=0.001,
            order_type=OrderType.MARKET,
        )
        assert buy_response.status == BrokerOrderStatus.PENDING, (
            f"Expected PENDING after MARKET buy, got: {buy_response.status}"
            f" — {buy_response.rejection_reason}"
        )
        assert not buy_response.broker_ref.startswith('DRYRUN-'), (
            f"Expected real txid, got DRYRUN ref — adapter may be in dry_run mode"
        )
        buy_txid = buy_response.broker_ref

        # 2. Poll until buy is filled (MARKET orders fill in ~100-500ms)
        buy_fill = _poll_until_filled(live_adapter_fill, buy_txid)
        assert buy_fill.status == BrokerOrderStatus.FILLED, (
            f"Buy order not filled after {_POLL_MAX} polls: {buy_fill.status}"
        )
        assert buy_fill.fill_price is not None and buy_fill.fill_price > 0, (
            f"Expected fill_price > 0, got: {buy_fill.fill_price}"
        )
        assert buy_fill.filled_lots is not None and buy_fill.filled_lots > 0, (
            f"Expected filled_lots > 0, got: {buy_fill.filled_lots}"
        )

        # 3. Immediately sell to close — net exposure back to zero
        sell_response = live_adapter_fill.execute_order(
            symbol='ETHUSD',
            direction=OrderDirection.SHORT,
            lots=0.001,
            order_type=OrderType.MARKET,
        )
        assert sell_response.status == BrokerOrderStatus.PENDING, (
            f"Expected PENDING after MARKET sell, got: {sell_response.status}"
            f" — {sell_response.rejection_reason}"
        )
        sell_txid = sell_response.broker_ref

        # 4. Poll until sell is filled
        sell_fill = _poll_until_filled(live_adapter_fill, sell_txid)
        assert sell_fill.status == BrokerOrderStatus.FILLED, (
            f"Sell order not filled after {_POLL_MAX} polls: {sell_fill.status}"
        )
        assert sell_fill.fill_price is not None and sell_fill.fill_price > 0, (
            f"Expected fill_price > 0, got: {sell_fill.fill_price}"
        )
        assert sell_fill.filled_lots is not None and sell_fill.filled_lots > 0, (
            f"Expected filled_lots > 0, got: {sell_fill.filled_lots}"
        )
