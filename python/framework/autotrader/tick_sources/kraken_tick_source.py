"""
FiniexTestingIDE - Kraken Tick Source
Live tick stream from Kraken WebSocket v2, trade channel plus the quote channel.

Threading model 8.a: asyncio.run() in daemon thread, TickData pushed
to queue.Queue consumed by the synchronous main algo thread.

Data consistency: both sides now read the same two channels. The collector has stamped
every trade with the quote it executed against since format 1.6.0; this source does the
same (#520 step B), so an archived tick and a live one describe the same thing. The
second subscription is switchable per profile and a failure of it is DEGRADED rather
than fatal — trades keep flowing without a quote, and the quote age says so by growing.

A reconnect leaves a record (#609): each outage — how it was detected, how long it lasted, the
public trade ids before and after it — and the refused trades go to the session's
`io/tick_stream_events.jsonl`, so the comparison of a run can name a gap on the live side.
"""

import asyncio
import json
import queue
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, List, Optional
from urllib.parse import urlparse

import certifi
import websockets
from websockets.exceptions import ConnectionClosed
from websockets.frames import Close

from python.framework.autotrader.tick_sources.abstract_tick_source import AbstractTickSource
from python.framework.autotrader.tick_sources.kraken_tick_message_parser import (
    KrakenTickMessageParser,
)
from python.framework.autotrader.tick_sources.tick_stream_recorder import TickStreamRecorder
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.autotrader_types.autotrader_display_types import (
    QuoteFeedState,
    QuoteFeedStats,
)
from python.framework.types.autotrader_types.tick_stream_event_types import TickStreamLossCause
from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.types.market_types.market_data_types import TickData
from python.framework.utils.connection_ladder import ConnectionLadder

# What makes Kraken push a ticker update. The API default is "trades", which ties the quote to
# the trade stream: the cache then only refreshes when someone trades, and a trade tick reads a
# quote as old as the gap since the last one. "bbo" pushes whenever the best bid or offer moves,
# which is what the quote on a trade tick is supposed to describe. Measured on this side
# 2026-09-16 over 25 s of BTC/USD: 202 ticker updates against 10 trade frames, median gap 14 ms,
# and the quote a trade executed against was a median 214 ms old (max 777 ms).
TICKER_EVENT_TRIGGER = 'bbo'

# The close code the websockets keepalive sends when a ping goes unanswered ("keepalive ping
# timeout", 1011 INTERNAL_ERROR). Seen as sent by us with nothing received, it names a ping timeout.
KEEPALIVE_CLOSE_CODE = 1011


class KrakenTickSource(AbstractTickSource):
    """
    Live tick source connecting to Kraken WS v2 trade channel.

    Runs asyncio.run(_ws_loop()) in a daemon thread (Threading model 8.a).
    Pushes TickData to queue.Queue for the main algo thread.

    Features:
    - Endless reconnect with exponential backoff (1s -> 60s cap, jitter); the backoff starts
      over only after a connection has delivered for `backoff_reset_after_s` (#609)
    - Connection-liveness monitoring on the monotonic clock (check interval, dead threshold)
    - A record of every outage and of the refused trades in the session's io/ folder (#609)
    - SSL via certifi (cross-platform, Windows + Linux)
    - Single symbol per session

    Args:
        symbol: Internal trading symbol (e.g., 'BTCUSD')
        ws_pair: Kraken WS pair format (e.g., 'BTC/USD')
        tick_queue: Thread-safe queue for tick delivery
        ws_url: WebSocket URL
        quote_channel_enabled: Subscribe to the ticker channel as well, so each trade tick
            carries the quote it executed against (#520 step B)
        reconnect_initial_delay_s: Initial backoff delay
        reconnect_max_delay_s: Maximum backoff delay cap
        backoff_reset_after_s: How long a connection must deliver before a loss starts the
            backoff over
        connection_check_interval_s: WS connection-liveness check interval
        connection_dead_s: Silence threshold to force reconnect
        logger: ScenarioLogger instance for the connection's own lines (the global log)
        session_logger: The session's logger, for what the operator must read — a refused
            trade, a degraded quote channel, a record that cannot be written, the source's end
        record_dir: The run's io/ folder for the tick stream record; None keeps no record
        run_id: Written into the record's header
    """

    def __init__(
        self,
        symbol: str,
        ws_pair: str,
        tick_queue: queue.Queue,
        ws_url: str = 'wss://ws.kraken.com/v2',
        quote_channel_enabled: bool = True,
        reconnect_initial_delay_s: float = 1.0,
        reconnect_max_delay_s: float = 60.0,
        backoff_reset_after_s: float = 10.0,
        connection_check_interval_s: float = 1.0,
        connection_dead_s: float = 10.0,
        logger: Optional[ScenarioLogger] = None,
        session_logger: Optional[ScenarioLogger] = None,
        record_dir: Optional[Path] = None,
        run_id: str = '',
    ):
        self._symbol = symbol
        self._ws_pair = ws_pair
        self._tick_queue = tick_queue
        self._ws_url = ws_url
        self._quote_channel_enabled = quote_channel_enabled
        self._reconnect_initial_delay_s = reconnect_initial_delay_s
        self._reconnect_max_delay_s = reconnect_max_delay_s
        # #473 — the shared ladder. Budget 0 keeps this connection's "never give up":
        # a dead tick socket is not a reason to end a session, it is a reason to keep
        # asking. Jitter is what it did not have.
        self._ladder = ConnectionLadder(
            name='broker_ticks',
            policy=ConnectionPolicy(
                initial_delay_s=reconnect_initial_delay_s,
                max_delay_s=reconnect_max_delay_s,
                attempt_budget=0,
            ),
            logger=logger,
        )
        self._backoff_reset_after_s = backoff_reset_after_s
        self._connection_check_interval_s = connection_check_interval_s
        self._connection_dead_s = connection_dead_s
        self._logger = logger
        self._session_logger = session_logger

        self._running = False
        # The record of this stream's outages and refused trades (#609) — written on the socket
        # thread only, and finished there when the loop ends.
        self._recorder = TickStreamRecorder(
            symbol=symbol, record_dir=record_dir, run_id=run_id, session_logger=session_logger)
        self._parser = KrakenTickMessageParser(symbol=symbol, recorder=self._recorder)
        self._ssl_context: Optional[ssl.SSLContext] = None
        # Created inside the socket thread's event loop; stop() sets the event through that loop,
        # which wakes a backoff wait, a connect and a subscription at once.
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event: Optional[asyncio.Event] = None
        # Set by the silence check before it aborts a connection, read when the pass is classified.
        self._silence_detected = False
        # Trade frames that arrived before their subscription was confirmed: used once it stands,
        # dropped with it when it fails — the record never counts a trade the session did not use.
        self._held_trade_frames: List[str] = []

        # Stats
        self._ticks_emitted: int = 0
        self._last_message_time: Optional[datetime] = None
        self._last_tick_time: Optional[datetime] = None
        # Both on the monotonic clock and set at every connect, so a new connection is never
        # judged by the silence of the one before it. Silence and delivery are durations, and a
        # duration taken from two wall-clock readings can be stepped negative by the clock.
        self._last_message_monotonic: Optional[float] = None
        self._subscribed_monotonic: float = 0.0
        # Quote-channel condition as the display reports it. Set on the socket thread when a
        # subscription succeeds or is refused, read on the display thread — one value, so a
        # reader sees the old value or the new one and never a mixture.
        self._quote_state = (QuoteFeedState.WAITING if quote_channel_enabled
                             else QuoteFeedState.OFF)

    # === AbstractTickSource interface ===

    def start(self) -> None:
        """
        Start the WebSocket loop. Blocks in tick source thread.

        Runs asyncio.run(_ws_loop()) which connects, subscribes, and
        receives trades until stop() is called. Sends None sentinel
        on exit (normal or error).
        """
        self._running = True
        if self._logger:
            self._logger.info(
                f'📡 KrakenTickSource starting: {self._symbol} '
                f'({self._ws_pair}) -> {self._ws_url}'
            )
        try:
            asyncio.run(self._ws_loop())
        except Exception as e:
            # The session ends with its tick source: the operator reads why in the session log.
            logger = self._session_logger or self._logger
            if logger:
                logger.error(
                    f'📡 KrakenTickSource fatal error: {e} — the tick stream has ended')
        finally:
            try:
                # Normally already finished where the loop ended; this covers a loop that never
                # started. Closing twice writes nothing twice.
                self._recorder.close()
            finally:
                # Ensure sentinel is sent even on unexpected exit — nothing raised above may
                # cost the tick loop its end-of-stream signal
                try:
                    self._tick_queue.put_nowait(None)
                except queue.Full:
                    pass

    def stop(self) -> None:
        """
        Signal the tick source to stop. Thread-safe.

        Called from the main thread while start() runs in the
        tick source thread. Wakes the async loop wherever it waits —
        a backoff pause of up to a minute included — so it ends at once.
        """
        self._running = False
        event_loop, stop_event = self._event_loop, self._stop_event
        if event_loop is None or stop_event is None:
            return
        try:
            event_loop.call_soon_threadsafe(stop_event.set)
        except RuntimeError:
            # The loop has already ended; there is nothing left to wake.
            pass

    def get_symbol(self) -> str:
        """Return the symbol this tick source produces."""
        return self._symbol

    def is_exhausted(self) -> bool:
        """
        Live source is never exhausted (endless reconnect).

        Returns:
            Always False
        """
        return False

    # === Stats getters ===

    def get_ticks_emitted(self) -> int:
        """
        Return number of ticks pushed to queue.

        Returns:
            Total ticks emitted
        """
        return self._ticks_emitted

    def get_reconnect_count(self) -> int:
        """
        Outages this stream came back from — a trade arrived after each (#609).

        Not the attempts: a far side that accepts and drops connections makes many attempts of
        one outage, and the session's "reconnected" line and the algo's market-data status would
        otherwise count every one of them.

        Returns:
            Outages ended since the session started
        """
        return self._recorder.get_outages_ended()

    def get_message_age_s(self) -> Optional[float]:
        """
        Seconds since the last message, on the monotonic clock. GIL-safe for the display thread.

        Returns:
            The age, or None before the first connection
        """
        last = self._last_message_monotonic
        if last is None:
            return None
        return time.monotonic() - last

    def get_connection_dead_s(self) -> Optional[float]:
        """
        The silence threshold this source acts on.

        Returns:
            Seconds without a message before the connection is declared dead
        """
        return self._connection_dead_s

    def get_last_message_time(self) -> Optional[datetime]:
        """
        Last WebSocket message time (GIL-safe read for display thread).

        Returns:
            Last message datetime (UTC) or None if no messages yet
        """
        return self._last_message_time

    def get_last_tick_time(self) -> Optional[datetime]:
        """
        Last actual trade tick time (excludes heartbeats). GIL-safe.

        Returns:
            Last trade tick datetime (UTC) or None if no ticks yet
        """
        return self._last_tick_time

    def get_quote_stats(self) -> QuoteFeedStats:
        """
        Quote-channel condition for the CONNECTION panel (#520 step B). GIL-safe.

        Built from ONE read of the parser's held quote, so the spread and the age describe the
        same moment. The age is a DURATION and therefore comes off the monotonic clock (§9) —
        a wall-clock difference can be stepped negative by NTP, and an impossible age reads as
        a venue fault rather than a clock fault.

        Returns:
            A consistent snapshot; state OFF when the channel is not subscribed
        """
        quote = self._parser.get_last_quote()
        if quote is None:
            return QuoteFeedStats(
                state=self._quote_state,
                quotes_received=self._parser.get_quotes_received(),
            )

        age_ms = int((time.monotonic() - quote.observed_monotonic_s) * 1000)
        return QuoteFeedStats(
            state=self._quote_state,
            bid=quote.bid,
            ask=quote.ask,
            quote_age_ms=age_ms,
            quotes_received=self._parser.get_quotes_received(),
        )

    # === Async internals ===

    async def _ws_loop(self) -> None:
        """
        Main WebSocket loop with reconnection.

        Each pass connects and subscribes, then receives while the silence check watches. A
        connection that ends is classified and recorded, and the wait before the next attempt
        comes from the shared ladder — which climbs until a connection has delivered for
        `backoff_reset_after_s`, so a far side that accepts and closes at once is not dialled
        again every second (#609). stop() wakes every wait. Runs until stop() is called.
        """
        self._stop_event = asyncio.Event()
        self._event_loop = asyncio.get_running_loop()
        try:
            await self._reconnect_until_stopped()
        finally:
            # Finished here, the moment the loop ends: asyncio.run's own cleanup may still wait
            # minutes for a name lookup it cannot cancel, and the session reads its log long
            # before that.
            self._recorder.close()

    async def _reconnect_until_stopped(self) -> None:
        """
        The reconnect loop itself: one pass per connection, until stop().
        """
        # TLS for a wss:// address — judged by the parsed scheme, the same reading the
        # configuration's check makes; ws:// is admitted only to the local machine.
        self._ssl_context = (self._create_ssl_context() if needs_tls(self._ws_url) else None)
        attempt = 0

        while self._running:
            ws = await self._open_connection()
            if ws is not None:
                cause = await self._stream(ws)
                if cause is None:
                    break
                delivered_for_s = max(
                    self._last_message_monotonic - self._subscribed_monotonic, 0.0)
                self._recorder.on_connection_lost(cause, delivered_for_s)
                attempt = self._ladder.attempt_after_loss(
                    attempt, delivered_for_s, self._backoff_reset_after_s)
                if self._logger:
                    self._logger.warning(
                        f'📡 WS connection lost ({cause.value}) after delivering for '
                        f'{delivered_for_s:.1f}s')
            if not self._running:
                break

            delay = self._ladder.next_delay(attempt + 1)
            if self._logger:
                self._logger.info(
                    f'📡 Reconnecting in {delay:.1f}s '
                    f'(attempt {attempt + 1}, outages so far: {self.get_reconnect_count()})'
                )
            if await self._stopped_within(delay):
                break
            attempt += 1

    async def _open_connection(self):
        """
        One connection attempt — connect and subscribe — unless the source is stopped first.

        Returns:
            The subscribed connection, or None when the attempt failed or the source stopped
        """
        self._recorder.on_attempt()
        try:
            ws = await self._unless_stopped(self._connect_and_subscribe())
        except ConnectionClosed as e:
            if self._logger:
                self._logger.warning(f'📡 WS connection closed during the handshake: {e}')
            return None
        except asyncio.TimeoutError:
            if self._logger:
                self._logger.warning('📡 WS connection/subscription timed out')
            return None
        except Exception as e:
            if self._logger:
                self._logger.error(f'📡 WS error: {e}')
            return None
        if ws is not None:
            self._recorder.on_restored()
        return ws

    async def _connect_and_subscribe(self):
        """
        Connect to WebSocket and subscribe to the configured channels.

        The quote channel goes FIRST, so its snapshot has arrived before the first trade can:
        measured 2026-09-16, the ticker snapshot follows its own ack by about 5 ms, and a trade
        that beats it simply carries no quote rather than a wrong one.

        Returns:
            Connected and subscribed WebSocket connection

        Raises:
            Exception: On connection or trade-subscription failure
        """
        ws = await websockets.connect(
            self._ws_url,
            ssl=self._ssl_context,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
        )
        self._last_message_time = datetime.now(timezone.utc)
        self._last_message_monotonic = time.monotonic()
        self._held_trade_frames = []

        if self._logger:
            self._logger.info(f'📡 WebSocket connected to {self._ws_url}')

        try:
            await self._subscribe_channels(ws)
        except BaseException:
            # A refused or timed-out subscription would otherwise leave the socket open — one
            # more connection held at the venue's front for every failed attempt (#609). Trades
            # it carried before the refusal were never used, so they are not recorded either.
            self._held_trade_frames = []
            self._abort(ws)
            raise

        # Delivery and silence are measured from the subscription on.
        self._subscribed_monotonic = time.monotonic()
        self._last_message_monotonic = self._subscribed_monotonic
        return ws

    async def _stream(self, ws) -> Optional[TickStreamLossCause]:
        """
        Receive on a subscribed connection until it ends, the silence check ends it, or stop().

        Args:
            ws: The subscribed connection

        Returns:
            How the connection was lost, or None when the source was stopped
        """
        self._silence_detected = False
        # Trades that came ahead of their subscription's confirmation, used now that it stands.
        held, self._held_trade_frames = self._held_trade_frames, []
        for raw in held:
            self._emit(self._parser.parse_message(raw))
        receive_task = asyncio.create_task(self._receive_loop(ws))
        monitor_task = asyncio.create_task(self._connection_monitor(ws))
        stop_task = asyncio.create_task(self._stop_event.wait())

        done, pending = await asyncio.wait(
            [receive_task, monitor_task, stop_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        # Whatever ended the pass, the transport goes now: a close handshake with a dead far
        # side would cost its timeout out of the silence budget, and stop() waits for nothing.
        self._abort(ws)

        if stop_task in done or not self._running:
            return None
        error = None
        if receive_task.done() and not receive_task.cancelled():
            error = receive_task.exception()
        if error is not None and not isinstance(error, ConnectionClosed) and self._logger:
            self._logger.error(f'📡 WS error: {error}')
        close_rcvd, close_sent = _close_frames(ws, error)
        return classify_connection_end(self._silence_detected, error, close_rcvd, close_sent)

    async def _subscribe_channels(self, ws) -> None:
        """
        Request this session's channels on an already-connected socket.

        Separated from the connect above so it can be exercised without a socket: the handshake
        is where the two-subscription logic lives, and it is the half worth pinning.

        Args:
            ws: Connected WebSocket

        Raises:
            ValueError: The venue refused the trade subscription
            asyncio.TimeoutError: No trade confirmation within the deadline
        """
        if self._quote_channel_enabled:
            await self._subscribe_quote_channel(ws)

        await self._subscribe_trade_channel(ws)

    async def _subscribe_trade_channel(self, ws) -> None:
        """
        Subscribe to the trade channel — the session's reason to exist.

        A failure here is fatal for this connection attempt: without trades there is no tick
        stream at all, so it raises and the outer loop reconnects.

        Args:
            ws: Connected WebSocket

        Raises:
            ValueError: The venue refused the subscription
            asyncio.TimeoutError: No confirmation within the deadline
        """
        # No `snapshot` parameter, deliberately. Measured 2026-10-12 with a read-only public probe:
        # the trade channel then sends no trade snapshot — the acknowledgement answers
        # 'snapshot': False and only live trades follow. So the first bar a session renders holds
        # the trades from its first tick on, exactly as a backtest's first bar does (#588);
        # everything before comes from the warmup history.
        await ws.send(json.dumps({
            'method': 'subscribe',
            'params': {
                'channel': 'trade',
                'symbol': [self._ws_pair],
            }
        }))

        error = await self._await_subscription_ack(ws, 'trade')
        if error is not None:
            raise ValueError(
                f"Subscription failed for {self._ws_pair}: {error}. "
                f"Pair must match Kraken WS v2 format (e.g., 'BTC/USD'). "
                f"Check base_currency / quote_currency in broker config."
            )

        if self._logger:
            self._logger.info(f'📡 Subscribed to trade channel: {self._ws_pair}')

    async def _subscribe_quote_channel(self, ws) -> None:
        """
        Subscribe to the ticker channel, so each trade tick carries the quote it executed
        against (#520 step B).

        A failure here is DEGRADED, never terminal (§43): trades still flow, they simply lose
        their quote, and the age on the display grows to say so. A data-quality improvement must
        not become a new reason a bot refuses to start — so this swallows the refusal and logs
        it to the SESSION logger, where the §35 error pot picks it up.

        Args:
            ws: Connected WebSocket
        """
        try:
            await ws.send(json.dumps({
                'method': 'subscribe',
                'params': {
                    'channel': 'ticker',
                    'symbol': [self._ws_pair],
                    'event_trigger': TICKER_EVENT_TRIGGER,
                }
            }))
            error = await self._await_subscription_ack(ws, 'ticker')
        except asyncio.TimeoutError:
            error = 'no confirmation within the subscription deadline'

        if error is not None:
            self._quote_state = QuoteFeedState.DEGRADED
            # On a RECONNECT the parser may still hold the quote from before the drop. Keeping
            # it would stamp every tick with a spread we can no longer refresh, and the tick
            # carries no age for a reader to catch it with.
            self._parser.forget_quote()
            logger = self._session_logger or self._logger
            if logger:
                logger.warning(
                    f'📡 Quote channel unavailable for {self._ws_pair}: {error}. '
                    f'Trades continue WITHOUT a quote — bid and ask carry the trade price, '
                    f'and every valuation reading mid is one-sided until it returns.'
                )
            return

        self._quote_state = QuoteFeedState.WAITING
        if self._logger:
            self._logger.info(
                f'📡 Subscribed to ticker channel: {self._ws_pair} '
                f'(event_trigger={TICKER_EVENT_TRIGGER})'
            )

    async def _await_subscription_ack(self, ws, channel: str) -> Optional[str]:
        """
        Wait for one channel's subscription acknowledgement.

        Kraken names the channel in the ack, which is what lets two subscriptions share one
        connection: without that match the first ack would satisfy both waits. Messages that
        are neither — the status frame after connect, a heartbeat, or the other channel's data
        already flowing — are skipped.

        Args:
            ws: Connected WebSocket
            channel: Channel name the ack must name

        Returns:
            None when confirmed, the venue's error text when refused

        Raises:
            asyncio.TimeoutError: No answer for this channel within the deadline
        """
        deadline = asyncio.get_event_loop().time() + 10.0
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError(
                    f'Subscription confirmation timeout: {channel}'
                )
            response = await asyncio.wait_for(ws.recv(), timeout=remaining)

            if self._parser.is_subscription_confirmation(response, channel=channel):
                return None

            error = self._parser.is_error_message(response)
            if error:
                return error

            # A trade ahead of its own ack is HELD: used once the subscription stands, dropped
            # with it if it fails, so the outage record never counts as seen a trade the session
            # did not use (#609).
            if self._parser.is_trade_message(response):
                self._held_trade_frames.append(response)
                continue
            # Status, heartbeat, the other channel's ack or its data. Routed rather than
            # dropped, because the ticker snapshot arrives about 5 ms after its own ack and
            # therefore lands in the middle of THIS wait — feeding it here is what makes the
            # quote warm at the first trade instead of one update later.
            self._parser.parse_message(response)
            if self._logger:
                self._logger.debug(
                    f'📡 Skipping message while awaiting {channel} ack: {response[:120]}'
                )

    async def _receive_loop(self, ws) -> None:
        """
        Receive and process messages until disconnect or stop.

        One parse per frame: the parser routes by channel, turning trade messages into TickData
        and ticker messages into the quote those ticks are stamped with. Heartbeats, status
        frames and acks update the last-message stamps and produce nothing.

        Args:
            ws: Active WebSocket connection
        """
        async for message in ws:
            if not self._running:
                break

            self._last_message_time = datetime.now(timezone.utc)
            self._last_message_monotonic = time.monotonic()

            self._emit(self._parser.parse_message(message))

            if (self._quote_state == QuoteFeedState.WAITING
                    and self._parser.get_last_quote() is not None):
                self._quote_state = QuoteFeedState.LIVE

    def _emit(self, ticks: Optional[List[TickData]]) -> None:
        """
        Hand parsed ticks to the tick loop.

        Args:
            ticks: What one frame yielded, or None
        """
        if not ticks:
            return
        for tick in ticks:
            self._tick_queue.put(tick)
            self._ticks_emitted += 1
        self._last_tick_time = datetime.now(timezone.utc)

    async def _connection_monitor(self, ws) -> None:
        """
        Monitor connection health via message timing.

        Every check interval it measures the silence since the last message on the monotonic
        clock. Past connection_dead_s it marks the pass as ended by silence and aborts the
        connection, which ends the receive loop. Kraken sends a heartbeat about once a second on
        every connection — measured 2026-10-20 by `probe_kraken_ws_silence.py`, the longest
        healthy pause 1.15 s — so a silence past the threshold is a dead connection, not a
        quiet market.

        Args:
            ws: Active WebSocket connection
        """
        while self._running:
            await asyncio.sleep(self._connection_check_interval_s)

            silence = time.monotonic() - self._last_message_monotonic
            if silence > self._connection_dead_s:
                if self._logger:
                    self._logger.warning(
                        f'📡 No messages for {silence:.1f}s '
                        f'(threshold: {self._connection_dead_s:.0f}s), '
                        f'forcing reconnect'
                    )
                self._silence_detected = True
                self._abort(ws)
                return

    async def _unless_stopped(self, awaitable: Awaitable):
        """
        Await something unless stop() comes first, which cancels it.

        Args:
            awaitable: What to wait for

        Returns:
            Its result, or None when the source was stopped first
        """
        task = asyncio.ensure_future(awaitable)
        stopper = asyncio.ensure_future(self._stop_event.wait())
        done, _ = await asyncio.wait([task, stopper], return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            stopper.cancel()
            await asyncio.gather(stopper, return_exceptions=True)
            return task.result()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return None

    async def _stopped_within(self, delay_s: float) -> bool:
        """
        Wait out a backoff delay, or less when stop() comes first.

        Args:
            delay_s: The delay the ladder gave

        Returns:
            True when the source was stopped during the wait
        """
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay_s)
            return True
        except asyncio.TimeoutError:
            return False

    @staticmethod
    def _abort(ws) -> None:
        """
        Drop a connection at once, without a close handshake.

        Args:
            ws: The connection; a stand-in without a transport is left alone
        """
        transport = getattr(ws, 'transport', None)
        if transport is not None:
            transport.abort()

    def _create_ssl_context(self) -> ssl.SSLContext:
        """
        Create SSL context using certifi certificates.

        Cross-platform: works on Linux (Docker) and Windows (server).

        Returns:
            Configured SSL context
        """
        return ssl.create_default_context(cafile=certifi.where())



def needs_tls(ws_url: str) -> bool:
    """
    Whether an address asks for an encrypted connection, read off its parsed scheme.

    Parsed rather than prefix-matched: the scheme is case-insensitive, and a `WSS://` address the
    configuration accepts must not reach the library without TLS, which refuses it on every attempt.

    Args:
        ws_url: The configured address

    Returns:
        True for a wss:// address
    """
    return urlparse(ws_url).scheme == 'wss'


def classify_connection_end(
    silence_detected: bool,
    error: Optional[BaseException],
    close_rcvd: Optional[Close],
    close_sent: Optional[Close],
) -> TickStreamLossCause:
    """
    How a subscribed connection was lost, judged in a fixed order (#609).

    Our own silence check comes first: it aborts the connection, and what the library reports
    afterwards describes the abort, not the cause. A clean close by the venue raises nothing at
    all — the receive loop simply ends — which is why the close frames are read off the
    connection when there is no exception to read them from.

    Args:
        silence_detected: The silence check ended the pass
        error: What the receive loop raised, or None when it ended without an exception
        close_rcvd: The close frame the venue sent, or None
        close_sent: The close frame our side sent, or None

    Returns:
        The cause the outage record names
    """
    if silence_detected:
        return TickStreamLossCause.SILENCE
    if error is not None and not isinstance(error, ConnectionClosed):
        return TickStreamLossCause.ERROR
    if close_rcvd is None and close_sent is not None and close_sent.code == KEEPALIVE_CLOSE_CODE:
        return TickStreamLossCause.PING_TIMEOUT
    if close_rcvd is not None:
        return TickStreamLossCause.CLOSED_BY_VENUE
    return TickStreamLossCause.CONNECTION_ERROR


def _close_frames(ws, error: Optional[BaseException]):
    """
    The close frames of a connection that has ended.

    Args:
        ws: The connection
        error: What its receive loop raised, or None

    Returns:
        (received, sent) — read off the exception when it carries them, else off the
        connection's protocol state; None where there was none
    """
    if isinstance(error, ConnectionClosed):
        return error.rcvd, error.sent
    protocol = getattr(ws, 'protocol', None)
    return getattr(protocol, 'close_rcvd', None), getattr(protocol, 'close_sent', None)
