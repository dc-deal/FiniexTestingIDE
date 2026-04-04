"""
FiniexTestingIDE - Kraken Tick Source
Live tick stream from Kraken WebSocket v2 trade channel.

Threading model 8.a: asyncio.run() in daemon thread, TickData pushed
to queue.Queue consumed by the synchronous main algo thread.

Data consistency: uses the same trade channel as DataCollector,
ensuring backtesting data matches live data format.
"""

import asyncio
import json
import queue
import ssl
import time
from datetime import datetime, timezone
from typing import List, Optional

import certifi
import websockets
from websockets.exceptions import (
    ConnectionClosed,
    ConnectionClosedError,
    ConnectionClosedOK,
)

from python.framework.autotrader.tick_sources.abstract_tick_source import AbstractTickSource
from python.framework.autotrader.tick_sources.kraken_tick_message_parser import KrakenTickMessageParser
from python.framework.logging.scenario_logger import ScenarioLogger


class KrakenTickSource(AbstractTickSource):
    """
    Live tick source connecting to Kraken WS v2 trade channel.

    Runs asyncio.run(_ws_loop()) in a daemon thread (Threading model 8.a).
    Pushes TickData to queue.Queue for the main algo thread.

    Features:
    - Endless reconnect with exponential backoff (1s -> 60s cap)
    - Heartbeat monitoring (configurable interval, dead threshold)
    - SSL via certifi (cross-platform, Windows + Linux)
    - Single symbol per session

    Args:
        symbol: Internal trading symbol (e.g., 'BTCUSD')
        ws_pair: Kraken WS pair format (e.g., 'BTC/USD')
        tick_queue: Thread-safe queue for tick delivery
        ws_url: WebSocket URL
        reconnect_initial_delay_s: Initial backoff delay
        reconnect_max_delay_s: Maximum backoff delay cap
        heartbeat_interval_s: Heartbeat check interval
        heartbeat_dead_s: Silence threshold to force reconnect
        logger: ScenarioLogger instance
    """

    def __init__(
        self,
        symbol: str,
        ws_pair: str,
        tick_queue: queue.Queue,
        ws_url: str = 'wss://ws.kraken.com/v2',
        reconnect_initial_delay_s: float = 1.0,
        reconnect_max_delay_s: float = 60.0,
        heartbeat_interval_s: float = 30.0,
        heartbeat_dead_s: float = 90.0,
        logger: Optional[ScenarioLogger] = None,
    ):
        self._symbol = symbol
        self._ws_pair = ws_pair
        self._tick_queue = tick_queue
        self._ws_url = ws_url
        self._reconnect_initial_delay_s = reconnect_initial_delay_s
        self._reconnect_max_delay_s = reconnect_max_delay_s
        self._heartbeat_interval_s = heartbeat_interval_s
        self._heartbeat_dead_s = heartbeat_dead_s
        self._logger = logger

        self._running = False
        self._parser = KrakenTickMessageParser(symbol=symbol)
        self._ssl_context: Optional[ssl.SSLContext] = None

        # Stats
        self._ticks_emitted: int = 0
        self._reconnect_count: int = 0
        self._last_message_time: Optional[datetime] = None
        self._last_tick_time: Optional[datetime] = None

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
                f"📡 KrakenTickSource starting: {self._symbol} "
                f"({self._ws_pair}) -> {self._ws_url}"
            )
        try:
            asyncio.run(self._ws_loop())
        except Exception as e:
            if self._logger:
                self._logger.error(f"📡 KrakenTickSource fatal error: {e}")
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

    # === Async internals ===

    async def _ws_loop(self) -> None:
        """
        Main WebSocket loop with reconnection.

        Outer loop: connect -> subscribe -> receive/heartbeat concurrent tasks.
        On disconnect/error: log, backoff, reconnect.
        Runs until self._running is False.
        """
        self._ssl_context = self._create_ssl_context()
        reconnect_attempt = 0

        while self._running:
            try:
                ws = await self._connect_and_subscribe()
                reconnect_attempt = 0

                # Run receive + heartbeat concurrently
                receive_task = asyncio.create_task(self._receive_loop(ws))
                heartbeat_task = asyncio.create_task(self._heartbeat_monitor(ws))

                done, pending = await asyncio.wait(
                    [receive_task, heartbeat_task],
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
                    self._logger.warning(f"📡 WS connection closed: {e}")

            except asyncio.TimeoutError:
                if self._logger:
                    self._logger.warning('📡 WS connection/subscription timed out')

            except Exception as e:
                if self._logger:
                    self._logger.error(f"📡 WS error: {e}")

            # Reconnect with backoff (if still running)
            if self._running:
                delay = self._get_reconnect_delay(reconnect_attempt)
                self._reconnect_count += 1
                if self._logger:
                    self._logger.info(
                        f"📡 Reconnecting in {delay:.1f}s "
                        f"(attempt {reconnect_attempt + 1}, "
                        f"total reconnects: {self._reconnect_count})"
                    )
                await asyncio.sleep(delay)
                reconnect_attempt += 1

    async def _connect_and_subscribe(self):
        """
        Connect to WebSocket and subscribe to trade channel.

        Returns:
            Connected and subscribed WebSocket connection

        Raises:
            Exception: On connection or subscription failure
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
            self._logger.info(f"📡 WebSocket connected to {self._ws_url}")

        # Subscribe to trade channel
        subscribe_msg = json.dumps({
            'method': 'subscribe',
            'params': {
                'channel': 'trade',
                'symbol': [self._ws_pair],
            }
        })
        await ws.send(subscribe_msg)

        # Wait for confirmation (skip status/heartbeat messages Kraken sends after connect)
        deadline = asyncio.get_event_loop().time() + 10.0
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError('Subscription confirmation timeout')
            response = await asyncio.wait_for(ws.recv(), timeout=remaining)

            if self._parser.is_subscription_confirmation(response):
                if self._logger:
                    self._logger.info(
                        f"📡 Subscribed to trade channel: {self._ws_pair}"
                    )
                break

            error = self._parser.is_error_message(response)
            if error:
                raise ValueError(
                    f"Subscription failed for {self._ws_pair}: {error}. "
                    f"Check 'symbol_to_ws_pair' in broker settings — "
                    f"the symbol must match Kraken WS v2 format (e.g., 'BTC/USD')."
                )

            # Status, heartbeat, or other non-subscription messages — skip
            if self._logger:
                self._logger.debug(f"📡 Skipping pre-subscription message: {response[:120]}")

        return ws

    async def _receive_loop(self, ws) -> None:
        """
        Receive and process messages until disconnect or stop.

        Parses trade messages into TickData and pushes to queue.
        Heartbeats update _last_message_time but are not parsed.

        Args:
            ws: Active WebSocket connection
        """
        async for message in ws:
            if not self._running:
                break

            self._last_message_time = datetime.now(timezone.utc)

            # Heartbeats keep the connection alive but produce no ticks
            if self._parser.is_heartbeat(message):
                continue

            # Parse trade messages
            ticks = self._parser.parse_trade_message(message)
            if ticks:
                for tick in ticks:
                    self._tick_queue.put(tick)
                    self._ticks_emitted += 1
                self._last_tick_time = datetime.now(timezone.utc)

    async def _heartbeat_monitor(self, ws) -> None:
        """
        Monitor connection health via message timing.

        Checks _last_message_time periodically. If silence exceeds
        heartbeat_dead_s, closes the WebSocket to trigger reconnect
        in _ws_loop.

        Args:
            ws: Active WebSocket connection
        """
        while self._running:
            await asyncio.sleep(self._heartbeat_interval_s)

            if not self._last_message_time:
                continue

            silence = (
                datetime.now(timezone.utc) - self._last_message_time
            ).total_seconds()

            if silence > self._heartbeat_dead_s:
                if self._logger:
                    self._logger.warning(
                        f"📡 No messages for {silence:.0f}s "
                        f"(threshold: {self._heartbeat_dead_s:.0f}s), "
                        f"forcing reconnect"
                    )
                await ws.close()
                break

            elif silence > self._heartbeat_interval_s * 2:
                if self._logger:
                    self._logger.warning(
                        f"📡 No messages for {silence:.0f}s, "
                        f"connection may be stale"
                    )

    def _get_reconnect_delay(self, attempt: int) -> float:
        """
        Calculate reconnect delay with exponential backoff.

        Formula: initial * 2^attempt, capped at max.

        Args:
            attempt: Current reconnect attempt number (0-based)

        Returns:
            Delay in seconds
        """
        delay = self._reconnect_initial_delay_s * (2 ** attempt)
        return min(delay, self._reconnect_max_delay_s)

    def _create_ssl_context(self) -> ssl.SSLContext:
        """
        Create SSL context using certifi certificates.

        Cross-platform: works on Linux (Docker) and Windows (server).

        Returns:
            Configured SSL context
        """
        return ssl.create_default_context(cafile=certifi.where())
