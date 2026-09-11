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

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.logging.global_logger import GlobalLogger
from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter
from python.framework.trading_env.live.live_request_processor import LiveRequestProcessor
from python.framework.types.live_types.live_execution_types import (
    BrokerOrderStatus,
    BrokerResponse,
    TimeoutConfig,
)
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderType
from tests.live_adapters.conftest import record_observed_adapter

_BROKER_CONFIG_PATH = Path('configs/brokers/kraken/kraken_spot_broker_config.json')
_CREDENTIALS_PATH = Path('user_configs/credentials/kraken_credentials.json')

_POLL_MAX = 10  # max query_order_sync attempts before giving up


@pytest.fixture(scope='module')
def live_adapter_fill(request, real_orders_authorised):
    """
    KrakenAdapter with dry_run=False for fill validation.

    Returns:
        KrakenAdapter with Tier 3 enabled and dry_run disabled
    """
    if not _CREDENTIALS_PATH.exists():
        pytest.skip(f'Kraken credentials not found: {_CREDENTIALS_PATH}')

    with open(_BROKER_CONFIG_PATH, 'r') as f:
        broker_config = json.load(f)

    # The SAME source a live session reads (#505 follow-up). `configs/broker_settings/` was
    # a leftover of the #252 migration that production had stopped reading, so this suite —
    # the only one that spends real money — was proving something about a file nobody obeyed.
    entry = MarketConfigManager().get_broker_entry('kraken_spot')
    transport = entry.broker_transport.model_copy(update={'rate_limit_interval_s': 0.5})
    credentials_file = entry.credentials_file
    # Forced here, never read from config: this fixture's phase decides it.
    dry_run = False

    adapter = KrakenAdapter(broker_config)
    adapter.enable_live(
        credentials_file=credentials_file,
        dry_run=dry_run,
        transport=transport,
    )
    # The certificate records what was BUILT here, not what the settings file says.
    record_observed_adapter(
        request,
        phase='real_orders',
        dry_run=dry_run,
        api_base_url=transport.api_base_url)
    return adapter


@pytest.fixture(scope='module')
def processor():
    """LiveRequestProcessor used to drive the adapter's Tier-3 layers."""
    return LiveRequestProcessor(
        logger=GlobalLogger(name='LiveAdapterTestFill'),
        timeout_config=TimeoutConfig(order_timeout_seconds=30.0),
    )


def _poll_until_filled(processor: LiveRequestProcessor, adapter: KrakenAdapter, txid: str) -> BrokerResponse:
    """
    Poll query_order_sync until FILLED or _POLL_MAX exhausted.

    Args:
        processor: LiveRequestProcessor driving the Tier-3 query layer
        adapter: Live KrakenAdapter instance
        txid: Broker order reference to poll

    Returns:
        Last BrokerResponse (FILLED or final status after timeout)
    """
    response = processor.query_order_sync(txid, adapter)
    for _ in range(_POLL_MAX - 1):
        if response.status == BrokerOrderStatus.FILLED:
            return response
        response = processor.query_order_sync(txid, adapter)
    return response


class TestKrakenAdapterOrderLifecycleFill:
    """
    Fill validation: MARKET buy → poll FILLED → MARKET sell → poll FILLED.

    Verifies that query_order_sync correctly parses Kraken's QueryOrders
    response for filled orders: fill_price > 0, filled_lots populated.
    Net exposure is ~0. Cost: ~$0.012 in fees (0.001 ETH × ~$2300 × 0.26% × 2).
    """

    def test_market_order_fill_roundtrip(self, live_adapter_fill, processor):
        """MARKET buy 0.001 ETHUSD → verify fill → MARKET sell → verify fill."""
        # 1. Buy
        buy_response = processor.submit_open_order(
            symbol='ETHUSD',
            direction=OrderDirection.LONG,
            lots=0.001,
            order_type=OrderType.MARKET,
            adapter=live_adapter_fill,
        )
        assert buy_response.status == BrokerOrderStatus.PENDING, (
            f'Expected PENDING after MARKET buy, got: {buy_response.status}'
            f' — {buy_response.rejection_reason}'
        )
        assert not buy_response.broker_ref.startswith('DRYRUN-'), (
            'Expected real txid, got DRYRUN ref — adapter may be in dry_run mode'
        )
        buy_txid = buy_response.broker_ref

        # 2. Poll until buy is filled (MARKET orders fill in ~100-500ms)
        buy_fill = _poll_until_filled(processor, live_adapter_fill, buy_txid)
        assert buy_fill.status == BrokerOrderStatus.FILLED, (
            f'Buy order not filled after {_POLL_MAX} polls: {buy_fill.status}'
        )
        assert buy_fill.fill_price is not None and buy_fill.fill_price > 0, (
            f'Expected fill_price > 0, got: {buy_fill.fill_price}'
        )
        assert buy_fill.filled_lots is not None and buy_fill.filled_lots > 0, (
            f'Expected filled_lots > 0, got: {buy_fill.filled_lots}'
        )

        # 3. Immediately sell to close — net exposure back to zero
        sell_response = processor.submit_open_order(
            symbol='ETHUSD',
            direction=OrderDirection.SHORT,
            lots=0.001,
            order_type=OrderType.MARKET,
            adapter=live_adapter_fill,
        )
        assert sell_response.status == BrokerOrderStatus.PENDING, (
            f'Expected PENDING after MARKET sell, got: {sell_response.status}'
            f' — {sell_response.rejection_reason}'
        )
        sell_txid = sell_response.broker_ref

        # 4. Poll until sell is filled
        sell_fill = _poll_until_filled(processor, live_adapter_fill, sell_txid)
        assert sell_fill.status == BrokerOrderStatus.FILLED, (
            f'Sell order not filled after {_POLL_MAX} polls: {sell_fill.status}'
        )
        assert sell_fill.fill_price is not None and sell_fill.fill_price > 0, (
            f'Expected fill_price > 0, got: {sell_fill.fill_price}'
        )
        assert sell_fill.filled_lots is not None and sell_fill.filled_lots > 0, (
            f'Expected filled_lots > 0, got: {sell_fill.filled_lots}'
        )
