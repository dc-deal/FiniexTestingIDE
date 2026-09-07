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

from tests.live_adapters.conftest import record_observed_adapter

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
def live_adapter_real(request, real_orders_authorised):
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
    # The certificate records what was BUILT here, not what the settings file says.
    record_observed_adapter(
        request,
        phase='real_orders',
        dry_run=broker_settings['dry_run'],
        api_base_url=broker_settings['broker_transport']['api_base_url'])
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
                limit_price=100.0,
            )
            assert response.status == BrokerOrderStatus.PENDING, (
                f'Expected PENDING after placing LIMIT order, got: {response.status}'
                f' — {response.rejection_reason}'
            )
            assert not response.broker_ref.startswith('DRYRUN-'), (
                'Expected real txid, got DRYRUN ref — adapter may still be in dry_run mode'
            )
            txid = response.broker_ref

            # 2. Query — order should be open (Kraken 'open' maps to PENDING)
            status_response = processor.query_order_sync(txid, live_adapter_real)
            assert status_response.status == BrokerOrderStatus.PENDING, (
                f'Expected PENDING for open order, got: {status_response.status}'
            )

            # 3. Modify price — Kraken AmendOrder amends in-place (same txid).
            # `order_type` decides WHICH of Kraken's two amend fields the new price goes
            # to: `limit_price` here, `trigger_price` for a stop. Everything used to go
            # into limit_price, so amending a resting stop's trigger would have moved its
            # fill price instead (#500).
            modify_response = processor.modify_order_sync(
                broker_ref=txid,
                symbol='ETHUSD',
                order_type=OrderType.LIMIT,
                new_price=110.0,
                new_stop_loss=None,
                new_take_profit=None,
                adapter=live_adapter_real,
            )
            assert modify_response.status == BrokerOrderStatus.PENDING, (
                f'Expected PENDING after modify, got: {modify_response.status}'
                f' — {modify_response.rejection_reason}'
            )
            assert modify_response.broker_ref == txid, (
                'Expected unchanged txid from Kraken AmendOrder (in-place amend)'
            )

            # 4. Query modified order — should still be open
            modified_status = processor.query_order_sync(txid, live_adapter_real)
            assert modified_status.status == BrokerOrderStatus.PENDING, (
                f'Expected PENDING for modified order, got: {modified_status.status}'
            )

            # 5. Cancel
            cancel_response = processor.cancel_order_sync(txid, live_adapter_real)
            assert cancel_response.status == BrokerOrderStatus.CANCELLED, (
                f'Expected CANCELLED, got: {cancel_response.status}'
            )
            txid = None  # prevent double-cancel in finally

        finally:
            if txid is not None:
                processor.cancel_order_sync(txid, live_adapter_real)

    def test_stop_order_lifecycle(self, live_adapter_real, processor):
        """
        STOP lifecycle: place → read the RESTING order back → amend the trigger → cancel.

        The one thing a `validate=true` probe cannot answer. Validate has Kraken parse and
        describe an order without creating it, so it proves the submit mapping and nothing
        about what a RESTING stop looks like on the way back — and the way back is what
        boot adoption and the reconciler consume. Two questions only a real resting order
        settles: whether Kraken reports the trigger where our parser reads it, and whether
        AmendOrder's `trigger_price` moves the trigger rather than the limit (#500).

        A BUY stop rests ABOVE the market, so the trigger goes far above it — the mirror of
        the limit case, which rests far below. Nothing fills and no fee is paid.

        The SIZE is chosen the other way round from the limit test. A buy stop's cost is
        lots x trigger, so a HIGH trigger clears Kraken's ~$5 order minimum with a tiny
        lot: 0.001 ETH at $8000 is $8 reserved, against $10 for the limit case. A SELL stop
        would be cheaper still in quote terms but needs the base asset HELD — measured
        2026-09-07, that is exactly what `EOrder:Insufficient funds` came back for.
        """
        txid = None
        try:
            response = processor.submit_open_order(
                symbol='ETHUSD',
                direction=OrderDirection.LONG,
                lots=0.001,
                order_type=OrderType.STOP,
                adapter=live_adapter_real,
                stop_price=8000.0,
            )
            assert response.status == BrokerOrderStatus.PENDING, (
                f'Kraken refused a standalone STOP: {response.status}'
                f' — {response.rejection_reason}'
            )
            txid = response.broker_ref

            # The read-back, which is the point of running this with real money.
            resting = [o for o in live_adapter_real.get_broker_orders()
                       if o.broker_ref == txid]
            assert resting, f'The venue does not report our resting stop {txid}'
            order = resting[0]
            assert order.order_type == OrderType.STOP, (
                f'A resting stop read back as {order.order_type} — the parser maps '
                f"Kraken's ordertype wrongly, which is how a trigger becomes a limit")
            assert order.stop_price is not None, (
                'The trigger came back empty. Our parser reads it from descr.price; if '
                'Kraken reports it elsewhere for a resting order, adoption rebuilds this '
                'order at no price at all')
            assert abs(order.stop_price - 8000.0) < 1.0, (
                f'Expected the trigger we sent, got {order.stop_price} — a mismatch here '
                f'means price and price2 are swapped somewhere')
            assert order.price is None, (
                f'A plain stop has no limit price; got {order.price}, which is the '
                f'trigger leaking into the field the whole codebase reads as a fill price')

            # AmendOrder must move the TRIGGER, not the limit.
            amended = processor.modify_order_sync(
                broker_ref=txid,
                symbol='ETHUSD',
                order_type=OrderType.STOP,
                new_price=8500.0,
                new_stop_loss=None,
                new_take_profit=None,
                adapter=live_adapter_real,
            )
            assert amended.status == BrokerOrderStatus.PENDING, (
                f'Amending a stop trigger was refused: {amended.rejection_reason}')
            txid = amended.broker_ref or txid

            after = [o for o in live_adapter_real.get_broker_orders()
                     if o.broker_ref == txid]
            assert after, f'The amended stop {txid} is no longer reported'
            assert abs(after[0].stop_price - 8500.0) < 1.0, (
                f'The amend did not move the trigger: {after[0].stop_price}. If the '
                f'trigger is unchanged the new price went to limit_price instead')

            cancel_response = processor.cancel_order_sync(txid, live_adapter_real)
            assert cancel_response.status == BrokerOrderStatus.CANCELLED, (
                f'Expected CANCELLED, got: {cancel_response.status}')
            txid = None

        finally:
            if txid is not None:
                processor.cancel_order_sync(txid, live_adapter_real)
