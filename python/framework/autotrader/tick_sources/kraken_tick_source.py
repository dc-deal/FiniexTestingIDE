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
"""

import asyncio
import json
import queue
import ssl
import time
from datetime import datetime, timezone
from typing import Optional

import certifi
import websockets
from websockets.exceptions import (
    ConnectionClosed,
    ConnectionClosedError,
    ConnectionClosedOK,
)

from python.framework.autotrader.tick_sources.abstract_tick_source import AbstractTickSource
from python.framework.autotrader.tick_sources.kraken_tick_message_parser import (
    KrakenTickMessageParser,
)
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.autotrader_types.autotrader_display_types import QuoteFeedStats
from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.utils.connection_ladder import ConnectionLadder

# What makes Kraken push a ticker update. The API default is "trades", which ties the quote to
# the trade stream: the cache then only refreshes when someone trades, and a trade tick reads a
# quote as old as the gap since the last one. "bbo" pushes whenever the best bid or offer moves,
# which is what the quote on a trade tick is supposed to describe. Measured on this side
# 2026-09-16 over 25 s of BTC/USD: 202 ticker updates against 10 trade frames, median gap 14 ms,
# and the quote a trade executed against was a median 214 ms old (max 777 ms).
TICKER_EVENT_TRIGGER = 'bbo'


class KrakenTickSource(AbstractTickSource):
    """
    Live tick source connecting to Kraken WS v2 trade channel.

    Runs asyncio.run(_ws_loop()) in a daemon thread (Threading model 8.a).
    Pushes TickData to queue.Queue for the main algo thread.

    Features:
    - Endless reconnect with exponential backoff (1s -> 60s cap)
    - Connection-liveness monitoring (configurable interval, dead threshold)
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
        connection_check_interval_s: WS connection-liveness check interval
        connection_dead_s: Silence threshold to force reconnect
        logger: ScenarioLogger instance
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
        connection_check_interval_s: float = 30.0,
        connection_dead_s: float = 90.0,
        logger: Optional[ScenarioLogger] = None,
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
        self._connection_check_interval_s = connection_check_interval_s
        self._connection_dead_s = connection_dead_s
        self._logger = logger

        self._running = False
        self._parser = KrakenTickMessageParser(symbol=symbol)
        self._ssl_context: Optional[ssl.SSLContext] = None

        # Stats
        self._ticks_emitted: int = 0
        self._reconnect_count: int = 0
        self._last_message_time: Optional[datetime] = None
        self._last_tick_time: Optional[datetime] = None
        # Quote-channel condition as the display reports it. Set on the socket thread when a
        # subscription succeeds or is refused, read on the display thread — one string, so a
        # reader sees the old value or the new one and never a mixture.
        self._quote_state: str = 'off' if not quote_channel_enabled else 'waiting'

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
            if self._logger:
                self._logger.error(f'📡 KrakenTickSource fatal error: {e}')
        finally:
            # Ensure sentinel is sent even on unexpected exit
            try:
                self._tick_queue.put_nowait(None)
            except queue.Full:
                pass

    def stop(self) -> None:
        """
        Signal the tick source to stop. Thread-safe.

        Called from the main thread while start() runs in the
        tick source thread. The async loop checks _running and
        exits gracefully.
        """
        self._running = False

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
        Return number of reconnection attempts.

        Returns:
            Total reconnect count
        """
        return self._reconnect_count

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
            A consistent snapshot; state 'off' when the channel is not subscribed
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

        Outer loop: connect -> subscribe -> receive/connection-monitor concurrent tasks.
        On disconnect/error: log, backoff, reconnect.
        Runs until self._running is False.
        """
        self._ssl_context = self._create_ssl_context()
        reconnect_attempt = 0

        while self._running:
            try:
                ws = await self._connect_and_subscribe()
                reconnect_attempt = 0

                # Run receive + connection-monitor concurrently
                receive_task = asyncio.create_task(self._receive_loop(ws))
                connection_monitor_task = asyncio.create_task(self._connection_monitor(ws))

                done, pending = await asyncio.wait(
                    [receive_task, connection_monitor_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                # Cancel the other task
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

                # Check if receive_loop raised an exception
                for task in done:
                    if task.exception() is not None:
                        raise task.exception()

            except (ConnectionClosed, ConnectionClosedError, ConnectionClosedOK) as e:
                if self._logger:
                    self._logger.warning(f'📡 WS connection closed: {e}')

            except asyncio.TimeoutError:
                if self._logger:
                    self._logger.warning('📡 WS connection/subscription timed out')

            except Exception as e:
                if self._logger:
                    self._logger.error(f'📡 WS error: {e}')

            # Reconnect with backoff (if still running)
            if self._running:
                delay = self._get_reconnect_delay(reconnect_attempt)
                self._reconnect_count += 1
                if self._logger:
                    self._logger.info(
                        f'📡 Reconnecting in {delay:.1f}s '
                        f'(attempt {reconnect_attempt + 1}, '
                        f'total reconnects: {self._reconnect_count})'
                    )
                await asyncio.sleep(delay)
                reconnect_attempt += 1

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

        if self._logger:
            self._logger.info(f'📡 WebSocket connected to {self._ws_url}')

        await self._subscribe_channels(ws)

        return ws

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
            self._quote_state = 'degraded'
            # On a RECONNECT the parser may still hold the quote from before the drop. Keeping
            # it would stamp every tick with a spread we can no longer refresh, and the tick
            # carries no age for a reader to catch it with.
            self._parser.forget_quote()
            if self._logger:
                self._logger.warning(
                    f'📡 Quote channel unavailable for {self._ws_pair}: {error}. '
                    f'Trades continue WITHOUT a quote — bid and ask carry the trade price, '
                    f'and every valuation reading mid is one-sided until it returns.'
                )
            return

        self._quote_state = 'waiting'
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

            # Status, heartbeat, the other channel's ack or its data. Routed rather than
            # dropped, because the ticker snapshot arrives about 5 ms after its own ack and
            # therefore lands in the middle of THIS wait — feeding it here is what makes the
            # quote warm at the first trade instead of one update later. No trade can arrive
            # yet: that channel is subscribed after this returns.
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
        frames and acks update _last_message_time and produce nothing.

        Args:
            ws: Active WebSocket connection
        """
        async for message in ws:
            if not self._running:
                break

            self._last_message_time = datetime.now(timezone.utc)

            ticks = self._parser.parse_message(message)
            if ticks:
                for tick in ticks:
                    self._tick_queue.put(tick)
                    self._ticks_emitted += 1
                self._last_tick_time = datetime.now(timezone.utc)

            if self._quote_state == 'waiting' and self._parser.get_last_quote() is not None:
                self._quote_state = 'live'

    async def _connection_monitor(self, ws) -> None:
        """
        Monitor connection health via message timing.

        Checks _last_message_time periodically. If silence exceeds
        connection_dead_s, closes the WebSocket to trigger reconnect
        in _ws_loop.

        Args:
            ws: Active WebSocket connection
        """
        while self._running:
            await asyncio.sleep(self._connection_check_interval_s)

            if not self._last_message_time:
                continue

            silence = (
                datetime.now(timezone.utc) - self._last_message_time
            ).total_seconds()

            if silence > self._connection_dead_s:
                if self._logger:
                    self._logger.warning(
                        f'📡 No messages for {silence:.0f}s '
                        f'(threshold: {self._connection_dead_s:.0f}s), '
                        f'forcing reconnect'
                    )
                await ws.close()
                break

            elif silence > self._connection_check_interval_s * 2:
                if self._logger:
                    self._logger.warning(
                        f'📡 No messages for {silence:.0f}s, '
                        f'connection may be stale'
                    )

    def _get_reconnect_delay(self, attempt: int) -> float:
        """
        Delay before the next reconnect attempt, from the shared ladder (#473).

        Gains jitter, which this connection did not have: a fleet of clients returning in
        lockstep after a venue-side blip is a self-inflicted thundering herd, and the
        venue rate-limits the same endpoint the orders go through.

        Args:
            attempt: Current reconnect attempt number (0-based)

        Returns:
            Delay in seconds
        """
        return self._ladder.next_delay(attempt + 1)

    def _create_ssl_context(self) -> ssl.SSLContext:
        """
        Create SSL context using certifi certificates.

        Cross-platform: works on Linux (Docker) and Windows (server).

        Returns:
            Configured SSL context
        """
        return ssl.create_default_context(cafile=certifi.where())
