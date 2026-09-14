"""
Live Executor — Every Write Carries an Identity We Can Ask About (#487)

A write whose answer is lost can only be resolved by asking the venue what became of it,
and the only handle that survives a lost answer is the key WE chose (#473). Two properties
have to hold for that question to have one answer:

  the key is UNIQUE per write   — an entry and its close used to send the same one, because
                                  the close derived its key from the position id. Measured
                                  2026-09-13 against Kraken: `ClosedOrders` for one key
                                  answered with TWO orders, so a lookup by key could not
                                  name an order at all
  the key is RECORDED, not re-derived — the reconciler rebuilt it from `pending_order_id`,
                                  which held only while every key was a function of its id.
                                  A re-derived key misses a close in flight and reports it
                                  as abandoned: the false alarm measured twice in five live
                                  field-study runs

No network: MockBrokerAdapter throughout.
"""

from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderType,
)
from python.framework.utils.run_id_utils import parse_client_order_id


class TestTheKeyIsUniquePerWrite:
    """One key, one order — the property a lookup by key depends on."""

    def test_a_close_does_not_reuse_its_entry_s_key(self, mock_instant, executor_instant):
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)
        executor_instant.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.001,
        ))
        entry_key = executor_instant._request_processor.get_pending_orders()[0].client_order_id
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)
        position_id = executor_instant.get_open_positions()[0].position_id

        executor_instant.close_position(position_id)
        close_key = executor_instant._request_processor.get_pending_orders()[0].client_order_id

        assert entry_key, 'the entry went on the wire without a key'
        assert close_key, 'the close went on the wire without a key'
        assert close_key != entry_key, (
            f'entry and close both sent {entry_key} — the venue answers a lookup by that '
            f'key with two orders, so a lost answer cannot be asked about')

    def test_two_partial_closes_send_two_different_keys(self, mock_instant, executor_instant):
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)
        executor_instant.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.004,
        ))
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)
        position_id = executor_instant.get_open_positions()[0].position_id

        executor_instant.close_position(position_id, lots=0.001)
        first = executor_instant._request_processor.get_pending_orders()[0].client_order_id
        mock_instant.feed_tick(executor_instant, bid=50100.0, ask=50102.0)
        executor_instant.close_position(position_id, lots=0.001)
        second = executor_instant._request_processor.get_pending_orders()[0].client_order_id

        assert first and second
        assert first != second, (
            f'both partial closes sent {first}; Kraken refused exactly this once already '
            f'with EGeneral:Invalid arguments:cl_ord_id not unique')

    def test_the_close_key_is_still_one_of_ours(self, mock_instant, executor_instant):
        """
        Its shape is unchanged — the counter moved, the grammar did not.

        `parse_client_order_id` is deliberately strict (exact session width, digits only)
        so a foreign key is never claimed as ours, and a close that stopped parsing would
        be read as somebody else's order by the reconciler and by boot adoption.
        """
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)
        executor_instant.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.001,
        ))
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)
        position_id = executor_instant.get_open_positions()[0].position_id

        executor_instant.close_position(position_id)
        close_key = executor_instant._request_processor.get_pending_orders()[0].client_order_id

        parsed = parse_client_order_id(close_key)
        assert parsed is not None, f'{close_key} no longer parses as one of our keys'
        assert parsed[0] == executor_instant.get_session_key()


class TestTheKeyIsRecorded:
    """What went on the wire is read back off the order, never recomputed."""

    def test_a_resting_order_carries_the_key_it_sent(self, mock_instant, executor_instant):
        mock_instant.feed_tick(executor_instant, symbol='BTCUSD')
        executor_instant.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.LIMIT,
            direction=OrderDirection.LONG, lots=0.001, price=40000.0,
        ))

        pending = executor_instant._active_limit_orders[0]
        assert pending.client_order_id, (
            'the order is in flight with no record of the key it was sent under — the one '
            'handle a lost answer leaves behind')

    def test_an_in_flight_close_is_not_reported_as_abandoned(
        self, mock_instant, executor_instant
    ):
        """
        The regression C1 would otherwise have introduced.

        `get_in_flight_client_keys` is what keeps the reconciler from calling an order we
        have sent and not yet heard about "placed and forgotten". While it re-derived the
        key from the order id, a close — whose key is no longer a function of that id —
        fell straight through into the abandoned bucket.
        """
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)
        executor_instant.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.001,
        ))
        mock_instant.feed_tick(executor_instant, bid=49999.0, ask=50001.0)
        position_id = executor_instant.get_open_positions()[0].position_id

        executor_instant.close_position(position_id)
        pending = executor_instant._request_processor.get_pending_orders()[0]

        assert pending.client_order_id in executor_instant.get_in_flight_client_keys(), (
            'the venue already has this close and reports it under a key the reconciler '
            'could not match — calling that "placed and forgotten" is a false alarm')
