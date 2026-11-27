"""
Kraken's ledger of an account — every movement of a balance, with the order behind each trade.

`POST /0/private/Ledgers` answers up to fifty entries per page, newest first, and `count` for the
whole stretch; pages are walked by `ofs` and joined by entry id. A trade books one entry per
currency it moved, both with the trade's id as `refid`; `POST /0/private/QueryTrades` names the
order of each trade (`ordertxid`), up to twenty trades per call. Both need a key with the rights
to query the ledger and the closed orders and trades — nothing more.

Measured 2026-11-09 on the account of the thirty-day run (shapes only): entries carry `refid`,
`time`, `type`, `subtype`, `aclass`, `asset`, `amount`, `fee`, `balance`; a spot trade is
type 'trade', subtype 'tradespot', one leg in XETH and one in ZUSD, the fee on the ZUSD leg; a
conversion outside the order book is a 'spend' and a 'receive' with no order; a deposit is a
'deposit'. The other types are taken from Kraken's documentation of the endpoint, not measured.
"""

import math
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence

from python.adapters.kraken.kraken_api_client import KrakenApiClient
from python.configuration.autotrader.kraken_config_fetcher import KrakenConfigFetcher
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.types.connection_types import GiveUpAction
from python.framework.types.venue_history_types import VenueLedgerEntry, VenueLedgerEntryKind
from python.framework.utils.connection_ladder import ConnectionLadder, run_with_ladder
from python.framework.venue_history.abstract_venue_ledger_reader import (
    AbstractVenueLedgerReader,
)

LEDGERS_ENDPOINT = '/0/private/Ledgers'
QUERY_TRADES_ENDPOINT = '/0/private/QueryTrades'

# The most trades one QueryTrades call names
_TRADES_PER_QUERY = 20

# Kraken's ledger types, each with its kind; a type not listed reads as OTHER
KRAKEN_VENUE_LEDGER_KINDS: Dict[str, VenueLedgerEntryKind] = {
    'trade': VenueLedgerEntryKind.TRADE,
    'margin': VenueLedgerEntryKind.TRADE,
    'spend': VenueLedgerEntryKind.CONVERSION,
    'receive': VenueLedgerEntryKind.CONVERSION,
    'sale': VenueLedgerEntryKind.CONVERSION,
    'conversion': VenueLedgerEntryKind.CONVERSION,
    'deposit': VenueLedgerEntryKind.DEPOSIT,
    'withdrawal': VenueLedgerEntryKind.WITHDRAWAL,
    'transfer': VenueLedgerEntryKind.TRANSFER,
    'custodytransfer': VenueLedgerEntryKind.TRANSFER,
    'staking': VenueLedgerEntryKind.EARN,
    'reward': VenueLedgerEntryKind.EARN,
    'dividend': VenueLedgerEntryKind.EARN,
    'earn': VenueLedgerEntryKind.EARN,
    'adjustment': VenueLedgerEntryKind.ADJUSTMENT,
    'credit': VenueLedgerEntryKind.ADJUSTMENT,
    'rollover': VenueLedgerEntryKind.ADJUSTMENT,
    'settled': VenueLedgerEntryKind.ADJUSTMENT,
}

# Kraken's asset codes where they differ from ours ('XETH' → 'ETH', 'ZUSD' → 'USD'), from the one
# map the configuration fetcher keeps of them
_OUR_ASSET_CODE: Dict[str, str] = {
    kraken_code: our_code
    for our_code, kraken_codes in KrakenConfigFetcher.STANDARD_TO_KRAKEN_BALANCE.items()
    for kraken_code in kraken_codes
}


class KrakenVenueLedgerReader(AbstractVenueLedgerReader):
    """
    Reads an account's ledger at Kraken, with the order of every trade.

    Args:
        client: A Kraken client with the credentials of a key that may read the ledger and the
            closed orders and trades
        connection: The ladder's numbers for one request; a give-up always raises, because a
            ledger read in part would read as one without movements
        logger: Where the ladder reports its attempts and its give-up
        pace_s: The pause between two requests — a ledger read costs twice an ordinary private
            call against Kraken's counter, which falls by about one every three seconds
        wait: How to pause; time.sleep unless a test runs it without waiting
    """

    def __init__(self, client: KrakenApiClient, connection: ConnectionPolicy,
                 logger: AbstractLogger, pace_s: float = 3.0,
                 wait: Callable[[float], None] = time.sleep):
        self._client = client
        self._ladder = ConnectionLadder(
            'venue_ledger',
            connection.model_copy(update={'on_give_up': GiveUpAction.ABORT}), logger)
        self._pace_s = pace_s
        self._wait = wait
        self._requests = 0

    def read_ledger(self, start: datetime, end: datetime) -> List[VenueLedgerEntry]:
        """
        Every entry of the account's ledger in a stretch of time.

        Args:
            start: The stretch's start, included
            end: Its end, excluded

        Returns:
            The entries in booking order, each trade's leg with the order it belongs to
        """
        raw = self._ledger_pages(start, end)
        trade_refs = sorted({entry['refid'] for entry in raw.values()
                             if _kind_of(entry).may_belong_to_an_order()})
        orders = self._orders_of(trade_refs)
        entries = [_entry_of(entry_id, entry, orders) for entry_id, entry in raw.items()]
        return sorted((entry for entry in entries if start <= entry.executed_at < end),
                      key=lambda entry: (entry.executed_at, entry.entry_id))

    def _ledger_pages(self, start: datetime, end: datetime) -> Dict[str, Dict]:
        """
        The ledger's raw entries around a stretch, every page of it.

        Args:
            start: The stretch's start
            end: Its end

        Returns:
            Entry id → Kraken's entry, for every entry from the second before the start to the
            second after the end — Kraken takes whole seconds, its start excluded
        """
        data = {'start': math.floor(start.timestamp()) - 1, 'end': math.ceil(end.timestamp())}
        entries: Dict[str, Dict] = {}
        offset = 0
        while True:
            page_data = dict(data, ofs=offset)
            result = self._request(LEDGERS_ENDPOINT, page_data)
            page = result.get('ledger', {})
            entries.update(page)
            offset += len(page)
            if not page or offset >= int(result.get('count', 0)):
                return entries

    def _orders_of(self, trade_refs: Sequence[str]) -> Dict[str, str]:
        """
        The order of each trade, as Kraken names it.

        Args:
            trade_refs: The trades' ids

        Returns:
            Trade id → the order's handle, for each trade Kraken named an order for
        """
        orders: Dict[str, str] = {}
        for first in range(0, len(trade_refs), _TRADES_PER_QUERY):
            batch = trade_refs[first:first + _TRADES_PER_QUERY]
            result = self._request(QUERY_TRADES_ENDPOINT, {'txid': ','.join(batch)})
            for trade_id, trade in result.items():
                if trade.get('ordertxid'):
                    orders[trade_id] = trade['ordertxid']
        return orders

    def _request(self, endpoint: str, data: Dict) -> Dict:
        """
        One private request under the ladder, paced behind the one before.

        Args:
            endpoint: The path
            data: Its form data

        Returns:
            The answer's result
        """
        if self._requests:
            self._wait(self._pace_s)
        self._requests += 1
        return run_with_ladder(lambda: self._client.fetch_private(endpoint, data), self._ladder,
                               self._wait)


def _kind_of(entry: Dict) -> VenueLedgerEntryKind:
    """
    The kind of one of Kraken's entries.

    Args:
        entry: The entry as Kraken answered it

    Returns:
        Its kind; OTHER for a type the map does not list
    """
    return KRAKEN_VENUE_LEDGER_KINDS.get(entry.get('type', ''), VenueLedgerEntryKind.OTHER)


def _entry_of(entry_id: str, entry: Dict, orders: Dict[str, str]) -> VenueLedgerEntry:
    """
    One of Kraken's entries in our words.

    Args:
        entry_id: Its id
        entry: The entry as Kraken answered it
        orders: Trade id → its order's handle

    Returns:
        The entry
    """
    kind = _kind_of(entry)
    order_ref: Optional[str] = orders.get(entry['refid']) if kind.may_belong_to_an_order() else None
    return VenueLedgerEntry(
        entry_id=entry_id, venue_ref=entry['refid'],
        executed_at=datetime.fromtimestamp(float(entry['time']), tz=timezone.utc),
        kind=kind, venue_type=entry.get('type', ''), venue_subtype=entry.get('subtype', ''),
        asset=_OUR_ASSET_CODE.get(entry['asset'], entry['asset']),
        amount=float(entry['amount']), fee=float(entry['fee']), order_ref=order_ref)
