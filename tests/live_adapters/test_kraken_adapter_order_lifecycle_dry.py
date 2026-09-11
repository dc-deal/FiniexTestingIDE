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

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.logging.global_logger import GlobalLogger
from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter
from python.framework.trading_env.live.live_request_processor import LiveRequestProcessor
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus, TimeoutConfig
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderType
from tests.live_adapters.conftest import record_observed_adapter

_BROKER_CONFIG_PATH = Path('configs/brokers/kraken/kraken_spot_broker_config.json')
_CREDENTIALS_PATH = Path('user_configs/credentials/kraken_credentials.json')


@pytest.fixture(scope='module')
def live_adapter(request):
    """
    KrakenAdapter loaded from tracked broker settings with dry_run=True enforced.

    Returns:
        KrakenAdapter with Tier 3 enabled and dry_run mode active
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
    dry_run = True

    adapter = KrakenAdapter(broker_config)
    adapter.enable_live(
        credentials_file=credentials_file,
        dry_run=dry_run,
        transport=transport,
    )
    # The certificate records what was BUILT here, not what the settings file says.
    record_observed_adapter(
        request,
        phase='validate_only',
        dry_run=dry_run,
        api_base_url=transport.api_base_url)
    return adapter


@pytest.fixture(scope='module')
def processor():
    """LiveRequestProcessor used to drive the adapter's Tier-3 layers."""
    return LiveRequestProcessor(
        logger=GlobalLogger(name='LiveAdapterTest'),
        timeout_config=TimeoutConfig(order_timeout_seconds=30.0),
    )


class TestKrakenAdapterOrderLifecycle:
    """
    Phase 1: Dry-run order lifecycle validation.

    Drives the adapter's Tier-3 layers via LiveRequestProcessor.submit_open_order
    against the real Kraken API with validate=true (set inside the adapter's
    dry-run path). Kraken validates syntax, pair resolution, and margin but
    does NOT place orders. All tests skip if credentials are not available.

    Post-DryRunOrderSimulator behavior: a successful submit returns PENDING
    with a synthetic DRYRUN-* ref. What makes it FILL is no longer time alone
    (#505): the poll counter must be spent AND the market must have reached the
    order's price, and each poll has to be given the quote to compare against.
    Nothing here polls to a fill — these tests assert the submit answer only.

    Note: submit_open_order does not call validate_order() internally — invalid
    symbol and below-min-lot cases reach the API and return REJECTED.

    LIMIT order volume uses 0.1 ETH (@ $100 limit price → $10 cost) to satisfy
    Kraken's minimum order cost requirement (~$5). MARKET tests use 0.001 (volume_min).
    """

    def test_market_buy_dryrun(self, live_adapter, processor):
        """MARKET buy with validate=true — expects PENDING with synthetic DRYRUN ref."""
        response = processor.submit_open_order(
            symbol='ETHUSD',
            direction=OrderDirection.LONG,
            lots=0.001,
            order_type=OrderType.MARKET,
            adapter=live_adapter,
        )
        assert response.status == BrokerOrderStatus.PENDING
        assert response.broker_ref.startswith('DRYRUN-'), (
            f'Expected DRYRUN ref, got: {response.broker_ref}'
        )

    def test_market_sell_dryrun(self, live_adapter, processor):
        """MARKET sell with validate=true — expects PENDING with synthetic DRYRUN ref."""
        response = processor.submit_open_order(
            symbol='ETHUSD',
            direction=OrderDirection.SHORT,
            lots=0.001,
            order_type=OrderType.MARKET,
            adapter=live_adapter,
        )
        assert response.status == BrokerOrderStatus.PENDING
        assert response.broker_ref.startswith('DRYRUN-'), (
            f'Expected DRYRUN ref, got: {response.broker_ref}'
        )

    def test_limit_buy_dryrun(self, live_adapter, processor):
        """LIMIT buy far below market — 0.1 ETH @ $100 meets Kraken cost minimum (~$10)."""
        response = processor.submit_open_order(
            symbol='ETHUSD',
            direction=OrderDirection.LONG,
            lots=0.1,
            order_type=OrderType.LIMIT,
            adapter=live_adapter,
            limit_price=100.0,
        )
        assert response.status == BrokerOrderStatus.PENDING
        assert response.broker_ref.startswith('DRYRUN-'), (
            f'Expected DRYRUN ref, got: {response.broker_ref}'
        )

    def test_limit_buy_with_sltp_dryrun(self, live_adapter, processor):
        """LIMIT buy with stop_loss and take_profit kwargs — 0.1 ETH @ $100."""
        response = processor.submit_open_order(
            symbol='ETHUSD',
            direction=OrderDirection.LONG,
            lots=0.1,
            order_type=OrderType.LIMIT,
            adapter=live_adapter,
            limit_price=100.0,
            stop_loss=50.0,
            take_profit=200.0,
        )
        assert response.status == BrokerOrderStatus.PENDING
        assert response.broker_ref.startswith('DRYRUN-'), (
            f'Expected DRYRUN ref, got: {response.broker_ref}'
        )

    def test_the_venues_own_description_is_preserved(self, live_adapter, processor):
        """
        A dry run must carry back what the VENUE made of the order, not only our ref.

        `validate=true` costs nothing and Kraken answers with a `descr` block naming the
        resolved pair and the effective order type. Without it a dry-run assertion can only
        ever observe that the call did not raise — which is exactly how a level that reached
        nothing passed a test named after it.
        """
        response = processor.submit_open_order(
            symbol='ETHUSD',
            direction=OrderDirection.LONG,
            lots=0.1,
            order_type=OrderType.LIMIT,
            adapter=live_adapter,
            limit_price=100.0,
        )

        assert response.raw_response is not None, (
            'The validate answer was discarded — nothing about this order can be asserted'
        )
        assert 'descr' in response.raw_response, (
            f'Expected a descr block, got: {sorted(response.raw_response)}'
        )
        assert response.raw_response['descr'].get('order'), 'Expected an order description'

    def test_a_standalone_stop_reaches_the_venue(self, live_adapter, processor):
        """
        Kraken's own words for a stop order we placed — the mapping, proven at the venue.

        Costs nothing: `validate=true` has Kraken parse and describe the order without
        creating it. It is the only way to see whether our `price` / `price2` assignment
        means to Kraken what the documentation says it means, and the assertion is on the
        venue's rendering rather than on our payload (the offline suite pins that half).

        Deliberately loose on the wording. Kraken's AddOrder response carries only a
        rendered `descr.order` string and no `ordertype` key, and the exact phrasing is
        theirs to change — so this asserts that the description names a stop and carries
        both numbers, not that it matches a format we guessed.
        """
        response = processor.submit_open_order(
            symbol='ETHUSD',
            direction=OrderDirection.LONG,
            lots=0.1,
            order_type=OrderType.STOP_LIMIT,
            adapter=live_adapter,
            stop_price=100.0,
            limit_price=99.0,
        )

        described = (response.raw_response or {}).get('descr', {}).get('order', '')
        assert 'stop loss' in described.lower(), (
            f'Kraken did not describe this as a stop order: {described!r}'
        )
        assert '100' in described and '99' in described, (
            f'Both the trigger and the limit must appear in the venue description, '
            f'got: {described!r}'
        )

    def test_a_declared_level_deliberately_does_not_travel_on_the_entry(
        self, live_adapter, processor
    ):
        """
        The question #500 opened, answered by #503 — and answered at a different layer.

        This test was a strict `xfail` waiting for a declared `stop_loss` to appear in
        Kraken's description of the ENTRY. It could never flip, because #503 does not
        send it there: Kraken has no bracket and no OCO, so the level reaches the venue
        as a SEPARATE stop order the framework places once the entry has filled.

        So the payload's silence is now the CORRECT answer, and this pins it as such —
        an entry carrying a hidden conditional close would mean two enforcers on one
        position. What the payload cannot show is whether the level reaches the venue at
        all; that needs a filled position and a real order resting over it, which is the
        field study's `protective_level_test` phase.
        """
        response = processor.submit_open_order(
            symbol='ETHUSD',
            direction=OrderDirection.LONG,
            lots=0.1,
            order_type=OrderType.LIMIT,
            adapter=live_adapter,
            limit_price=100.0,
            stop_loss=50.0,
        )

        descr = (response.raw_response or {}).get('descr', {})
        assert not (descr.get('close') or '').strip(), (
            'The entry must carry NO conditional close. A level attached here and a '
            'protective order placed for the same position would be two enforcers on '
            f'one holding. Kraken described the close leg as: {descr.get("close")!r}'
        )
        assert 'limit' in (descr.get('order') or '').lower(), (
            f'and the entry itself is still a plain limit: {descr.get("order")!r}')

    def test_invalid_symbol_rejected(self, live_adapter, processor):
        """Unknown symbol reaches Kraken API — expects REJECTED response."""
        response = processor.submit_open_order(
            symbol='XXXUSD',
            direction=OrderDirection.LONG,
            lots=0.001,
            order_type=OrderType.MARKET,
            adapter=live_adapter,
        )
        assert response.status == BrokerOrderStatus.REJECTED, (
            f'Expected REJECTED for unknown symbol, got: {response.status}'
        )
        assert response.rejection_reason, 'Expected non-empty rejection reason'

    def test_below_minimum_lot_rejected(self, live_adapter, processor):
        """Volume below ETHUSD minimum (0.001) reaches Kraken API — expects REJECTED response."""
        response = processor.submit_open_order(
            symbol='ETHUSD',
            direction=OrderDirection.LONG,
            lots=0.00001,
            order_type=OrderType.MARKET,
            adapter=live_adapter,
        )
        assert response.status == BrokerOrderStatus.REJECTED, (
            f'Expected REJECTED for below-min volume, got: {response.status}'
        )
        assert response.rejection_reason, 'Expected non-empty rejection reason'
