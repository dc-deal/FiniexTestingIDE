"""
Our Trades Against the Venue's List — Trade Completeness Tests (#647).

A missing trade id is an upper bound: a venue may allocate ids that never become public trades.
The completeness check asks the venue's own trade list instead, trade by trade, and reports the
trades our files lack in runs, and any trade we hold that the list does not. A list it could not
read is reported as not read — never as a window without trades.

The venue's list and our window are built here; nothing is fetched.
"""

from datetime import datetime, timedelta, timezone
from typing import List

import numpy as np

from python.framework.discoveries.data_coverage import trade_completeness_check
from python.framework.discoveries.data_coverage.trade_completeness_check import (
    TradeCompletenessCheck,
    compare_with_venue,
)
from python.framework.exceptions.connection_errors import ConnectionGaveUpError
from python.framework.types.data_deficiency_types import TradeIdGaps
from python.framework.types.venue_history_types import VenueTrade
from python.framework.venue_history.abstract_public_trade_reader import (
    AbstractPublicTradeReader,
)

_START = datetime(2026, 11, 16, 0, 0, tzinfo=timezone.utc)
_END = _START + timedelta(hours=1)


def _venue(*trade_ids: int) -> List[VenueTrade]:
    """
    The venue's trades with the given ids, one second apart.

    Args:
        trade_ids: Their ids, in the venue's order

    Returns:
        The trades
    """
    return [VenueTrade(trade_id=trade_id, executed_at=_START + timedelta(seconds=index),
                       price=3000.0, volume=0.1)
            for index, trade_id in enumerate(trade_ids)]


class _Reader(AbstractPublicTradeReader):
    """A venue that answers with a fixed list, or gives up."""

    def __init__(self, trades: List[VenueTrade], gives_up: bool = False):
        self._trades = trades
        self._gives_up = gives_up

    def read_trades(self, symbol: str, start: datetime, end: datetime) -> List[VenueTrade]:
        if self._gives_up:
            raise ConnectionGaveUpError('venue_public_trades gave up: the venue did not answer')
        return self._trades


class _Index:
    """A tick index holding one file over the window, with or without trade ids."""

    def __init__(self, with_ids: bool = True):
        self._entry = {
            'data_format_version': '1.7.0' if with_ids else '1.5.0',
            'start_time': _START.isoformat(), 'end_time': _END.isoformat(),
            'trade_id_gaps': TradeIdGaps(span=3, missing=0, runs=0, longest_run=0,
                                         longest_run_seconds=0.0) if with_ids else None,
        }

    def get_symbol_entries(self, broker_type: str, symbol: str) -> List[dict]:
        return [self._entry]


class TestTheComparison:

    def test_the_trades_we_lack_are_counted_in_runs_longest_first(self):
        result = compare_with_venue('kraken_spot', 'ETHUSD', _START, _END,
                                    np.array([1, 2, 5, 9]), _venue(1, 2, 3, 4, 5, 6, 7, 8, 9))

        assert (result.our_trades, result.venue_trades, result.missing_trades) == (4, 9, 5)
        assert [run.trades for run in result.runs] == [3, 2]
        longest = result.runs[0]
        assert (longest.first_at, longest.last_at) == (
            _START + timedelta(seconds=5), _START + timedelta(seconds=7))
        assert result.extra_trades == 0

    def test_a_trade_the_venue_did_not_publish_is_counted_apart(self):
        result = compare_with_venue('kraken_spot', 'ETHUSD', _START, _END,
                                    np.array([1, 2, 3, 99]), _venue(1, 2, 3))

        assert result.missing_trades == 0
        assert result.extra_trades == 1
        assert result.runs == []


class TestTheVenueSide:

    def _check(self, reader, monkeypatch, with_ids: bool = True) -> TradeCompletenessCheck:
        monkeypatch.setattr(trade_completeness_check, 'window_trades',
                            lambda *args: (np.array([1, 2]), np.array([0, 1])))
        return TradeCompletenessCheck(tick_index=_Index(with_ids), trade_reader=reader)

    def test_a_list_that_could_not_be_read_compares_nothing(self, monkeypatch):
        result = self._check(_Reader([], gives_up=True), monkeypatch).run(
            'kraken_spot', 'ETHUSD', _START, _END)

        assert result.our_trades == 2
        assert (result.venue_trades, result.missing_trades, result.extra_trades) == (None, None, None)
        assert 'did not answer' in result.not_read_because

    def test_a_venue_without_a_trade_list_says_so(self, monkeypatch):
        result = self._check(None, monkeypatch).run('mt5', 'EURUSD', _START, _END)

        assert result.venue_trades is None
        assert 'no trade list' in result.not_read_because

    def test_a_read_list_is_compared(self, monkeypatch):
        result = self._check(_Reader(_venue(1, 2, 3)), monkeypatch).run(
            'kraken_spot', 'ETHUSD', _START, _END)

        assert result.missing_trades == 1

    def test_a_window_whose_files_carry_no_trade_ids_is_not_measured(self, monkeypatch):
        """Every venue trade would read as missing; the window is named not measurable instead."""
        result = self._check(_Reader(_venue(1, 2, 3)), monkeypatch, with_ids=False).run(
            'kraken_spot', 'ETHUSD', _START, _END)

        assert result.missing_trades is None
        assert 'carry no trade ids' in result.not_measurable_because
        assert '1.5.0' in result.not_measurable_because
