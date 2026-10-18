"""
Tick Index Lookup Tests.

Every backtest batch asks the tick index which files cover a symbol's window. The lookup
converted two time strings per file on every call — 0.4 s for a symbol of 641 files, per symbol
and batch (measured 2026-10-15). The entries' times are now converted once per list
(`entry_time_bounds`), and the lookup must select exactly the files the former scan selected:
the scan is reproduced below as it stood, and both are asked the same windows, at the edges
above all.
"""

import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

import pandas as pd

from python.data_management.index.index_entry_times import entry_time_bounds
from python.data_management.index.tick_index_manager import TickIndexManager

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _entry(start: datetime, end: datetime, name: str, fraction: bool = False) -> Dict:
    """
    An index entry as the loaded index holds it: its times as naive ISO strings.

    Args:
        start: First tick of the file
        end: Last tick of the file
        name: File name
        fraction: Write the times with microseconds

    Returns:
        The entry
    """
    def iso(moment: datetime) -> str:
        naive = moment.replace(tzinfo=None)
        return naive.isoformat(timespec='microseconds') if fraction else naive.isoformat()
    return {'file': name, 'path': f'/data/{name}', 'start_time': iso(start),
            'end_time': iso(end)}


def _former_scan(entries: List[Dict], start: datetime, end: datetime) -> List[Path]:
    """The lookup as it stood: two conversions per entry, per call."""
    relevant = []
    for entry in entries:
        file_start = pd.to_datetime(entry['start_time'], utc=True)
        file_end = pd.to_datetime(entry['end_time'], utc=True)
        if file_start <= end and file_end >= start:
            relevant.append(Path(entry['path']))
    return relevant


def _archive(seed: int, count: int) -> List[Dict]:
    """
    Consecutive files of one symbol, some touching, some apart, with both time formats.

    Args:
        seed: Random seed
        count: How many files

    Returns:
        The entries in chronological order
    """
    rng = random.Random(seed)
    entries = []
    cursor = EPOCH
    for number in range(count):
        start = cursor + timedelta(seconds=rng.choice([0, 1, 3600, 86400]))
        end = start + timedelta(hours=rng.randint(1, 30), milliseconds=rng.randint(0, 999))
        entries.append(_entry(start, end, f'f{number}.parquet', fraction=rng.random() < 0.3))
        cursor = end
    return entries


def _manager(tmp_path, entries: List[Dict]) -> TickIndexManager:
    """A manager holding the given entries for kraken_spot BTCUSD."""
    manager = TickIndexManager(data_dir=str(tmp_path))
    manager.index = {'kraken_spot': {'BTCUSD': entries}}
    return manager


class TestTheBoundsOfTheEntries:

    def test_times_with_and_without_a_fraction_read_side_by_side(self):
        # Without the format, pandas takes it from the first string and refuses the second
        entries = [_entry(EPOCH, EPOCH + timedelta(hours=1), 'a'),
                   _entry(EPOCH + timedelta(hours=1, milliseconds=250),
                          EPOCH + timedelta(hours=2), 'b', fraction=True)]
        starts, ends = entry_time_bounds(entries)
        assert list(starts) == [pd.Timestamp(EPOCH),
                                pd.Timestamp(EPOCH + timedelta(hours=1, milliseconds=250))]
        assert ends[1] == pd.Timestamp(EPOCH + timedelta(hours=2))

    def test_an_entry_without_a_time_reads_as_no_time(self):
        entries = [{'start_time': None, 'end_time': None}]
        starts, ends = entry_time_bounds(entries)
        assert pd.isna(starts[0]) and pd.isna(ends[0])


class TestTheLookupSelectsWhatTheScanSelected:

    def test_random_windows(self, tmp_path):
        # Small on purpose: the former scan costs ~0.3 ms per conversion
        for seed in range(1, 4):
            entries = _archive(seed, 40)
            manager = _manager(tmp_path, entries)
            rng = random.Random(seed)
            for _ in range(25):
                start = EPOCH + timedelta(hours=rng.uniform(-24, 40 * 30))
                end = start + timedelta(hours=rng.uniform(0, 24 * 10))
                assert manager.get_relevant_files('kraken_spot', 'BTCUSD', start, end) == (
                    _former_scan(entries, start, end))

    def test_a_window_on_a_file_s_edges(self, tmp_path):
        entries = _archive(9, 12)
        manager = _manager(tmp_path, entries)
        for entry in entries:
            for moment in (entry['start_time'], entry['end_time']):
                edge = pd.Timestamp(moment, tz='UTC').to_pydatetime()
                for start, end in ((edge, edge), (edge - timedelta(hours=2), edge),
                                   (edge, edge + timedelta(hours=2))):
                    assert manager.get_relevant_files('kraken_spot', 'BTCUSD', start, end) == (
                        _former_scan(entries, start, end))

    def test_an_entry_without_a_time_is_never_selected(self, tmp_path):
        # The scan raised on it; a file with no ticks has nothing to read
        entries = [_entry(EPOCH, EPOCH + timedelta(hours=1), 'a'),
                   {'file': 'empty', 'path': '/data/empty', 'start_time': None,
                    'end_time': None}]
        manager = _manager(tmp_path, entries)
        assert manager.get_relevant_files('kraken_spot', 'BTCUSD', EPOCH,
                                          EPOCH + timedelta(days=1)) == [Path('/data/a')]
