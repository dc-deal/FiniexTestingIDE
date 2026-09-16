"""
FiniexTestingIDE - Kraken Tick Message Parser
Parses Kraken WebSocket v2 trade and ticker messages into AutoTrader TickData.

Data consistency: an execution happens at exactly ONE price, so a trade tick on its own
carries bid == ask. The quote it executed against rides the ticker channel, and from
collector format 1.6.0 the archive states it on every tick. This parser does the same
(#520 step B) so an archived tick and a live one describe the same thing.

What that does NOT change is the strategy's price: `last` is the traded price on both
sides, and a worker reads `tick.price` (§31c). The quote moves `mid`, and with it the
valuation plane — equity, drawdown, the mark price, the slippage baseline.

Without a quote — the first trades after a start or a reconnect, or a ticker channel that
never came up — the trade price stands in for both sides exactly as it always did.
"""

import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from python.framework.types.market_types.market_data_types import ObservedQuote, TickData


class KrakenTickMessageParser:
    """
    Parses Kraken WS v2 messages into AutoTrader TickData.

    Handles four message categories:
    - Trade updates (channel='trade', type='update'/'snapshot') -> TickData list
    - Ticker updates (channel='ticker', type='update'/'snapshot') -> quote only, no ticks
    - Heartbeats and status frames -> nothing, via parse_message
    - Subscription confirmations / errors -> detected via helper methods, used at handshake

    The parser is initialized with a fixed symbol (e.g., 'BTCUSD') because
    the AutoTrader runs one symbol per session. This avoids per-tick
    symbol normalization — and it is why the quote is held as ONE value rather
    than the collector's per-symbol cache: that side collects many symbols, this
    side would key a dict on a constant.

    Args:
        symbol: Internal trading symbol (e.g., 'BTCUSD')
    """

    def __init__(self, symbol: str):
        self._symbol = symbol
        # Written on the socket thread, read there and by the display thread. One reference to
        # a frozen value, so the reader either sees the previous quote or the next one whole,
        # and no lock is needed for either.
        self._last_quote: Optional[ObservedQuote] = None
        self._quotes_received: int = 0

    def parse_message(self, raw_message: str) -> Optional[List[TickData]]:
        """
        Parse a raw WebSocket message, routing it by channel.

        One json.loads for the whole message. The alternative — a predicate per channel, each
        parsing again — cost three passes over every frame once the ticker channel joined, and
        the ticker channel alone carries twenty times the trade channel's volume.

        Args:
            raw_message: JSON string from WebSocket

        Returns:
            List of TickData for trade messages, None for everything else
        """
        try:
            data = json.loads(raw_message)
        except json.JSONDecodeError:
            return None

        if not isinstance(data, dict):
            return None

        channel = data.get('channel')
        msg_type = data.get('type')

        if msg_type not in ('snapshot', 'update'):
            return None

        if channel == 'ticker':
            self._consume_ticker(data)
            # Deliberately no ticks: a ticker update's time base is our local receipt while a
            # trade's is the exchange's event time, and interleaving the two in one stream steps
            # time backwards on nearly every channel change. The collector refuses it for the
            # same reason, one layer earlier.
            return None

        if channel != 'trade':
            return None

        trade_data = data.get('data', [])
        if not trade_data:
            return None

        ticks = []
        for trade in trade_data:
            tick = self._parse_single_trade(trade)
            if tick is not None:
                ticks.append(tick)

        return ticks if ticks else None

    def get_last_quote(self) -> Optional[ObservedQuote]:
        """
        The most recent quote observed on the ticker channel.

        GIL-safe for the display thread: one reference read of a frozen value, never a
        half-updated pair.

        Returns:
            The last ObservedQuote, or None if none has been observed yet
        """
        return self._last_quote

    def forget_quote(self) -> None:
        """
        Drop the held quote, so trades fall back to the trade price on both sides.

        Called when the quote channel is known to be unavailable. Holding one we can no longer
        refresh would keep stamping a spread on every tick that is arbitrarily old and says
        nothing about it — the tick carries no age, so a reader could not tell. Falling back is
        the honest answer and it is the behaviour the fallback contract already describes.

        A quote is never dropped merely for being OLD: a quiet channel is still a working one,
        and the age reported on the display is what says so.
        """
        self._last_quote = None

    def get_quotes_received(self) -> int:
        """
        Quote updates accepted this session.

        Returns:
            Count of accepted ticker updates (dropped ones are not counted)
        """
        return self._quotes_received

    def _consume_ticker(self, data: Dict[str, Any]) -> None:
        """
        Record the quote from a ticker message.

        A crossed or non-positive quote is DROPPED rather than stored: it would be written onto
        a trade tick as fact, and the previous quote ageing visibly is a better answer than a
        fresh impossible one. The symbol is not checked — this source subscribes to exactly one
        pair, so the venue sends no other.

        Args:
            data: Parsed ticker message
        """
        ticker_data = data.get('data', [])
        if not ticker_data:
            return

        for ticker in ticker_data:
            try:
                bid = float(ticker.get('bid', 0))
                ask = float(ticker.get('ask', 0))
            except (TypeError, ValueError):
                continue

            if bid <= 0 or ask <= 0 or ask < bid:
                continue

            self._last_quote = ObservedQuote(
                bid=bid,
                ask=ask,
                observed_monotonic_s=time.monotonic(),
            )
            self._quotes_received += 1

    def _parse_single_trade(self, trade: Dict[str, Any]) -> Optional[TickData]:
        """
        Convert a single trade dict to TickData.

        The execution happened at one price; the quote it executed against comes from the
        ticker channel. Without one, the trade price stands in for both sides as it always
        did. A trade is never held back waiting for a quote.

        Kraken trade format:
        {
            "symbol": "BTC/USD",
            "side": "buy",
            "price": 67123.4,
            "qty": 0.01,
            "timestamp": "2026-01-19T07:44:05.371000Z"
        }

        Args:
            trade: Single trade entry from Kraken 'data' array

        Returns:
            TickData or None if trade is invalid
        """
        try:
            price = float(trade.get('price', 0))
            if price <= 0:
                return None

            qty = float(trade.get('qty', 0))

            # Parse Kraken ISO timestamp -> datetime UTC + time_msc
            timestamp_str = trade.get('timestamp', '')
            if timestamp_str:
                try:
                    dt_utc = datetime.fromisoformat(
                        timestamp_str.replace('Z', '+00:00')
                    )
                    time_msc = int(dt_utc.timestamp() * 1000)
                except ValueError:
                    dt_utc = datetime.now(timezone.utc)
                    time_msc = int(time.time() * 1000)
            else:
                dt_utc = datetime.now(timezone.utc)
                time_msc = int(time.time() * 1000)

            # Local clock at receipt
            collected_msc = int(time.time() * 1000)

            quote = self._last_quote
            bid = quote.bid if quote is not None else price
            ask = quote.ask if quote is not None else price

            return TickData(
                timestamp=dt_utc,
                symbol=self._symbol,
                bid=bid,
                ask=ask,
                volume=qty,
                time_msc=time_msc,
                collected_msc=collected_msc,
                # Kraken is order-driven: this IS the traded price, and carrying it keeps the
                # live tick and an archived one the same shape. §41 — a field present in the
                # archive and absent live is reachable by a worker and would read differently
                # on the two sides, which is the parity break that rule exists to prevent.
                # Unchanged by the quote above: bid/ask move, the traded price does not.
                last=price,
            )

        except (KeyError, ValueError, TypeError):
            return None

    def is_subscription_confirmation(
        self,
        raw_message: str,
        channel: Optional[str] = None,
    ) -> bool:
        """
        Check if message is a successful subscription confirmation.

        Kraken names the channel in the ack's `result` block (measured 2026-09-16:
        `{"method":"subscribe","result":{"channel":"ticker",...},"success":true}`), which is what
        lets two subscriptions on one connection be confirmed independently. Without the channel
        argument the first ack would satisfy both waits and a failed second subscription would
        look like a successful one.

        Args:
            raw_message: JSON string
            channel: Require the ack to name this channel; any channel when None

        Returns:
            True if subscription confirmed for the requested channel
        """
        try:
            data = json.loads(raw_message)
        except json.JSONDecodeError:
            return False

        if not isinstance(data, dict):
            return False
        if data.get('method') != 'subscribe' or data.get('success') is not True:
            return False
        if channel is None:
            return True

        result = data.get('result')
        return isinstance(result, dict) and result.get('channel') == channel

    def is_error_message(self, raw_message: str) -> Optional[str]:
        """
        Check if message is an error and extract error text.

        Args:
            raw_message: JSON string

        Returns:
            Error message string if error, None otherwise
        """
        try:
            data = json.loads(raw_message)
            if isinstance(data, dict) and data.get('success') is False:
                return data.get('error', 'Unknown error')
            return None
        except json.JSONDecodeError:
            return None
