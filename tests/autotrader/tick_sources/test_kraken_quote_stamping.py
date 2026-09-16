"""
Kraken tick parser — the quote a trade executed against (#520 step B).

The path these tests cover feeds a real-money session and had no offline coverage at all
before this suite: `KrakenTickSource` and its parser appeared nowhere under tests/.

Every frame comes from `tests/fixtures/tick_sources/kraken_ws_frames.json`, recorded verbatim
from the live venue rather than hand-written, so what is pinned is what Kraken actually sends.
"""

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from python.framework.autotrader.tick_sources.kraken_tick_message_parser import (
    KrakenTickMessageParser,
)

FIXTURE_PATH = Path('tests/fixtures/tick_sources/kraken_ws_frames.json')
SYMBOL = 'BTCUSD'


@pytest.fixture(scope='module')
def frames() -> Dict[str, Any]:
    """
    The recorded Kraken frames.

    Module-scoped: the file is read once for the whole suite rather than per test — on this
    tree a file open is the expensive part, not the parsing.

    Returns:
        Mapping of frame name to the decoded frame
    """
    return json.loads(FIXTURE_PATH.read_text())


@pytest.fixture
def parser() -> KrakenTickMessageParser:
    """
    A parser with an empty quote, as it is at the start of a session.

    Returns:
        Fresh KrakenTickMessageParser
    """
    return KrakenTickMessageParser(symbol=SYMBOL)


def raw(frames: Dict[str, Any], name: str, **overrides: Any) -> str:
    """
    One recorded frame as the wire string, optionally with its first data entry adjusted.

    Args:
        frames: The loaded fixture
        name: Frame key
        overrides: Fields to replace in `data[0]`

    Returns:
        JSON string as it would arrive on the WebSocket
    """
    frame = json.loads(json.dumps(frames[name]))
    if overrides:
        frame['data'][0].update(overrides)
    return json.dumps(frame)


# =============================================================================
# THE QUOTE A TRADE EXECUTED AGAINST
# =============================================================================


def test_trade_before_any_quote_falls_back_to_the_trade_price(parser, frames):
    """A trade arriving before any quote keeps the shipped behaviour: bid = ask = price."""
    ticks = parser.parse_message(raw(frames, 'trade_buy'))

    assert ticks is not None and len(ticks) == 1
    tick = ticks[0]
    traded = frames['trade_buy']['data'][0]['price']

    assert tick.bid == traded
    assert tick.ask == traded
    assert tick.last == traded


def test_no_quote_means_no_age_and_specifically_not_zero(parser, frames):
    """
    Age is None before the first quote, never 0.

    A zero asserts a quote observed in that same millisecond, which is a measurement nobody
    made — the distinction the collector's format carries and the one a reader depends on.
    """
    parser.parse_message(raw(frames, 'trade_buy'))

    assert parser.get_last_quote() is None
    assert parser.get_quotes_received() == 0


def test_trade_after_a_quote_carries_that_quote(parser, frames):
    """Once a quote has been seen, a trade tick states it on both sides."""
    parser.parse_message(raw(frames, 'ticker_update'))
    ticks = parser.parse_message(raw(frames, 'trade_buy'))

    quoted = frames['ticker_update']['data'][0]
    tick = ticks[0]

    assert tick.bid == quoted['bid']
    assert tick.ask == quoted['ask']
    assert tick.bid != tick.ask


def test_the_traded_price_survives_the_quote(parser, frames):
    """
    `last` stays the traded price while bid/ask become the quote — the whole point of step A.

    This is the strategy plane's guarantee: a worker reads `tick.price`, so it sees the same
    number before and after this feature. Only `mid` moves.
    """
    traded = frames['trade_buy']['data'][0]['price']

    before = parser.parse_message(raw(frames, 'trade_buy'))[0]
    parser.parse_message(raw(frames, 'ticker_update'))
    after = parser.parse_message(raw(frames, 'trade_buy'))[0]

    assert before.price == traded
    assert after.price == traded
    assert after.last == traded
    # What DID change is the valuation basis.
    assert before.mid == traded
    assert after.mid != traded


def test_buy_prints_at_the_ask_and_sell_at_the_bid(parser, frames):
    """
    The taker-side convention, measured live 2026-09-16 at 14 of 14 (burst fills excluded).

    It is what makes a spread reconstructable for archived data that has none, so it is pinned
    rather than assumed.
    """
    parser.parse_message(raw(frames, 'ticker_update'))
    quoted = frames['ticker_update']['data'][0]

    buy = parser.parse_message(
        raw(frames, 'trade_buy', side='buy', price=quoted['ask'])
    )[0]
    sell = parser.parse_message(
        raw(frames, 'trade_sell', side='sell', price=quoted['bid'])
    )[0]

    assert buy.last == buy.ask
    assert sell.last == sell.bid


def test_every_trade_in_a_burst_carries_the_same_quote(parser, frames):
    """
    A burst is one order sweeping the book: several fills, one quote, one age.

    Their prices walk away from the top of book — that is depth consumed, not spread paid, and
    the parser must not "correct" it.
    """
    parser.parse_message(raw(frames, 'ticker_update'))
    ticks = parser.parse_message(raw(frames, 'trade_burst'))

    assert len(ticks) > 1
    assert len({(t.bid, t.ask) for t in ticks}) == 1


# =============================================================================
# WHAT A QUOTE MUST NOT BECOME
# =============================================================================


def test_ticker_updates_produce_no_ticks(parser, frames):
    """
    The ticker channel feeds the quote and writes nothing of its own.

    Its time base is our local receipt while a trade's is the exchange's event time, and
    interleaving the two steps time backwards on nearly every channel change.
    """
    assert parser.parse_message(raw(frames, 'ticker_snapshot')) is None
    assert parser.parse_message(raw(frames, 'ticker_update')) is None
    assert parser.get_last_quote() is not None


def test_a_snapshot_fills_the_quote_exactly_like_an_update(parser, frames):
    """The first ticker frame after a subscribe is a snapshot; it must count."""
    parser.parse_message(raw(frames, 'ticker_snapshot'))

    quoted = frames['ticker_snapshot']['data'][0]
    quote = parser.get_last_quote()

    assert quote.bid == quoted['bid']
    assert quote.ask == quoted['ask']
    assert parser.get_quotes_received() == 1


@pytest.mark.parametrize(
    'label,overrides',
    [
        ('crossed', {'bid': 75_100.0, 'ask': 75_000.0}),
        ('zero_bid', {'bid': 0.0, 'ask': 75_000.0}),
        ('zero_ask', {'bid': 75_000.0, 'ask': 0.0}),
        ('negative', {'bid': -1.0, 'ask': 75_000.0}),
    ],
)
def test_an_impossible_quote_is_dropped_and_the_previous_one_survives(
    parser, frames, label, overrides
):
    """
    An impossible quote is refused rather than stored.

    It would be written onto a trade tick as fact, and the import pipeline rejects a file whose
    ask sits below its bid. Keeping the previous one lets it age visibly instead of failing
    invisibly — measured live: 0 crossed and 0 equal quotes in 202, so this is a guard rather
    than a routine event.
    """
    parser.parse_message(raw(frames, 'ticker_update'))
    good = parser.get_last_quote()

    parser.parse_message(raw(frames, 'ticker_update', **overrides))

    assert parser.get_last_quote() == good, f'{label} quote was stored'
    assert parser.get_quotes_received() == 1


def test_a_quote_is_never_discarded_for_being_old(parser, frames):
    """
    A quiet ticker channel does not stop trades: they keep the last quote and it keeps ageing.

    This is the degraded state the design chose deliberately — a data-quality feature must not
    become a reason ticks stop flowing.
    """
    parser.parse_message(raw(frames, 'ticker_update'))
    quoted = frames['ticker_update']['data'][0]

    for _ in range(50):
        tick = parser.parse_message(raw(frames, 'trade_buy'))[0]
        assert tick.bid == quoted['bid']
        assert tick.ask == quoted['ask']


# =============================================================================
# EVERYTHING THAT IS NOT MARKET DATA
# =============================================================================


@pytest.mark.parametrize('name', ['heartbeat', 'status', 'subscribe_ack_ticker'])
def test_non_market_frames_produce_nothing(parser, frames, name):
    """Heartbeats, status frames and acks yield no ticks and touch no quote."""
    assert parser.parse_message(json.dumps(frames[name])) is None
    assert parser.get_last_quote() is None


@pytest.mark.parametrize(
    'malformed',
    ['', 'not json at all', '[]', '{}', '{"channel": "trade", "type": "update"}'],
)
def test_malformed_frames_do_not_raise(parser, malformed):
    """A frame we cannot read is skipped; it never ends the session."""
    assert parser.parse_message(malformed) is None


def test_a_trade_without_a_usable_price_is_skipped(parser, frames):
    """A non-positive price is not a price — the tick is dropped rather than emitted at zero."""
    assert parser.parse_message(raw(frames, 'trade_buy', price=0)) is None


# =============================================================================
# TELLING TWO SUBSCRIPTIONS APART
# =============================================================================


def test_an_ack_only_satisfies_its_own_channel(parser, frames):
    """
    Two subscriptions share one connection, so an ack must be matched by channel.

    Without it the ticker ack would satisfy the wait for the trade ack, and a refused trade
    subscription would look like a successful one.
    """
    ticker_ack = json.dumps(frames['subscribe_ack_ticker'])
    trade_ack = json.dumps(frames['subscribe_ack_trade'])

    assert parser.is_subscription_confirmation(ticker_ack, channel='ticker')
    assert not parser.is_subscription_confirmation(ticker_ack, channel='trade')
    assert parser.is_subscription_confirmation(trade_ack, channel='trade')
    assert not parser.is_subscription_confirmation(trade_ack, channel='ticker')


def test_an_ack_without_a_channel_requirement_still_matches(parser, frames):
    """The channel argument is optional; omitting it accepts any successful ack."""
    assert parser.is_subscription_confirmation(json.dumps(frames['subscribe_ack_trade']))


def test_an_error_frame_reports_its_text(parser):
    """A refusal is handed back as the venue's own words, not flattened to a bool."""
    refusal = json.dumps({
        'method': 'subscribe',
        'success': False,
        'error': 'Currency pair not supported ETH/USDD',
    })

    assert parser.is_error_message(refusal) == 'Currency pair not supported ETH/USDD'
    assert not parser.is_subscription_confirmation(refusal, channel='ticker')
