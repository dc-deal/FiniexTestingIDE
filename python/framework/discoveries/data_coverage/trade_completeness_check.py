"""
Trade completeness - our collection against the venue's own trade list (#647).

The tick index counts the trade ids a file lacks, which is an upper bound: a venue may allocate
ids that never become public trades. This asks the venue: it reads the venue's public trade list
over a window and compares it with the trades our files hold there, trade by trade. The result is
how many of the venue's trades we did not record, in which runs, and any trade we hold that the
venue's list does not.

A repeatable diagnostic, not a filter: it changes nothing in the archive. Filling what it finds
from the venue's record is the tick backfiller's (#127).
"""

from datetime import datetime
from typing import List, Optional, Sequence

import numpy as np

from python.data_management.index.index_entry_times import entry_time_bounds
from python.data_management.index.tick_index_manager import TickIndexManager
from python.framework.exceptions.connection_errors import ConnectionGaveUpError
from python.framework.reporting.builders.divergence.backtest_side_collector import window_trades
from python.framework.types.coverage_report_types import MissingTradeRun, TradeCompletenessResult
from python.framework.types.venue_history_types import VenueTrade
from python.framework.venue_history.abstract_public_trade_reader import (
    AbstractPublicTradeReader,
)


def compare_with_venue(broker_type: str, symbol: str, start: datetime, end: datetime,
                       our_ids: np.ndarray, venue: Sequence[VenueTrade]) -> TradeCompletenessResult:
    """
    Compare our trade ids in a window with the venue's trades in it.

    Args:
        broker_type: The broker
        symbol: The symbol
        start: The window's start, included
        end: The window's end, excluded
        our_ids: The trade ids our files hold in the window
        venue: The venue's trades in the window, in its id order

    Returns:
        The comparison, its runs of missing trades longest first
    """
    ours = set(int(trade_id) for trade_id in our_ids)
    venue_ids = {trade.trade_id for trade in venue}
    runs: List[MissingTradeRun] = []
    current: List[VenueTrade] = []
    for trade in venue:
        if trade.trade_id in ours:
            if current:
                runs.append(MissingTradeRun(len(current), current[0].executed_at, current[-1].executed_at))
                current = []
            continue
        current.append(trade)
    if current:
        runs.append(MissingTradeRun(len(current), current[0].executed_at, current[-1].executed_at))
    runs.sort(key=lambda run: run.trades, reverse=True)
    return TradeCompletenessResult(
        broker_type=broker_type, symbol=symbol, start=start, end=end,
        our_trades=len(ours), venue_trades=len(venue_ids),
        missing_trades=len(venue_ids - ours), extra_trades=len(ours - venue_ids), runs=runs)


class TradeCompletenessCheck:
    """
    Reads both sides of a window and compares them.

    Args:
        tick_index: The tick index our files are found through
        trade_reader: The venue's public trade list; None where the venue publishes none we read
    """

    def __init__(self, tick_index: TickIndexManager,
                 trade_reader: Optional[AbstractPublicTradeReader]):
        self._tick_index = tick_index
        self._trade_reader = trade_reader

    def run(self, broker_type: str, symbol: str, start: datetime, end: datetime) -> TradeCompletenessResult:
        """
        Compare our trades with the venue's over a window.

        Args:
            broker_type: The broker
            symbol: The symbol
            start: The window's start, included
            end: The window's end, excluded

        Returns:
            The comparison; the venue's side None where its list could not be read, with the reason
        """
        our_ids, _ = window_trades(self._tick_index, broker_type, symbol, start, end)
        # Our side is comparable only where every file carries trade ids: a file without them would
        # make each venue trade in its stretch read as one we lack
        without_ids = self._files_without_trade_ids(broker_type, symbol, start, end)
        if without_ids:
            result = self._unread(broker_type, symbol, start, end, our_ids, '')
            result.not_measurable_because = (
                f'{len(without_ids)} tick file(s) in the window carry no trade ids (format '
                f'{", ".join(sorted(set(without_ids)))}); trade ids exist from collector format 1.7.0 on')
            return result
        if self._trade_reader is None:
            return self._unread(broker_type, symbol, start, end, our_ids,
                                f'{broker_type}: the venue publishes no trade list we read')
        try:
            venue = self._trade_reader.read_trades(symbol, start, end)
        except ConnectionGaveUpError as error:
            # A list read in part would read as trades we lack; the window is reported unread
            return self._unread(broker_type, symbol, start, end, our_ids, str(error))
        return compare_with_venue(broker_type, symbol, start, end, our_ids, venue)

    def _files_without_trade_ids(self, broker_type: str, symbol: str, start: datetime,
                                 end: datetime) -> List[str]:
        """
        The data format versions of the window's tick files that carry no trade ids.

        Args:
            broker_type: The broker
            symbol: The symbol
            start: The window's start
            end: The window's end

        Returns:
            One version per such file; empty where every file carries trade ids
        """
        entries = self._tick_index.get_symbol_entries(broker_type, symbol)
        if not entries:
            return []
        starts, ends = entry_time_bounds(entries)
        return [entry.get('data_format_version', 'unknown')
                for entry, first, last in zip(entries, starts, ends)
                if first < end and last >= start and entry.get('trade_id_gaps') is None]

    @staticmethod
    def _unread(broker_type: str, symbol: str, start: datetime, end: datetime,
                our_ids: np.ndarray, reason: str) -> TradeCompletenessResult:
        """
        A window whose venue side was not read: our count, nothing compared.

        Args:
            broker_type: The broker
            symbol: The symbol
            start: The window's start
            end: The window's end
            our_ids: The trade ids our files hold in the window
            reason: Why the venue's list was not read

        Returns:
            The result, its venue side None
        """
        return TradeCompletenessResult(
            broker_type=broker_type, symbol=symbol, start=start, end=end,
            our_trades=len(set(int(trade_id) for trade_id in our_ids)), venue_trades=None,
            missing_trades=None, extra_trades=None, runs=[], not_read_because=reason)
