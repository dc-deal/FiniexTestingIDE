"""
FiniexTestingIDE - Kraken Adapter Live Integration Test — Phase 1 (Dry-Run)

Validates KrakenAdapter Tier 3 order execution against the real Kraken API
using validate=true. Tests syntax validation, pair resolution, and API
acceptance for all supported order types. No funds are placed or moved.

Run explicitly:
    pytest tests/live_adapters/ -v -m live_adapter

Skipped by default and excluded from the unified test runner.
Requires: user_configs/credentials/kraken_credentials.json
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
def live_adapter():
    """
    KrakenAdapter loaded from tracked broker settings with dry_run=True enforced.

    Returns:
        KrakenAdapter with Tier 3 enabled and dry_run mode active
    """
    if not _CREDENTIALS_PATH.exists():
        pytest.skip(f'Kraken credentials not found: {_CREDENTIALS_PATH}')

    with open(_BROKER_CONFIG_PATH, 'r') as f:
        broker_config = json.load(f)

    with open(_BROKER_SETTINGS_PATH, 'r') as f:
        broker_settings = json.load(f)

    # Always enforce dry_run in test context — never place real orders
    broker_settings['dry_run'] = True
    # Reduce rate limit for test speed — validate=true calls are lenient
    broker_settings['rate_limit_interval_s'] = 0.5

    adapter = KrakenAdapter(broker_config)
    adapter.enable_live(broker_settings)
    return adapter


class TestKrakenAdapterOrderLifecycle:
    """
    Phase 1: Dry-run order lifecycle validation.

    Calls execute_order() directly against the real Kraken API with validate=true.
    Kraken validates syntax, pair resolution, and margin but does NOT place orders.
    All tests skip if credentials are not available.

    Note: execute_order() does not call validate_order() internally — invalid
    symbol and below-min-lot cases reach the API and return REJECTED.

    LIMIT order volume uses 0.1 ETH (@ $100 limit price → $10 cost) to satisfy
    Kraken's minimum order cost requirement (~$5). MARKET tests use 0.001 (volume_min).
    """

    def test_market_buy_dryrun(self, live_adapter):
        """MARKET buy with validate=true — expects synthetic DRYRUN ref."""
        response = live_adapter.execute_order(
            symbol='ETHUSD',
            direction=OrderDirection.LONG,
            lots=0.001,
            order_type=OrderType.MARKET,
        )
        assert response.status == BrokerOrderStatus.FILLED
        assert response.broker_ref.startswith('DRYRUN-'), (
            f"Expected DRYRUN ref, got: {response.broker_ref}"
        )

    def test_market_sell_dryrun(self, live_adapter):
        """MARKET sell with validate=true — expects synthetic DRYRUN ref."""
        response = live_adapter.execute_order(
            symbol='ETHUSD',
            direction=OrderDirection.SHORT,
            lots=0.001,
            order_type=OrderType.MARKET,
        )
        assert response.status == BrokerOrderStatus.FILLED
        assert response.broker_ref.startswith('DRYRUN-'), (
            f"Expected DRYRUN ref, got: {response.broker_ref}"
        )

    def test_limit_buy_dryrun(self, live_adapter):
        """LIMIT buy far below market — 0.1 ETH @ $100 meets Kraken cost minimum (~$10)."""
        response = live_adapter.execute_order(
            symbol='ETHUSD',
            direction=OrderDirection.LONG,
            lots=0.1,
            order_type=OrderType.LIMIT,
            price=100.0,
        )
        assert response.status == BrokerOrderStatus.FILLED
        assert response.broker_ref.startswith('DRYRUN-'), (
            f"Expected DRYRUN ref, got: {response.broker_ref}"
        )

    def test_limit_buy_with_sltp_dryrun(self, live_adapter):
        """LIMIT buy with stop_loss and take_profit kwargs — 0.1 ETH @ $100."""
        response = live_adapter.execute_order(
            symbol='ETHUSD',
            direction=OrderDirection.LONG,
            lots=0.1,
            order_type=OrderType.LIMIT,
            price=100.0,
            stop_loss=50.0,
            take_profit=200.0,
        )
        assert response.status == BrokerOrderStatus.FILLED
        assert response.broker_ref.startswith('DRYRUN-'), (
            f"Expected DRYRUN ref, got: {response.broker_ref}"
        )

    def test_invalid_symbol_rejected(self, live_adapter):
        """Unknown symbol reaches Kraken API — expects REJECTED response."""
        response = live_adapter.execute_order(
            symbol='XXXUSD',
            direction=OrderDirection.LONG,
            lots=0.001,
            order_type=OrderType.MARKET,
        )
        assert response.status == BrokerOrderStatus.REJECTED, (
            f"Expected REJECTED for unknown symbol, got: {response.status}"
        )
        assert response.rejection_reason, 'Expected non-empty rejection reason'

    def test_below_minimum_lot_rejected(self, live_adapter):
        """Volume below ETHUSD minimum (0.001) reaches Kraken API — expects REJECTED response."""
        response = live_adapter.execute_order(
            symbol='ETHUSD',
            direction=OrderDirection.LONG,
            lots=0.00001,
            order_type=OrderType.MARKET,
        )
        assert response.status == BrokerOrderStatus.REJECTED, (
            f"Expected REJECTED for below-min volume, got: {response.status}"
        )
        assert response.rejection_reason, 'Expected non-empty rejection reason'
