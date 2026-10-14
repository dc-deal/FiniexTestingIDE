"""
FiniexTestingIDE - A Tick-Count Window Reads What It Needs (#485, #585)

A scenario that asks for a number of ticks rather than a time span — every AutoTrader mock
profile does — read every tick file from its start to the end of the archive, sorted them all,
and kept max_ticks. The archive grows with every import, so every such run grew slower. The read
now stops where the index's tick counts reach max_ticks; what comes back must be exactly what
reading the whole archive yields, ties and overlapping files included.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple
from unittest.mock import MagicMock

import pandas as pd

import python.framework.data_preparation.shared_data_preparator as shared_data_preparator
from python.data_management.index.tick_index_manager import TickIndexManager
from python.framework.data_preparation.shared_data_preparator import SharedDataPreparator
from python.framework.types.process_data_types import TickRequirement

_DAY = datetime(2026, 3, 2, tzinfo=timezone.utc)
_STAMP = '%Y-%m-%dT%H:%M:%S'


def _write_file(folder: Path, name: str, start: datetime, count: int,
                step_s: float, price: float) -> Dict[str, object]:
    """
    One tick file of the synthetic archive, and its index entry.

    Args:
        folder: Where the file goes
        name: File name
        start: Its first tick
        count: How many ticks it holds
        step_s: Seconds between two ticks
        price: The traded price of every tick, so a test can tell the files apart

    Returns:
        The index entry
    """
    times = [start + timedelta(seconds=i * step_s) for i in range(count)]
    frame = pd.DataFrame({
        'timestamp': times,
        'time_msc': [int(t.timestamp() * 1000) for t in times],
        'bid': [price - 0.5] * count,
        'ask': [price + 0.5] * count,
        'last': [price] * count,
        'volume': [0.01] * count,
    })
    path = folder / name
    frame.to_parquet(path)
    return {'path': str(path), 'start_time': times[0].strftime(_STAMP),
            'end_time': times[-1].strftime(_STAMP), 'tick_count': count}


def _archive(folder: Path) -> List[Dict[str, object]]:
    """
    Six days of ticks, one file a day, plus a file that overlaps day 2 and repeats one of its
    stamps exactly — the case where reading fewer files could reorder ties.

    Args:
        folder: Where the files go

    Returns:
        The index entries, in the index's own order
    """
    entries = [_write_file(folder, f'BTCUSD_day{n}.parquet', _DAY + timedelta(days=n), 100,
                           600.0, 50000.0 + n) for n in range(6)]
    entries.insert(2, _write_file(folder, 'BTCUSD_overlap.parquet',
                                  _DAY + timedelta(days=1, hours=5), 20, 600.0, 49000.0))
    return entries


def _preparator(entries: List[Dict[str, object]]) -> SharedDataPreparator:
    """
    The shared preparator over the synthetic archive, its index built by hand.

    Args:
        entries: The archive's index entries

    Returns:
        The preparator
    """
    index = TickIndexManager.__new__(TickIndexManager)
    index.index = {'kraken_spot': {'BTCUSD': entries}}
    index.logger = MagicMock()
    preparator = SharedDataPreparator.__new__(SharedDataPreparator)
    preparator._logger = MagicMock()
    preparator.tick_index_manager = index
    return preparator


def _counting_reads(monkeypatch) -> List[Path]:
    """
    Record every tick file the preparator opens.

    Args:
        monkeypatch: pytest's monkeypatch

    Returns:
        The list the opened paths are appended to
    """
    opened: List[Path] = []
    real = shared_data_preparator.read_tick_parquet

    def counting(path):
        opened.append(Path(path))
        return real(path)

    monkeypatch.setattr(shared_data_preparator, 'read_tick_parquet', counting)
    return opened


def _ticks(preparator: SharedDataPreparator, start: datetime, **window) -> Tuple:
    """
    Prepare one requirement and return its ticks.

    Args:
        preparator: The preparator
        start: The window's start
        window: max_ticks, or end_time

    Returns:
        The prepared ticks
    """
    ticks, _, _ = preparator.prepare_ticks([TickRequirement(
        scenario_name='window', broker_type='kraken_spot', symbol='BTCUSD',
        start_time=start, **window)])
    return ticks['window']


class TestATickCountWindowReadsOnlyWhatItNeeds:

    def test_it_stops_where_the_index_counts_reach_max_ticks(self, tmp_path, monkeypatch):
        preparator = _preparator(_archive(tmp_path))
        opened = _counting_reads(monkeypatch)

        _ticks(preparator, _DAY + timedelta(hours=6), max_ticks=150)

        # Day 0 straddles the start and is not counted; days 1 and 2 (and the overlap file
        # inside them) reach 150 — days 3 to 5 are never opened
        assert {p.name for p in opened} == {
            'BTCUSD_day0.parquet', 'BTCUSD_day1.parquet', 'BTCUSD_overlap.parquet',
            'BTCUSD_day2.parquet'}

    def test_an_archive_too_short_is_read_to_its_end(self, tmp_path, monkeypatch):
        preparator = _preparator(_archive(tmp_path))
        opened = _counting_reads(monkeypatch)

        ticks = _ticks(preparator, _DAY + timedelta(hours=6), max_ticks=10_000)

        assert len(opened) == 7
        assert len(ticks) == 584  # every tick from the start on: 64 + 4 x 100 + 100 + 20


class TestTheTicksAreTheSameAsReadingEverything:

    def test_from_inside_a_file(self, tmp_path):
        preparator = _preparator(_archive(tmp_path))
        start = _DAY + timedelta(hours=6)

        bounded = _ticks(preparator, start, max_ticks=150)
        everything = _ticks(preparator, start, end_time=_DAY + timedelta(days=7))

        assert bounded == everything[:150]

    def test_across_the_overlap_and_its_repeated_stamp(self, tmp_path):
        preparator = _preparator(_archive(tmp_path))
        start = _DAY + timedelta(days=1, hours=4)

        bounded = _ticks(preparator, start, max_ticks=40)
        everything = _ticks(preparator, start, end_time=_DAY + timedelta(days=7))

        assert bounded == everything[:40]
        assert any(t['last'] == 49000.0 for t in bounded)
