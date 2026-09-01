"""
Kraken Adapter — Client Order ID on the Wire (#473)

A submit whose answer was lost cannot be asked about with the venue's own reference,
because that reference is exactly what did not arrive. The key has to be one WE chose.

Two properties matter and both are cheap to get wrong:

  it must FIT      — Kraken allows 18 ASCII characters, and a key one character too long
                     is refused at runtime, inside a live session, on a real order
  it must not COLLIDE across restarts — the internal counter restarts at 1 with the
                     process, so without a session discriminator a brand-new order would
                     carry the key of one still resting at the venue from last night,
                     and boot adoption (#355) would match the wrong order

No network here: `_build_submit_payload` and `_parse_openorders_response` are pure.
"""

import pytest

from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderType
from python.framework.utils.run_id_utils import (
    build_client_order_id,
    mint_run_id,
    parse_client_order_id,
    session_key_from_run_id,
)

_KRAKEN_CONFIG = 'configs/brokers/kraken/kraken_spot_broker_config.json'
_CL_ORD_ID_MAX_LEN = 18


@pytest.fixture
def adapter() -> KrakenAdapter:
    """Tier-1 adapter — no credentials, no network."""
    import json
    with open(_KRAKEN_CONFIG, encoding='utf-8') as handle:
        return KrakenAdapter(json.load(handle))


class TestOnTheWire:
    """_build_submit_payload puts the key where Kraken reads it."""

    def test_key_lands_in_cl_ord_id(self, adapter):
        payload = adapter._build_submit_payload(
            symbol='BTCUSD',
            direction=OrderDirection.LONG,
            lots=0.001,
            order_type=OrderType.MARKET,
            client_order_id='p1641_47',
        )
        assert payload['cl_ord_id'] == 'p1641_47'

    def test_absent_key_sends_no_field(self, adapter):
        # The mock and dry-run paths never reach a venue and need no key. Sending an
        # empty one would be a value the venue has to interpret.
        payload = adapter._build_submit_payload(
            symbol='BTCUSD',
            direction=OrderDirection.LONG,
            lots=0.001,
            order_type=OrderType.MARKET,
            client_order_id=None,
        )
        assert 'cl_ord_id' not in payload

    def test_key_is_truncated_to_the_venue_limit(self, adapter):
        payload = adapter._build_submit_payload(
            symbol='BTCUSD',
            direction=OrderDirection.LONG,
            lots=0.001,
            order_type=OrderType.MARKET,
            client_order_id='p1641_' + 'x' * 40,
        )
        assert len(payload['cl_ord_id']) <= _CL_ORD_ID_MAX_LEN


class TestReadBack:
    """_parse_openorders_response returns our key, so a resting order can be attributed."""

    def test_key_survives_the_round_trip(self, adapter):
        parsed = adapter._parse_openorders_response({'open': {
            'TX-1': {
                'status': 'open',
                'vol': '0.001',
                'cl_ord_id': 'p1641_47',
                'descr': {'pair': 'XBTUSD', 'type': 'buy', 'ordertype': 'limit',
                          'price': '50000.0'},
            }}})
        assert parsed[0].client_order_id == 'p1641_47'
        assert parsed[0].broker_ref == 'TX-1'

    def test_foreign_order_reports_no_key(self, adapter):
        # An order somebody placed by hand in the venue's own UI carries no key of ours.
        # That absence is the FACT that tells it apart from one we placed — #349 turns it
        # into an EXTERNAL order rather than a ghost.
        parsed = adapter._parse_openorders_response({'open': {
            'TX-2': {
                'status': 'open',
                'vol': '0.001',
                'descr': {'pair': 'XBTUSD', 'type': 'sell', 'ordertype': 'limit',
                          'price': '60000.0'},
            }}})
        assert parsed[0].client_order_id is None
        assert parsed[0].status is BrokerOrderStatus.PENDING


class TestSessionKey:
    """The discriminator that makes the restart-colliding counter safe."""

    def test_derived_from_the_run_id(self):
        assert session_key_from_run_id('20260831_110757_164176c0') == '1641'

    def test_two_sessions_do_not_collide_on_the_same_counter(self):
        from datetime import datetime, timezone
        now = datetime(2026, 8, 31, 3, 14, tzinfo=timezone.utc)
        keys = {session_key_from_run_id(mint_run_id(now)) for _ in range(50)}
        # Same second, same counter reset — only the run id's random half separates them.
        assert len(keys) > 1

    def test_wire_key_fits_the_venue_limit(self):
        from datetime import datetime, timezone

        key = session_key_from_run_id(
            mint_run_id(datetime(2026, 8, 31, 3, 14, tzinfo=timezone.utc)))
        # Longest realistic shape: a six-character symbol and a five-digit counter.
        wire = build_client_order_id(key, 'pos_btcusd_99999')
        assert len(wire) <= _CL_ORD_ID_MAX_LEN

    def test_empty_session_key_means_no_wire_key(self):
        assert session_key_from_run_id('no_random_half_here_') == ''


class TestKeyRoundTrip:
    """
    build / parse are one format, and #355 depends on the pair agreeing.

    The builder writes the key onto the wire; the parser reads it back off a truth pull to
    decide whose order a resting one is. If the two ever disagree, an order of ours reads
    as a stranger's — which is the one classification that must not be wrong.
    """

    def test_a_built_key_parses_back(self):
        wire = build_client_order_id('1641', 'pos_btcusd_47')
        assert wire == 'p1641_47'
        assert parse_client_order_id(wire) == ('1641', '47')

    def test_no_session_key_means_no_wire_key(self):
        assert build_client_order_id('', 'pos_btcusd_47') is None

    def test_the_counter_survives_a_symbol_of_any_length(self):
        # Only the trailing counter goes on the wire — the readable id stays in our books.
        assert build_client_order_id('1641', 'pos_x_9') == 'p1641_9'
        assert build_client_order_id('1641', 'pos_verylongsymbol_9') == 'p1641_9'

    @pytest.mark.parametrize('foreign', [
        None,
        '',
        'bot-7-entry',       # another client's own scheme
        'p164_47',           # discriminator too short
        'p16411_47',         # discriminator too long
        'p1641_x',           # counter not a number
        'p1641_',            # counter missing
        '1641_47',           # prefix missing
    ])
    def test_a_key_that_is_not_ours_is_refused(self, foreign):
        # Client order ids are free-format at the venue, so a loose parse would claim
        # another client's order as one of ours.
        assert parse_client_order_id(foreign) is None
