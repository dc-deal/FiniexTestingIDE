"""
FiniexTestingIDE - A Tick Price No Bar May Be Built From Is Refused Where It Is Consumed

The tick reader derives every tick's price once: what traded where the venue prints trades, the
book midpoint where it does not. A bar renders that price as it stands, so a price that is not
above zero — or a midpoint of a quote with a side at zero, which is positive and still wrong —
would reach the bars silently. Where the price is consumed, the file is refused: the bar import
fails the symbol, and the backtest refuses every scenario of that symbol while the others run on.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from python.data_management.importers.bar_importer import _load_all_ticks_for_symbol
from python.data_management.index.tick_index_manager import TickIndexManager
from python.framework.data_preparation.shared_data_preparator import SharedDataPreparator
from python.framework.data_preparation.tick_parquet_reader import read_tick_parquet
from python.framework.data_preparation.tick_price_check import refuse_unusable_price
from python.framework.exceptions.data_quality_errors import (
    TickPriceUnusableError,
    TradedPriceMissingException,
)
from python.framework.types.process_data_types import TickRequirement

_START = datetime(2026, 3, 2, 10, 0, tzinfo=timezone.utc)
_STAMP = '%Y-%m-%dT%H:%M:%S'


def _write(path: Path, bid: List[float], ask: List[float],
           last: Optional[List[float]] = None) -> Dict[str, object]:
    """
    Write one tick file, a second apart per tick, and return its index entry.

    Args:
        path: Where the file goes
        bid: The bid of every tick
        ask: The ask of every tick
        last: The traded price of every tick, or None for a file without the column

    Returns:
        The index entry
    """
    times = [_START + timedelta(seconds=i) for i in range(len(bid))]
    columns = {
        'timestamp': times,
        'time_msc': [int(t.timestamp() * 1000) for t in times],
        'bid': bid,
        'ask': ask,
        'volume': [0.01] * len(bid),
    }
    if last is not None:
        columns['last'] = last
    pd.DataFrame(columns).to_parquet(path)
    return {'path': str(path), 'start_time': times[0].strftime(_STAMP),
            'end_time': times[-1].strftime(_STAMP), 'tick_count': len(bid)}


def _price(path: Path) -> List[float]:
    """
    Read a file and judge its prices the way the bar import and the backtest do.

    Args:
        path: The tick file

    Returns:
        The derived prices, once the file passed
    """
    frame = read_tick_parquet(path)
    refuse_unusable_price(path, frame)
    return frame['price'].tolist()


class TestAnUnusablePriceIsRefused:
    """
    Every way a derived price can be unusable, and the cases that look close and pass.
    """

    def test_a_traded_price_passes(self, tmp_path):
        path = tmp_path / 'BTCUSD_traded.parquet'
        _write(path, [100.0, 100.1], [100.2, 100.3], last=[100.1, 100.2])
        assert _price(path) == [100.1, 100.2]

    def test_a_quote_driven_file_reads_its_midpoint(self, tmp_path):
        # A venue without a central place of trade writes 0.0 as its traded price on every row
        path = tmp_path / 'EURUSD_quote.parquet'
        _write(path, [1.1000, 1.1002], [1.1002, 1.1004], last=[0.0, 0.0])
        assert [round(price, 5) for price in _price(path)] == [1.1001, 1.1003]

    def test_a_flat_file_without_the_column_passes(self, tmp_path):
        # Collector files before 1.6.0 carry the trade as their quote: bid = ask = the trade
        path = tmp_path / 'BTCUSD_flat.parquet'
        _write(path, [100.0, 100.5], [100.0, 100.5])
        assert _price(path) == [100.0, 100.5]

    def test_a_traded_tick_is_not_judged_by_its_quote(self, tmp_path):
        # Its price is what traded; the quote is not what a bar is built from here
        path = tmp_path / 'BTCUSD_noquote.parquet'
        _write(path, [0.0], [0.0], last=[100.0])
        assert _price(path) == [100.0]

    def test_a_price_of_zero_is_refused(self, tmp_path):
        path = tmp_path / 'BTCUSD_zero.parquet'
        _write(path, [100.0, 0.0], [100.2, 0.0], last=[100.1, 0.0])
        with pytest.raises(TickPriceUnusableError, match='1 of 2 ticks carry no usable price'):
            _price(path)

    def test_a_midpoint_of_a_one_sided_quote_is_refused(self, tmp_path):
        # Half the ask is a positive number, and still no price
        path = tmp_path / 'EURUSD_onesided.parquet'
        _write(path, [1.1000, 0.0], [1.1002, 1.1004], last=[0.0, 0.0])
        with pytest.raises(TickPriceUnusableError):
            _price(path)

    def test_a_price_that_is_not_a_number_is_refused(self, tmp_path):
        path = tmp_path / 'EURUSD_nan.parquet'
        _write(path, [1.1000, np.nan], [1.1002, 1.1004])
        with pytest.raises(TickPriceUnusableError):
            _price(path)

    def test_the_reader_itself_refuses_nothing(self, tmp_path):
        # A caller that reads the quote as data — the divergence report skips a tick without a
        # bid on its own terms — gets the file as it is
        path = tmp_path / 'ETHUSD_quotes.parquet'
        _write(path, [3000.0, 0.0], [3001.0, 3002.0])
        frame = read_tick_parquet(path, columns=['time_msc', 'bid', 'ask'])
        assert frame['bid'].tolist() == [3000.0, 0.0]

    def test_the_message_names_the_file_the_first_tick_and_an_absent_last(self, tmp_path):
        path = tmp_path / 'EURUSD_named.parquet'
        _write(path, [1.1000, 0.0, 0.0], [1.1002, 1.1004, 1.1006], last=[0.0, 0.0, 0.0])
        with pytest.raises(TickPriceUnusableError) as caught:
            _price(path)
        message = str(caught.value)
        assert message.startswith('EURUSD_named.parquet: 2 of 3 ticks carry no usable price')
        assert '2026-03-02 10:00:01.000 UTC' in message
        assert 'no last' in message
        assert 'last 0.0' not in message


class TestTheBarImportRefusesTheSymbol:
    """
    The bar import reads a symbol's files and judges each before it renders.
    """

    def test_an_unusable_file_stops_the_symbols_load(self, tmp_path):
        index = TickIndexManager.__new__(TickIndexManager)
        index.index = {'kraken_spot': {'BTCUSD': [
            _write(tmp_path / 'BTCUSD_good.parquet', [100.0], [100.2], last=[100.1]),
            _write(tmp_path / 'BTCUSD_bad.parquet', [0.0], [0.0], last=[0.0])]}}
        with pytest.raises(TickPriceUnusableError, match='BTCUSD_bad.parquet'):
            _load_all_ticks_for_symbol(index, 'BTCUSD', 'kraken_spot', [])


def _preparator(entries: Dict[str, List[Dict[str, object]]]) -> SharedDataPreparator:
    """
    The shared preparator over a synthetic archive, its index built by hand.

    Args:
        entries: Per symbol of kraken_spot, the index entries

    Returns:
        The preparator
    """
    index = TickIndexManager.__new__(TickIndexManager)
    index.index = {'kraken_spot': entries}
    index.logger = MagicMock()
    preparator = SharedDataPreparator.__new__(SharedDataPreparator)
    preparator._logger = MagicMock()
    preparator.tick_index_manager = index
    return preparator


def _requirement(scenario_name: str, symbol: str) -> TickRequirement:
    """
    One time-span requirement over the synthetic files.

    Args:
        scenario_name: The scenario
        symbol: Its symbol

    Returns:
        The requirement
    """
    return TickRequirement(scenario_name=scenario_name, broker_type='kraken_spot', symbol=symbol,
                           start_time=_START, end_time=_START + timedelta(minutes=1))


class TestTheBacktestRefusesTheSymbolNotTheBatch:
    """
    A data error at loading refuses every scenario of its symbol; the others are prepared.
    """

    def test_an_unusable_file_refuses_its_symbol_and_the_other_runs(self, tmp_path):
        preparator = _preparator({
            'BTCUSD': [_write(tmp_path / 'BTCUSD_bad.parquet', [100.0, 0.0], [100.2, 0.0],
                              last=[100.1, 0.0])],
            'ETHUSD': [_write(tmp_path / 'ETHUSD_good.parquet', [2000.0, 2001.0],
                              [2000.2, 2001.2], last=[2000.1, 2001.1])],
        })
        ticks, _, _ = preparator.prepare_ticks([
            _requirement('btc_a', 'BTCUSD'), _requirement('btc_b', 'BTCUSD'),
            _requirement('eth', 'ETHUSD')])
        assert set(ticks) == {'eth'}
        for name in ('btc_a', 'btc_b'):
            assert 'BTCUSD_bad.parquet' in preparator.get_tick_data_failure(name)
        assert preparator.get_tick_data_failure('eth') is None

    def test_a_mount_without_any_traded_price_still_ends_the_batch(self, tmp_path):
        # The import refuses such a file, so reaching here means the pipeline lost the column:
        # a fault of ours, which ends the batch rather than refusing one symbol's scenarios
        preparator = _preparator({
            'BTCUSD': [_write(tmp_path / 'BTCUSD_nolast.parquet', [100.0], [100.2], last=[0.0])],
            'ETHUSD': [_write(tmp_path / 'ETHUSD_good.parquet', [2000.0], [2000.2],
                              last=[2000.1])],
        })
        with pytest.raises(TradedPriceMissingException,
                           match='none of the 1 mounted ticks carries a traded price'):
            preparator.prepare_ticks([
                _requirement('btc', 'BTCUSD'), _requirement('eth', 'ETHUSD')])

    def test_the_refused_scenario_carries_its_validation_error(self, monkeypatch):
        preparator = SharedDataPreparator.__new__(SharedDataPreparator)
        preparator._logger = MagicMock()

        def refusing_prepare_ticks(requirements):
            preparator._modelled_spread_summaries = {}
            preparator._modelled_spread_failures = {}
            preparator._tick_data_failures = {'btc': 'BTCUSD_bad.parquet: 1 of 2 ticks carry no '
                                                     'usable price'}
            return {}, {}, {}

        monkeypatch.setattr(preparator, 'prepare_ticks', refusing_prepare_ticks)
        monkeypatch.setattr(preparator, 'prepare_bars', lambda *args, **kwargs: ({}, {}, {}))
        scenario = SimpleNamespace(name='btc', scenario_index=0, validation_result=[])
        packages, _, _ = preparator.prepare_scenario_packages(
            MagicMock(), [scenario], broker_configs={})
        assert packages == {}
        (result,) = scenario.validation_result
        (finding,) = result.findings
        assert finding.check == 'tick_data_unusable'
        assert 'BTCUSD_bad.parquet' in finding.message
