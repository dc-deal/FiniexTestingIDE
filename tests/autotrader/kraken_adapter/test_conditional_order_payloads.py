"""
Kraken Adapter — Conditional Order Payloads (#500)

Kraken accepts a standalone stop order, and its two price fields mean different things
depending on the order type:

  limit            price = the limit price
  stop-loss        price = the TRIGGER
  stop-loss-limit  price = the TRIGGER, price2 = the limit

There is no `stopprice` REQUEST parameter — that name exists only in Kraken's responses
(OpenOrders / QueryOrders), and sending it would be silently ignored while the order went
out without a trigger. Getting the two the wrong way round places a real order at a level
nobody chose, which is why the mapping is pinned here, offline and in the daily suite: the
release-gate suite runs too rarely to be the only guard.

Two Kraken behaviours have no analogue anywhere else in this project and are pinned too.
A signed or percentage-suffixed price is read as an OFFSET from the last trade rather than
refused, so a negative number is a valid order at an unintended price. And AmendOrder has
a separate `trigger_price` — everything used to go into `limit_price`, so amending a
resting stop's trigger, which is what a trailing stop does on every ratchet, would have
moved the price it FILLS at and left the trigger standing.

No network here: the payload builders are pure.
"""

import json
from datetime import datetime, timezone

import pytest

from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderType

_KRAKEN_CONFIG = 'configs/brokers/kraken/kraken_spot_broker_config.json'


@pytest.fixture
def adapter() -> KrakenAdapter:
    """Tier-1 adapter — no credentials, no network."""
    with open(_KRAKEN_CONFIG, encoding='utf-8') as handle:
        return KrakenAdapter(json.load(handle))


class TestTheTriggerGoesWhereKrakenReadsIt:
    """The submit mapping this issue exists for."""

    def test_a_stop_sends_its_trigger_as_price(self, adapter):
        payload = adapter.build_submit_payload(
            symbol='BTCUSD', direction=OrderDirection.LONG, lots=0.01,
            order_type=OrderType.STOP, stop_price=49500.0)

        assert payload['ordertype'] == 'stop-loss'
        assert payload['price'] == '49500.0'
        assert 'price2' not in payload, 'A plain stop has no limit price'
        assert 'stopprice' not in payload, 'Not a request parameter — responses only'

    def test_a_stop_limit_sends_trigger_and_limit_in_that_order(self, adapter):
        payload = adapter.build_submit_payload(
            symbol='BTCUSD', direction=OrderDirection.LONG, lots=0.01,
            order_type=OrderType.STOP_LIMIT, stop_price=49500.0, limit_price=49600.0)

        assert payload['ordertype'] == 'stop-loss-limit'
        assert payload['price'] == '49500.0', 'price is the TRIGGER, not the limit'
        assert payload['price2'] == '49600.0'

    def test_a_limit_still_sends_its_limit_as_price(self, adapter):
        """
        The pre-existing mapping, so the new branch cannot have displaced it.

        The kwarg is `limit_price` for a LIMIT as well as for a STOP_LIMIT — one name per
        price in both pipelines (#500). It was `price` on the live path only, which is why
        the reconciler's own comparison found nothing for a live limit order.
        """
        payload = adapter.build_submit_payload(
            symbol='BTCUSD', direction=OrderDirection.LONG, lots=0.01,
            order_type=OrderType.LIMIT, limit_price=49000.0)

        assert payload['ordertype'] == 'limit'
        assert payload['price'] == '49000.0'


class TestASignedPriceNeverReachesTheVenue:
    """
    Kraken's relative-price syntax turns a defect on our side into a valid order.

    `price` and `price2` accept a leading +, - or # and a trailing % to mean an amount
    relative to the last traded price. So a negative float does not come back as an API
    error; it comes back as a fill. The refusal has to be ours.
    """

    def test_a_negative_trigger_is_refused(self, adapter):
        with pytest.raises(ValueError) as refused:
            adapter.build_submit_payload(
                symbol='BTCUSD', direction=OrderDirection.LONG, lots=0.01,
                order_type=OrderType.STOP, stop_price=-49500.0)

        assert 'offset' in str(refused.value), (
            'The message must say WHY a negative number is dangerous here')

    def test_a_zero_limit_is_refused(self, adapter):
        with pytest.raises(ValueError):
            adapter.build_submit_payload(
                symbol='BTCUSD', direction=OrderDirection.LONG, lots=0.01,
                order_type=OrderType.STOP_LIMIT, stop_price=49500.0, limit_price=0.0)

    def test_an_omitted_price_is_omitted_rather_than_refused(self, adapter):
        """
        None means "no such field", which is not the same as a bad value.

        The executor's own gate refuses a conditional order with no trigger; this layer
        must not turn a MARKET order's absent price into an error.
        """
        payload = adapter.build_submit_payload(
            symbol='BTCUSD', direction=OrderDirection.LONG, lots=0.01,
            order_type=OrderType.MARKET)

        assert 'price' not in payload


class TestAmendingAStopMovesItsTrigger:
    """AmendOrder's two price fields, which used to be collapsed into one."""

    def test_a_stop_amend_targets_the_trigger(self, adapter):
        payload = adapter.build_modify_payload(
            broker_ref='OABC-123', symbol='BTCUSD', order_type=OrderType.STOP,
            new_price=49800.0)

        assert payload['trigger_price'] == '49800.0'
        assert 'limit_price' not in payload

    def test_a_stop_limit_amend_carries_both(self, adapter):
        """
        The limit half used to be applied locally and never sent.

        So the venue kept the old limit while our shadow showed the new one — a divergence
        we authored ourselves, on the very path the reconciler then reports.
        """
        payload = adapter.build_modify_payload(
            broker_ref='OABC-123', symbol='BTCUSD', order_type=OrderType.STOP_LIMIT,
            new_price=49800.0, new_limit_price=49900.0)

        assert payload['trigger_price'] == '49800.0'
        assert payload['limit_price'] == '49900.0'

    def test_a_limit_amend_still_targets_the_limit(self, adapter):
        payload = adapter.build_modify_payload(
            broker_ref='OABC-123', symbol='BTCUSD', order_type=OrderType.LIMIT,
            new_price=49800.0)

        assert payload['limit_price'] == '49800.0'
        assert 'trigger_price' not in payload


class TestTheVenueDecidesWhoProvidedLiquidity:
    """
    Kraken's QueryTrades carries a `maker` boolean per fill, and it used to be ignored.

    Inferring from the ordertype is wrong in a way invisible from our side: a
    `stop-loss-limit` whose limit crosses the book the moment it triggers is a TAKER, and
    every one of them was booked as a maker. Our synthetic fee derives maker-ness the same
    way, so a fee-drift audit (#327) compared a wrong estimate against a wrongly-parsed
    truth and saw agreement.
    """

    def test_the_declared_flag_wins_over_the_ordertype_guess(self, adapter):
        """A stop-limit the venue calls a taker is a taker, whatever its type suggests."""
        assert adapter._maker_from_trade(
            {'ordertype': 'stop-loss-limit', 'maker': False}) is False
        assert adapter._maker_from_trade({'ordertype': 'market', 'maker': True}) is True

    def test_without_a_flag_the_ordertype_is_the_fallback(self, adapter):
        """A payload carrying no flag still has to be classified — as a guess, knowingly."""
        assert adapter._maker_from_trade({'ordertype': 'limit'}) is True
        assert adapter._maker_from_trade({'ordertype': 'stop-loss-limit'}) is True
        assert adapter._maker_from_trade({'ordertype': 'market'}) is False
        assert adapter._maker_from_trade({'ordertype': 'stop-loss'}) is False

    def test_a_non_boolean_flag_is_not_trusted(self, adapter):
        """
        A string 'false' is truthy in Python, and that is how a parser silently inverts.

        The check is on the TYPE, not on truthiness, so a venue that ever sent the field
        as a string falls back to the guess instead of booking every taker as a maker.
        """
        assert adapter._maker_from_trade(
            {'ordertype': 'market', 'maker': 'false'}) is False


class TestTheReadSideKeepsTheTwoPricesApart:
    """
    What comes back has the same ambiguity, and it decides what a boot adopts.

    `descr.price` is Kraken's PRIMARY price — the limit of a limit order, the trigger of a
    stop. It used to be written into BrokerOrder.price for every type, which the adopter
    reads as a resting limit price and the reconciler compares against ours.
    """

    def _open_orders(self, ordertype: str, price: str, price2: str = '0') -> dict:
        """One resting order in Kraken's OpenOrders shape. Returns: the raw result dict."""
        return {'open': {'OABC-123': {
            'status': 'open',
            'vol': '0.01',
            'vol_exec': '0',
            'descr': {
                'pair': 'XBTUSD', 'type': 'buy', 'ordertype': ordertype,
                'price': price, 'price2': price2,
            },
        }}}

    def test_a_resting_stop_reports_its_trigger_as_a_trigger(self, adapter):
        parsed = adapter._parse_openorders_response(
            self._open_orders('stop-loss', '49500.0'))

        assert len(parsed) == 1
        assert parsed[0].order_type == OrderType.STOP
        assert parsed[0].stop_price == 49500.0
        assert parsed[0].price is None, 'A plain stop has no limit price to report'

    def test_a_resting_stop_limit_reports_both(self, adapter):
        parsed = adapter._parse_openorders_response(
            self._open_orders('stop-loss-limit', '49500.0', '49600.0'))

        assert parsed[0].order_type == OrderType.STOP_LIMIT
        assert parsed[0].stop_price == 49500.0
        assert parsed[0].price == 49600.0

    def test_a_resting_limit_is_unchanged(self, adapter):
        parsed = adapter._parse_openorders_response(
            self._open_orders('limit', '49000.0'))

        assert parsed[0].order_type == OrderType.LIMIT
        assert parsed[0].price == 49000.0
        assert parsed[0].stop_price is None

    def test_a_trailing_stop_reports_no_price_at_all(self, adapter):
        """
        Kraken reports trailing offsets in the same fields, and an offset is not a level.

        Read as an absolute price it is wrong by the whole distance to the market, and it
        looks entirely plausible — the one failure mode worth a branch of its own.
        """
        parsed = adapter._parse_openorders_response(
            self._open_orders('trailing-stop', '-100.0'))

        assert parsed[0].order_type == OrderType.TRAILING_STOP
        assert parsed[0].price is None
        assert parsed[0].stop_price is None

    def test_an_unnameable_type_is_reported_not_dropped_and_not_called_a_limit(self, adapter):
        """
        Both halves matter, and they pull in opposite directions.

        Dropping the row hides the order from the exclusive-account check, which asks
        whether a stranger is working our symbol — the one order most worth seeing. Calling
        it a LIMIT hands a number of unknown meaning to everything that reads a limit price.
        """
        parsed = adapter._parse_openorders_response(
            self._open_orders('settle-position', '49000.0'))

        assert len(parsed) == 1, 'The venue reported it, so we report it'
        assert parsed[0].order_type == OrderType.UNKNOWN
        assert parsed[0].price is None
        assert parsed[0].stop_price is None
        assert parsed[0].broker_ref == 'OABC-123'

    def test_a_missing_ordertype_is_unnameable_too(self, adapter):
        """It used to default to 'limit', which is the same lie with nothing to name."""
        raw = self._open_orders('limit', '49000.0')
        del raw['open']['OABC-123']['descr']['ordertype']

        parsed = adapter._parse_openorders_response(raw)

        assert parsed[0].order_type == OrderType.UNKNOWN
        assert parsed[0].price is None


class TestAnAnswerThatNamesNoOrderIsNotAState:
    """
    A QueryOrders result that does not mention the txid used to become PENDING.

    Measured 2026-09-08 against the live API: asking Kraken about a txid it never minted
    returns an empty result. The parse then read a status off the absent entry and defaulted
    it to 'pending', so "the venue has never heard of this order" and "the order is resting"
    arrived as the same answer. They are opposite facts, and only one of them is safe to act
    on: a bot that reads the first as the second starts believing in a protection that does
    not exist — which is precisely what #503's boot resolver has to decide.

    An unmapped status WORD is the same mistake with a different cause: we cannot name the
    state, so we must not name one.
    """

    _TS = datetime(2026, 9, 8, 8, 33, tzinfo=timezone.utc)

    def test_an_empty_answer_is_unknown_not_pending(self, adapter):
        response = adapter.parse_query_response({}, 'OZZZZZ-ZZZZZ-ZZZZZZ', self._TS)

        assert response.status == BrokerOrderStatus.UNKNOWN
        assert response.is_unknown

    def test_an_answer_about_a_different_order_is_unknown_too(self, adapter):
        raw = {'OOTHER-11111-222222': {'status': 'open', 'vol_exec': '0.0'}}

        response = adapter.parse_query_response(raw, 'OZZZZZ-ZZZZZ-ZZZZZZ', self._TS)

        assert response.status == BrokerOrderStatus.UNKNOWN

    def test_unknown_is_not_terminal_so_nothing_gets_booked_off_it(self, adapter):
        """
        Terminal would be worse than the PENDING it replaces: it would invent a cancel or an
        expiry for an order the venue did not describe.
        """
        response = adapter.parse_query_response({}, 'OZZZZZ-ZZZZZ-ZZZZZZ', self._TS)

        assert not response.is_terminal
        assert not response.is_filled
        assert response.filled_lots is None

    def test_a_status_word_we_do_not_map_is_unknown_rather_than_pending(self, adapter):
        raw = {'OABC-123': {'status': 'something-kraken-invented', 'vol_exec': '0.0'}}

        response = adapter.parse_query_response(raw, 'OABC-123', self._TS)

        assert response.status == BrokerOrderStatus.UNKNOWN

    def test_a_described_order_still_parses_as_before(self, adapter):
        """The guard must not cost the ordinary answer its reading."""
        raw = {'OABC-123': {'status': 'closed', 'vol_exec': '0.002', 'price': '2467.94'}}

        response = adapter.parse_query_response(raw, 'OABC-123', self._TS)

        assert response.status == BrokerOrderStatus.FILLED
        assert response.filled_lots == pytest.approx(0.002)
        assert response.fill_price == pytest.approx(2467.94)
