"""
FiniexTestingIDE - Kraken Tick Message Parser
Parses Kraken WebSocket v2 trade messages into AutoTrader TickData.

Data consistency: uses the same trade channel as DataCollector,
so bid=ask=trade_price (spread=0), matching backtesting parquet data.
Crypto fees are handled by MakerTakerFee, not by spread.
"""

import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from python.framework.types.market_types.market_data_types import TickData


class KrakenTickMessageParser:
    """
    Parses Kraken WS v2 trade messages into AutoTrader TickData.

    Handles three message categories:
    - Trade updates (channel='trade', type='update'/'snapshot') -> TickData list
    - Heartbeats (channel='heartbeat') -> detected via is_heartbeat()
    - Subscription confirmations / errors -> detected via helper methods

    The parser is initialized with a fixed symbol (e.g., 'BTCUSD') because
    the AutoTrader runs one symbol per session. This avoids per-tick
    symbol normalization.

    Args:
        symbol: Internal trading symbol (e.g., 'BTCUSD')
    """

    def __init__(self, symbol: str):
        self._symbol = symbol

    def parse_trade_message(self, raw_message: str) -> Optional[List[TickData]]:
        """
        Parse a raw WebSocket message.

        Returns a list of TickData for trade channel messages (snapshot/update).
        Returns None for heartbeats, subscription confirmations, errors,
        and any other non-trade messages.

        Args:
            raw_message: JSON string from WebSocket

        Returns:
            List of TickData for trade messages, None otherwise
        """
        try:
            data = json.loads(raw_message)
        except json.JSONDecodeError:
            return None

        if not isinstance(data, dict):
            return None

        channel = data.get('channel')
        msg_type = data.get('type')

        if channel != 'trade' or msg_type not in ('snapshot', 'update'):
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

    def _parse_single_trade(self, trade: Dict[str, Any]) -> Optional[TickData]:
        """
        Convert a single trade dict to TickData.

        Trade price becomes both bid and ask (spread=0), consistent with
        DataCollector and the crypto maker/taker fee model.

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

            return TickData(
                timestamp=dt_utc,
                symbol=self._symbol,
                bid=price,
                ask=price,
                volume=qty,
                time_msc=time_msc,
                collected_msc=collected_msc,
            )

        except (KeyError, ValueError, TypeError):
            return None

    def is_heartbeat(self, raw_message: str) -> bool:
        """
        Check if message is a heartbeat.

        Args:
            raw_message: JSON string

        Returns:
            True if heartbeat message
        """
        try:
            data = json.loads(raw_message)
            return (
                isinstance(data, dict)
                and data.get('channel') == 'heartbeat'
            )
        except json.JSONDecodeError:
            return False

    def is_subscription_confirmation(self, raw_message: str) -> bool:
        """
        Check if message is a successful subscription confirmation.

        Args:
            raw_message: JSON string

        Returns:
            True if subscription confirmed
        """
        try:
            data = json.loads(raw_message)
            return (
                isinstance(data, dict)
                and data.get('method') == 'subscribe'
                and data.get('success') is True
            )
        except json.JSONDecodeError:
            return False

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
