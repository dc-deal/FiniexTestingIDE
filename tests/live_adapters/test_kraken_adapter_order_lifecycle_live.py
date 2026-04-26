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

from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderType
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus


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
    broker_settings['rate_limit_interval_s'] = 0.5

    adapter = KrakenAdapter(broker_config)
    adapter.enable_live(broker_settings)
    return adapter


class TestKrakenAdapterOrderLifecyclePhase2:
    """
    Phase 2: Real order lifecycle — place → query → modify → query → cancel.

    Places a LIMIT buy at $100 (far below market ~$2000+) — never filled.
    Requires a funded Kraken account. Lot size 0.1 ETH meets Kraken's $5 cost
    minimum ($10 at $100/ETH). A try/finally block cancels the order if the
    test fails mid-way to avoid leaving open orders on the account.
    """

    def test_limit_order_lifecycle(self, live_adapter_real):
        """LIMIT buy lifecycle: place → query → modify → query → cancel."""
        txid = None
        try:
            # 1. Place LIMIT buy far below market — never fills
            response = live_adapter_real.execute_order(
                symbol='ETHUSD',
                direction=OrderDirection.LONG,
                lots=0.1,
                order_type=OrderType.LIMIT,
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
            status_response = live_adapter_real.check_order_status(txid)
            assert status_response.status == BrokerOrderStatus.PENDING, (
                f"Expected PENDING for open order, got: {status_response.status}"
            )

            # 3. Modify price — Kraken EditOrder returns a new txid
            modify_response = live_adapter_real.modify_order(txid, symbol='ETHUSD', new_price=110.0)
            assert modify_response.status == BrokerOrderStatus.PENDING, (
                f"Expected PENDING after modify, got: {modify_response.status}"
                f" — {modify_response.rejection_reason}"
            )
            new_txid = modify_response.broker_ref
            assert new_txid != txid, 'Expected new txid from Kraken EditOrder'
            txid = new_txid

            # 4. Query modified order — should still be open
            modified_status = live_adapter_real.check_order_status(txid)
            assert modified_status.status == BrokerOrderStatus.PENDING, (
                f"Expected PENDING for modified order, got: {modified_status.status}"
            )

            # 5. Cancel
            cancel_response = live_adapter_real.cancel_order(txid)
            assert cancel_response.status == BrokerOrderStatus.CANCELLED, (
                f"Expected CANCELLED, got: {cancel_response.status}"
            )
            txid = None  # prevent double-cancel in finally

        finally:
            if txid is not None:
                live_adapter_real.cancel_order(txid)
