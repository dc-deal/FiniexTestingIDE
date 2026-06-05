"""
FiniexTestingIDE - Kraken Adapter Live Integration Test — Phase 2 (Real Orders)

Validates KrakenAdapter Tier 3 full order lifecycle against the real Kraken API
using real (non-validated) orders. Tests place → query → modify → query → cancel
using a LIMIT buy far below market — orders are never filled.

Lot size 0.1 ETH @ $100 meets Kraken's ~$5 cost minimum ($10 reserved, returned on cancel).

Run explicitly:
    pytest tests/live_adapters/ -v -m live_adapter

Skipped by default and excluded from the unified test runner.
Requires: user_configs/credentials/kraken_credentials.json
          USD balance (~$10 reserved during test, returned on cancel)
"""

import json
from pathlib import Path

import pytest

from python.framework.logging.global_logger import GlobalLogger
from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter
from python.framework.trading_env.live.live_request_processor import LiveRequestProcessor
from python.framework.types.config_types.market_config_types import BrokerTransportConfig
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus, TimeoutConfig
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderType


_BROKER_CONFIG_PATH = Path('configs/brokers/kraken/kraken_spot_broker_config.json')
_BROKER_SETTINGS_PATH = Path('configs/broker_settings/kraken_spot.json')
_CREDENTIALS_PATH = Path('user_configs/credentials/kraken_credentials.json')


@pytest.fixture(scope='module')
def live_adapter_real():
    """
    KrakenAdapter with dry_run=False — places real orders against the Kraken API.

    Returns:
        KrakenAdapter with Tier 3 enabled and dry_run disabled
    """
    if not _CREDENTIALS_PATH.exists():
        pytest.skip(f'Kraken credentials not found: {_CREDENTIALS_PATH}')

    with open(_BROKER_CONFIG_PATH, 'r') as f:
        broker_config = json.load(f)

    with open(_BROKER_SETTINGS_PATH, 'r') as f:
        broker_settings = json.load(f)

    # Phase 2: real orders required — dry_run must be off
    broker_settings['dry_run'] = False
    broker_settings['broker_transport']['rate_limit_interval_s'] = 0.5

    adapter = KrakenAdapter(broker_config)
    adapter.enable_live(
        credentials_file=broker_settings['credentials_file'],
        dry_run=broker_settings['dry_run'],
        transport=BrokerTransportConfig(**broker_settings['broker_transport']),
    )
    return adapter


@pytest.fixture(scope='module')
def processor():
    """LiveRequestProcessor used to drive the adapter's Tier-3 layers."""
    return LiveRequestProcessor(
        logger=GlobalLogger(name='LiveAdapterTestLive'),
        timeout_config=TimeoutConfig(order_timeout_seconds=30.0),
    )


class TestKrakenAdapterOrderLifecyclePhase2:
    """
    Phase 2: Real order lifecycle — place → query → modify → query → cancel.

    Places a LIMIT buy at $100 (far below market ~$2000+) — never filled.
    Requires a funded Kraken account. Lot size 0.1 ETH meets Kraken's $5 cost
    minimum ($10 at $100/ETH). A try/finally block cancels the order if the
    test fails mid-way to avoid leaving open orders on the account.
    """

    def test_limit_order_lifecycle(self, live_adapter_real, processor):
        """LIMIT buy lifecycle: place → query → modify → query → cancel."""
        txid = None
        try:
            # 1. Place LIMIT buy far below market — never fills
            response = processor.submit_open_order(
                symbol='ETHUSD',
                direction=OrderDirection.LONG,
                lots=0.1,
                order_type=OrderType.LIMIT,
                adapter=live_adapter_real,
                price=100.0,
            )
            assert response.status == BrokerOrderStatus.PENDING, (
                f"Expected PENDING after placing LIMIT order, got: {response.status}"
                f" — {response.rejection_reason}"
            )
            assert not response.broker_ref.startswith('DRYRUN-'), (
                f"Expected real txid, got DRYRUN ref — adapter may still be in dry_run mode"
            )
            txid = response.broker_ref

            # 2. Query — order should be open (Kraken 'open' maps to PENDING)
            status_response = processor.query_order_sync(txid, live_adapter_real)
            assert status_response.status == BrokerOrderStatus.PENDING, (
                f"Expected PENDING for open order, got: {status_response.status}"
            )

            # 3. Modify price — Kraken AmendOrder amends in-place (same txid)
            modify_response = processor.modify_order_sync(
                broker_ref=txid,
                symbol='ETHUSD',
                new_price=110.0,
                new_stop_loss=None,
                new_take_profit=None,
                adapter=live_adapter_real,
            )
            assert modify_response.status == BrokerOrderStatus.PENDING, (
                f"Expected PENDING after modify, got: {modify_response.status}"
                f" — {modify_response.rejection_reason}"
            )
            assert modify_response.broker_ref == txid, (
                'Expected unchanged txid from Kraken AmendOrder (in-place amend)'
            )

            # 4. Query modified order — should still be open
            modified_status = processor.query_order_sync(txid, live_adapter_real)
            assert modified_status.status == BrokerOrderStatus.PENDING, (
                f"Expected PENDING for modified order, got: {modified_status.status}"
            )

            # 5. Cancel
            cancel_response = processor.cancel_order_sync(txid, live_adapter_real)
            assert cancel_response.status == BrokerOrderStatus.CANCELLED, (
                f"Expected CANCELLED, got: {cancel_response.status}"
            )
            txid = None  # prevent double-cancel in finally

        finally:
            if txid is not None:
                processor.cancel_order_sync(txid, live_adapter_real)
