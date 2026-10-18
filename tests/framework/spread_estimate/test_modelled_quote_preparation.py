"""
Modelled Quote in the Tick Preparation Tests (#602).

The preparation loads a symbol's files once and cuts every scenario from that frame. These tests
drive it over a small archive of one file without a quote and one with, with the day estimates
handed in, and hold it to what leaves for the scenario process: the modelled quote on the ticks
without one and only there, the recorded values under a factor of 0, a shared frame left as it
was, and a scenario refused by name when its estimates cannot be had.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List
from unittest.mock import MagicMock

import pandas as pd
import pytest

import python.framework.data_preparation.shared_data_preparator as shared_data_preparator
from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.data_management.index.tick_index_manager import TickIndexManager
from python.framework.autotrader.startup.autotrader_data_preparer import build_scenario_from_config
from python.framework.data_preparation.shared_data_preparator import SharedDataPreparator
from python.framework.types.data_deficiency_types import DataDeficiency
from python.framework.types.process_data_types import TickRequirement
from python.framework.types.spread_estimate_types import SpreadDayEstimate

_DAY = datetime(2021, 3, 1, tzinfo=timezone.utc)
_STAMP = '%Y-%m-%dT%H:%M:%S'
_TICKS = 6


def _write_file(folder: Path, name: str, start: datetime, quote: float) -> Dict[str, object]:
    """
    One tick file at price 100, every ten minutes, and its index entry.

    Args:
        folder: Where the file goes
        name: File name
        start: Its first tick
        quote: Half the recorded spread; 0 writes a file without a quote

    Returns:
        The index entry
    """
    times = [start + timedelta(minutes=10 * i) for i in range(_TICKS)]
    frame = pd.DataFrame({
        'timestamp': times,
        'time_msc': [int(t.timestamp() * 1000) for t in times],
        'bid': [100.0 - quote] * _TICKS,
        'ask': [100.0 + quote] * _TICKS,
        'last': [100.0] * _TICKS,
        'volume': [0.01] * _TICKS,
    })
    path = folder / name
    frame.to_parquet(path)
    return {'path': str(path), 'start_time': times[0].strftime(_STAMP),
            'end_time': times[-1].strftime(_STAMP), 'tick_count': _TICKS,
            'deficiencies': [] if quote else [DataDeficiency.NO_QUOTE]}


def _preparator(entries: List[Dict[str, object]]) -> SharedDataPreparator:
    """The preparator over the given archive, its index built by hand."""
    index = TickIndexManager.__new__(TickIndexManager)
    index.index = {'kraken_spot': {'ETHUSD': entries}}
    index.logger = MagicMock()
    preparator = SharedDataPreparator.__new__(SharedDataPreparator)
    preparator._logger = MagicMock()
    preparator.tick_index_manager = index
    return preparator


def _estimates(monkeypatch, mean_bps: float = 4.0, error: Exception = None) -> List[int]:
    """
    Hand the preparation a day estimate for the day before the archive, or a failure.

    Returns:
        A list that grows by one per cache built, so a test can tell it was not asked
    """
    built: List[int] = []

    class _Cache:
        def __init__(self, **kwargs):
            built.append(1)

        def get_day_estimates(self, broker_type, symbol):
            if error is not None:
                raise error
            return [SpreadDayEstimate(day=(_DAY - timedelta(days=1)).date(), pairs=100,
                                      spread_median_bps=mean_bps, spread_p90_bps=mean_bps,
                                      spread_mean_bps=mean_bps)]

    monkeypatch.setattr(shared_data_preparator, 'SpreadEstimateCache', _Cache)
    return built


def _req(name: str, factor: float) -> TickRequirement:
    return TickRequirement(scenario_name=name, broker_type='kraken_spot', symbol='ETHUSD',
                           start_time=_DAY, end_time=_DAY + timedelta(days=2),
                           modelled_spread_factor=factor)


@pytest.fixture
def archive(tmp_path):
    """Day 1 recorded no quote, day 2 recorded one of 99.9 / 100.1."""
    return [_write_file(tmp_path, 'day1.parquet', _DAY, 0.0),
            _write_file(tmp_path, 'day2.parquet', _DAY + timedelta(days=1), 0.1)]


class TestTheTicksWithoutAQuoteAreGivenOne:

    def test_only_the_ticks_without_a_quote_get_the_modelled_quote(self, archive, monkeypatch):
        _estimates(monkeypatch, mean_bps=4.0)
        preparator = _preparator(archive)
        ticks, _, _ = preparator.prepare_ticks([_req('modelled', 1.0)])

        quotes = [(t['bid'], t['ask']) for t in ticks['modelled']]
        assert quotes[:_TICKS] == [(pytest.approx(99.98), pytest.approx(100.02))] * _TICKS
        assert quotes[_TICKS:] == [(pytest.approx(99.9), pytest.approx(100.1))] * _TICKS

        summary = preparator.get_modelled_spread('modelled')
        assert (summary.ticks_without_quote, summary.ticks_modelled) == (_TICKS, _TICKS)
        assert summary.median_spread_bps == pytest.approx(4.0)
        first, last = ticks['modelled'][0]['time_msc'], ticks['modelled'][_TICKS - 1]['time_msc']
        assert summary.quote_runs == ((first, last),)

    def test_factor_zero_leaves_every_tick_as_recorded_and_asks_no_cache(self, archive,
                                                                         monkeypatch):
        built = _estimates(monkeypatch)
        preparator = _preparator(archive)
        ticks, _, _ = preparator.prepare_ticks([_req('unmodelled', 0.0)])

        assert [(t['bid'], t['ask']) for t in ticks['unmodelled'][:_TICKS]] == (
            [(100.0, 100.0)] * _TICKS)
        assert built == [], 'factor 0 needs no estimate'
        summary = preparator.get_modelled_spread('unmodelled')
        assert (summary.factor, summary.ticks_modelled, summary.quote_runs) == (0.0, 0, ())

    def test_a_frame_shared_by_two_scenarios_is_left_as_it_was(self, archive, monkeypatch):
        _estimates(monkeypatch)
        ticks, _, _ = _preparator(archive).prepare_ticks(
            [_req('modelled', 1.0), _req('unmodelled', 0.0)])

        assert ticks['modelled'][0]['ask'] == pytest.approx(100.02)
        assert ticks['unmodelled'][0]['ask'] == 100.0, 'the first scenario changed the frame'

    def test_a_symbol_whose_every_file_has_a_quote_is_not_modelled(self, archive, monkeypatch):
        built = _estimates(monkeypatch)
        preparator = _preparator(archive[1:])
        preparator.prepare_ticks([_req('quoted', 1.0)])

        assert preparator.get_modelled_spread('quoted') is None
        assert built == []


class TestAScenarioWhoseEstimatesFailIsRefused:

    def test_it_gets_no_ticks_and_a_reason_naming_the_remedy(self, archive, monkeypatch):
        _estimates(monkeypatch, error=OSError('unreadable tick file'))
        preparator = _preparator(archive)
        ticks, _, _ = preparator.prepare_ticks([_req('modelled', 1.0), _req('unmodelled', 0.0)])

        assert 'modelled' not in ticks
        reason = preparator.get_modelled_spread_failure('modelled')
        assert 'spread_estimate_cli.py build kraken_spot ETHUSD' in reason
        assert 'modelled_spread_factor' in reason
        assert len(ticks['unmodelled']) == 2 * _TICKS, 'a factor-0 scenario still runs'
        assert preparator.get_modelled_spread_failure('unmodelled') is None


class TestAMockSessionReplaysTheTicksAsRecorded:

    def test_its_scenario_models_no_spread(self):
        """It rehearses the live path, whose venue always sends a quote of its own."""
        config = load_autotrader_config('configs/autotrader_profiles/mock/sentiment_mock_test.json')
        assert build_scenario_from_config(config).get_modelled_spread_factor() == 0.0
