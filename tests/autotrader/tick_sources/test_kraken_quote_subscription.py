"""
Kraken tick source — two subscriptions on one connection (#520 step B).

What the parser suite cannot reach: which channels are actually requested, what happens when
the venue refuses one of them, and what the CONNECTION panel is told about it.

The socket is faked rather than opened. The frames it hands back are the recorded ones, so the
handshake is driven by acknowledgements Kraken really sent — including the `result.channel`
field that is the only thing letting two subscriptions be told apart.
"""

import asyncio
import json
from collections import deque
from pathlib import Path
from typing import Any, Dict, List

import pytest

from python.framework.autotrader.tick_sources.kraken_tick_source import (
    TICKER_EVENT_TRIGGER,
    KrakenTickSource,
)

FIXTURE_PATH = Path('tests/fixtures/tick_sources/kraken_ws_frames.json')
SYMBOL = 'BTCUSD'
WS_PAIR = 'BTC/USD'


@pytest.fixture(scope='module')
def frames() -> Dict[str, Any]:
    """
    The recorded Kraken frames.

    Returns:
        Mapping of frame name to the decoded frame
    """
    return json.loads(FIXTURE_PATH.read_text())


class RecordingLogger:
    """
    A logger that keeps what it was told, so a test can assert the operator was informed.

    §35: a degraded feed has to reach the session channel, because 'gave up' and 'still
    trying' look identical from outside otherwise.
    """

    def __init__(self) -> None:
        self.infos: List[str] = []
        self.warnings: List[str] = []
        self.errors: List[str] = []

    def info(self, message: str) -> None:
        """Record an info line."""
        self.infos.append(message)

    def warning(self, message: str) -> None:
        """Record a warning line."""
        self.warnings.append(message)

    def error(self, message: str) -> None:
        """Record an error line."""
        self.errors.append(message)

    def debug(self, message: str) -> None:
        """Debug lines are not asserted on."""


class FakeWebSocket:
    """
    A socket that hands out a scripted sequence of frames and remembers what was sent to it.

    Args:
        script: Raw frames to return from recv(), in order
    """

    def __init__(self, script: List[str]):
        self.sent: List[Dict[str, Any]] = []
        self.closed = False
        self._inbox = deque(script)

    async def send(self, message: str) -> None:
        """Record an outgoing subscription request."""
        self.sent.append(json.loads(message))

    async def recv(self) -> str:
        """
        Hand back the next scripted frame.

        An exhausted script means the venue went quiet: this waits rather than returning, so a
        deadline test measures the deadline instead of an end-of-list.
        """
        if not self._inbox:
            await asyncio.sleep(3600)
        return self._inbox.popleft()

    async def close(self) -> None:
        """Mark the socket closed."""
        self.closed = True

    def __aiter__(self):
        """Iterate the remaining scripted frames, then stop."""
        return self

    async def __anext__(self) -> str:
        if not self._inbox:
            raise StopAsyncIteration
        return self._inbox.popleft()


def build_source(logger: RecordingLogger, quote_channel_enabled: bool) -> KrakenTickSource:
    """
    A tick source wired the way setup_tick_source wires it.

    Args:
        logger: Recording logger
        quote_channel_enabled: Whether the quote channel is requested

    Returns:
        KrakenTickSource with an unbounded queue
    """
    import queue

    return KrakenTickSource(
        symbol=SYMBOL,
        ws_pair=WS_PAIR,
        tick_queue=queue.Queue(),
        quote_channel_enabled=quote_channel_enabled,
        logger=logger,
    )


def subscribed_channels(ws: FakeWebSocket) -> List[str]:
    """
    The channels this socket was asked to subscribe to, in order.

    Args:
        ws: The fake socket

    Returns:
        Channel names
    """
    return [msg['params']['channel'] for msg in ws.sent if msg.get('method') == 'subscribe']


# =============================================================================
# WHAT IS SUBSCRIBED
# =============================================================================


def test_switch_off_subscribes_only_the_trade_channel(frames):
    """
    OFF means nothing changes at all — the off-valve proof.

    It is not the default, so if it quietly stopped working nothing else in the suite would
    notice; this is the only place that says so.
    """
    logger = RecordingLogger()
    source = build_source(logger, quote_channel_enabled=False)
    ws = FakeWebSocket([json.dumps(frames['subscribe_ack_trade'])])

    asyncio.run(source._subscribe_trade_channel(ws))

    assert subscribed_channels(ws) == ['trade']
    assert source.get_quote_stats().state == 'off'
    assert source.get_quote_stats().quote_age_ms is None


def test_the_quote_channel_is_subscribed_first_and_asks_for_bbo(frames):
    """
    Ticker before trade, and with the event trigger that ties the quote to the BOOK.

    Order matters: the ticker snapshot follows its own ack by about 5 ms, so subscribing it
    first is what makes a quote available to the very first trade. The trigger matters because
    Kraken's default ties ticker updates to the trade stream, which would make the quote as
    stale as the gap since the last trade — the opposite of what it is for.
    """
    logger = RecordingLogger()
    source = build_source(logger, quote_channel_enabled=True)
    ws = FakeWebSocket([
        json.dumps(frames['subscribe_ack_ticker']),
        json.dumps(frames['subscribe_ack_trade']),
    ])

    asyncio.run(source._subscribe_channels(ws))

    assert subscribed_channels(ws) == ['ticker', 'trade']
    assert ws.sent[0]['params']['event_trigger'] == TICKER_EVENT_TRIGGER
    assert source.get_quote_stats().state == 'waiting'


def test_frames_arriving_during_the_handshake_warm_the_quote(frames):
    """
    The ticker snapshot lands mid-handshake and must not be thrown away.

    Without this the first quote is the one AFTER the snapshot, and the first trades of a
    session carry no quote for no reason.
    """
    logger = RecordingLogger()
    source = build_source(logger, quote_channel_enabled=True)
    ws = FakeWebSocket([
        json.dumps(frames['subscribe_ack_ticker']),
        json.dumps(frames['ticker_snapshot']),
        json.dumps(frames['subscribe_ack_trade']),
    ])

    asyncio.run(source._subscribe_channels(ws))

    stats = source.get_quote_stats()
    assert stats.quotes_received == 1
    assert stats.bid == frames['ticker_snapshot']['data'][0]['bid']


# =============================================================================
# WHEN A CHANNEL IS REFUSED
# =============================================================================


def test_a_refused_quote_channel_is_degraded_and_the_session_starts(frames):
    """
    The venue refusing `ticker` must not stop a bot from trading.

    A data-quality improvement that becomes a new reason to refuse a start has made things
    worse, so this swallows the refusal, marks the state, and tells the operator.
    """
    logger = RecordingLogger()
    source = build_source(logger, quote_channel_enabled=True)
    ws = FakeWebSocket([
        json.dumps({'method': 'subscribe', 'success': False, 'error': 'Subscription depth unavailable'}),
        json.dumps(frames['subscribe_ack_trade']),
    ])

    asyncio.run(source._subscribe_channels(ws))

    assert subscribed_channels(ws) == ['ticker', 'trade']
    assert source.get_quote_stats().state == 'degraded'
    assert len(logger.warnings) == 1
    assert 'Subscription depth unavailable' in logger.warnings[0]


def test_a_refusal_on_reconnect_drops_the_quote_from_before_the_drop(frames):
    """
    A degraded quote channel must not leave the previous quote standing.

    On a reconnect the parser still holds what it saw before the socket died. Keeping it would
    stamp every tick with a spread we can no longer refresh — and the tick carries no age, so
    nothing downstream could tell. Falling back to the trade price is the honest answer.
    """
    logger = RecordingLogger()
    source = build_source(logger, quote_channel_enabled=True)
    source._running = True

    # A first connection that works, so a quote is in hand.
    asyncio.run(source._receive_loop(FakeWebSocket([json.dumps(frames['ticker_update'])])))
    assert source.get_quote_stats().quote_age_ms is not None

    # The reconnect: the venue now refuses the quote channel.
    asyncio.run(source._subscribe_channels(FakeWebSocket([
        json.dumps({'method': 'subscribe', 'success': False, 'error': 'temporarily unavailable'}),
        json.dumps(frames['subscribe_ack_trade']),
    ])))

    stats = source.get_quote_stats()
    assert stats.state == 'degraded'
    assert stats.quote_age_ms is None

    # And a trade now falls back to the trade price on both sides.
    source._running = True
    asyncio.run(source._receive_loop(FakeWebSocket([json.dumps(frames['trade_buy'])])))
    traded = frames['trade_buy']['data'][0]['price']
    tick = source._tick_queue.get_nowait()
    assert tick.bid == traded and tick.ask == traded


def test_a_refused_trade_channel_still_ends_the_attempt(frames):
    """
    The trade channel is the session's reason to exist — its refusal stays fatal.

    The quote channel's tolerance must not leak onto the one that carries the ticks.
    """
    logger = RecordingLogger()
    source = build_source(logger, quote_channel_enabled=True)
    ws = FakeWebSocket([
        json.dumps(frames['subscribe_ack_ticker']),
        json.dumps({'method': 'subscribe', 'success': False, 'error': 'Currency pair not supported'}),
    ])

    with pytest.raises(ValueError, match='Currency pair not supported'):
        asyncio.run(source._subscribe_channels(ws))


def test_a_ticker_ack_does_not_satisfy_the_trade_handshake(frames):
    """
    Two acks on one connection, matched by channel.

    If the trade wait accepted any ack, this script — where the venue answers ticker twice and
    never answers trade — would look like a completed handshake.
    """
    logger = RecordingLogger()
    source = build_source(logger, quote_channel_enabled=True)
    ws = FakeWebSocket([
        json.dumps(frames['subscribe_ack_ticker']),
        json.dumps(frames['subscribe_ack_ticker']),
    ])

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(_with_short_deadline(source, ws))


async def _with_short_deadline(source: KrakenTickSource, ws: FakeWebSocket) -> None:
    """
    Run the handshake against a socket that never answers the trade subscription.

    The production deadline is ten seconds; the test does not wait for it.

    Args:
        source: The tick source under test
        ws: The fake socket
    """
    await asyncio.wait_for(source._subscribe_channels(ws), timeout=2.0)


# =============================================================================
# WHAT THE CONNECTION PANEL IS TOLD
# =============================================================================


def test_the_quote_state_turns_live_on_the_first_quote(frames):
    """'waiting' is only true until a quote arrives; the panel must not say it afterwards."""
    logger = RecordingLogger()
    source = build_source(logger, quote_channel_enabled=True)
    source._running = True
    ws = FakeWebSocket([json.dumps(frames['ticker_update'])])

    asyncio.run(source._receive_loop(ws))

    stats = source.get_quote_stats()
    assert stats.state == 'live'
    assert stats.quote_age_ms is not None
    assert stats.quote_age_ms >= 0


def test_ticks_keep_flowing_while_the_quote_channel_is_silent(frames):
    """
    A quiet ticker channel is not a session event: trades still become ticks.

    The age is what reports the condition, by growing — which is why the display shows both it
    and the spread, and why nothing here discards a quote for being old.
    """
    logger = RecordingLogger()
    source = build_source(logger, quote_channel_enabled=True)
    source._running = True
    ws = FakeWebSocket(
        [json.dumps(frames['ticker_update'])]
        + [json.dumps(frames['trade_buy'])] * 5
    )

    asyncio.run(source._receive_loop(ws))

    assert source.get_ticks_emitted() == 5
    assert source.get_quote_stats().quotes_received == 1
    assert source.get_quote_stats().state == 'live'


def test_the_reported_spread_matches_the_quote_it_came_from(frames):
    """The panel's spread and percentage are derived from one quote, not assembled from two."""
    logger = RecordingLogger()
    source = build_source(logger, quote_channel_enabled=True)
    source._running = True
    ws = FakeWebSocket([json.dumps(frames['ticker_update'])])

    asyncio.run(source._receive_loop(ws))

    quoted = frames['ticker_update']['data'][0]
    stats = source.get_quote_stats()

    assert stats.bid == quoted['bid']
    assert stats.ask == quoted['ask']
    assert stats.spread == pytest.approx(quoted['ask'] - quoted['bid'])
    assert stats.spread_pct == pytest.approx(
        (quoted['ask'] - quoted['bid']) / quoted['bid'] * 100.0
    )


def test_a_source_without_a_quote_channel_reports_nothing_to_render(frames):
    """
    A replay source has no quote feed, and the panel must say nothing rather than show an idle
    one — the same shape the signal panel uses for a mounted session.
    """
    from python.framework.autotrader.tick_sources.mock_tick_source import MockTickSource
    import queue

    source = MockTickSource(ticks=[], symbol=SYMBOL, tick_queue=queue.Queue())

    assert source.get_quote_stats() is None


# =============================================================================
# HOW THE PANEL RENDERS IT
# =============================================================================


def _format(stats):
    """
    Render one quote snapshot the way the CONNECTION panel does.

    Built without the display's collaborators — the formatter is a pure function of the
    snapshot, which is the property worth pinning.

    Args:
        stats: QuoteFeedStats or None

    Returns:
        The rendered line
    """
    from python.system.ui.autotrader_live_display import AutoTraderLiveDisplay

    display = AutoTraderLiveDisplay.__new__(AutoTraderLiveDisplay)
    return display._format_quote(stats)


def test_the_panel_distinguishes_every_quote_condition():
    """
    A narrow market and a frozen cache must not look the same on screen.

    That is the whole reason the age is shown beside the spread: an unattended run has nobody
    watching a log, and 'off', 'waiting', 'degraded' and 'stale' all render as a dash or a
    number otherwise.
    """
    from python.framework.types.autotrader_types.autotrader_display_types import QuoteFeedStats

    assert 'no quote channel' in _format(None)
    assert '(off)' in _format(QuoteFeedStats(state='off'))
    assert 'waiting' in _format(QuoteFeedStats(state='waiting'))
    assert 'degraded' in _format(QuoteFeedStats(state='degraded'))

    live = _format(QuoteFeedStats(state='live', bid=75_920.6, ask=75_920.7, quote_age_ms=214))
    assert '0.1' in live
    assert '214ms' in live

    # A stuck cache: the spread stops moving while the age climbs, and the age is coloured so
    # the operator's eye lands on it rather than on an unremarkable number beside it.
    stuck = _format(QuoteFeedStats(state='live', bid=75_920.6, ask=75_920.7, quote_age_ms=47_000))
    assert '47s' in stuck
    assert 'yellow' in stuck
