"""
Kraken's public trade list — every trade of a pair, from its first one on, without an account.

`GET /0/public/Trades` pages forward from `since`, a time in nanoseconds; each answer carries up to
a thousand trades and, as `last`, the `since` of the next page. Measured 2026-11-09 on ETHUSD: a row
is [price, volume, time in seconds, side, order type, misc, trade id]; the answer names the pair
by Kraken's own key ('XETHZUSD' for 'ETHUSD'); a `since` equal to a trade's own time leaves that
trade out, and `last` repeats the last trade's time. Pages are joined by trade id, so no trade is
counted twice. Measured 2026-10-05 (#127): Kraken also allocates ids that never become public
trades — the list, not the run of ids, says which trades there were.

This is the fetch stage of the tick backfiller (#127), which the divergence report (#512) uses to
confirm the trades our archive lacks.
"""

import time
from datetime import datetime, timezone
from typing import Callable, Dict, List

from python.adapters.kraken.kraken_api_client import KrakenApiClient
from python.framework.autotrader.kraken_ohlc_bar_fetcher import KrakenOhlcBarFetcher
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.types.connection_types import GiveUpAction
from python.framework.types.venue_history_types import VenueTrade
from python.framework.utils.connection_ladder import ConnectionLadder, run_with_ladder
from python.framework.venue_history.abstract_public_trade_reader import (
    AbstractPublicTradeReader,
)

TRADES_ENDPOINT = '/0/public/Trades'

# The most trades one answer carries
_PAGE_SIZE = 1000

# Kraken leaves out a trade whose time equals `since`; one microsecond earlier keeps it
_SINCE_MARGIN_NS = 1000


class KrakenPublicTradeReader(AbstractPublicTradeReader):
    """
    Reads Kraken's public trades of a pair, page by page.

    Args:
        client: The Kraken client; a public one suffices
        connection: The ladder's numbers for one page; a give-up always raises, because a stretch
            read in part would read as one without trades
        logger: Where the ladder reports its attempts and its give-up
        pace_s: The pause between two pages — Kraken counts public calls per address
        wait: How to pause; time.sleep unless a test runs it without waiting
    """

    def __init__(self, client: KrakenApiClient, connection: ConnectionPolicy,
                 logger: AbstractLogger, pace_s: float = 1.0,
                 wait: Callable[[float], None] = time.sleep):
        self._client = client
        self._ladder = ConnectionLadder(
            'venue_public_trades', connection.model_copy(update={'on_give_up': GiveUpAction.ABORT}),
            logger)
        self._pace_s = pace_s
        self._wait = wait

    def read_trades(self, symbol: str, start: datetime, end: datetime) -> List[VenueTrade]:
        """
        Every trade Kraken published for a symbol in a stretch of time.

        Args:
            symbol: Our symbol ('ETHUSD')
            start: The stretch's start, included
            end: Its end, excluded

        Returns:
            The trades, each once, in id order
        """
        pair = KrakenOhlcBarFetcher.SYMBOL_TO_KRAKEN_PAIR.get(symbol, symbol)
        since = _nanoseconds(start) - _SINCE_MARGIN_NS
        trades: Dict[int, VenueTrade] = {}
        while True:
            params = {'pair': pair, 'since': str(since), 'count': _PAGE_SIZE}
            result = run_with_ladder(lambda: self._client.fetch_public(TRADES_ENDPOINT, params),
                                     self._ladder, self._wait)
            rows = next((value for key, value in result.items() if key != 'last'), [])
            for row in rows:
                trade = _trade_of(row)
                if start <= trade.executed_at < end:
                    trades[trade.trade_id] = trade
            last = int(result.get('last', since))
            if not rows or last <= since or _trade_of(rows[-1]).executed_at >= end:
                break
            since = last
            self._wait(self._pace_s)
        return sorted(trades.values(), key=lambda trade: trade.trade_id)


def _trade_of(row: list) -> VenueTrade:
    """
    One row of Kraken's answer as a trade.

    Args:
        row: [price, volume, time in seconds, side, order type, misc, trade id]

    Returns:
        The trade
    """
    return VenueTrade(trade_id=int(row[6]),
                      executed_at=datetime.fromtimestamp(float(row[2]), tz=timezone.utc),
                      price=float(row[0]), volume=float(row[1]))


def _nanoseconds(instant: datetime) -> int:
    """
    An instant in nanoseconds since the epoch, without a float's rounding.

    Args:
        instant: A timezone-aware instant

    Returns:
        Its nanoseconds
    """
    return int(instant.timestamp()) * 1_000_000_000 + instant.microsecond * 1000
