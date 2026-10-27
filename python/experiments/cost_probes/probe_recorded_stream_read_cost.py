"""Probe: what does reading a unit's recorded streams cost, on a given disk?

The question arose in #580 (a unit's recorded streams over time), which owns the probe. A viewer
following a running session asks for the unit's timeline every second or two and for a window of
its records whenever the timeline moved; a session thirty days in holds twenty-nine sealed trading
days and the file of the day it is in. What a read costs is mostly what it asks of the file system,
one call per sealed day or several, so the probe counts the file status calls of each read beside
its duration: the count holds on every machine, the duration moves with the disk — this development
container reads /app through a bridge to a Windows drive, a server reads a local disk.

Writes only into a temporary folder it creates under each --dir and removes again; no run, ledger
or carry-over of the operator is touched. Like every start of the framework, it leaves its header
in the global log.

Recorded results:
  2026-10-27 18:04 UTC — development container (WSL2): /tmp on the container's overlay, /app/logs on
      the Windows drive behind the bridge; a stream at day 30 of a session — 29 sealed days of 1,440
      frames and 800 in the running file, about 1.5 KB a frame. Before any change to the reads: the
      timeline 39.7 ms on its first read and 3.2 ms after it on the overlay, 1,076 and 1,056 ms behind
      the bridge, with 237 status calls, each sealed day asked up to five times; the latest frame
      1.6 ms and 622 ms (150 calls); a page of 2,000 from the start 148 ms and 728 ms, a seek
      mid-stream 62 ms and 648 ms, a page in the running day 29 ms and 549 ms (119 calls each). A
      status call cost about 4.4 ms behind the bridge during the run, 1.2 ms in a quiet minute the
      same hour. Not yet measured on a server.

Usage:
    python python/experiments/cost_probes/probe_recorded_stream_read_cost.py [--dir PATH ...]
        [--days N] [--repeats N]
"""

import argparse
import contextlib
import functools
import os
import shutil
import statistics
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from python.framework.constants.run_layout_constants import RUN_TYPE_AUTOTRADER
from python.framework.reporting.io.run_stream_sealer import RunStreamSealer
from python.framework.store.entry_members.run_dir_reader import RunDirReader
from python.framework.store.entry_members.run_entry_writer import RunEntryWriter
from python.framework.types.run_stream_types import StreamTimeWindow
from python.framework.types.store_member_types import RunEntryMemberId

_START = datetime(2026, 1, 5, tzinfo=timezone.utc)
# A day of state frames at an M1 cadence, and the day the session is in, a little past its middle
_FRAMES_PER_DAY = 1440
_RUNNING_DAY_FRAMES = 800
# The most records a window serves — the ceiling of the window route
_PAGE_LIMIT = 2000
# A frame about as wide as one of a session with a handful of workers, about 1.5 KB as written
_OUTPUTS: Dict[str, float] = {f'output_{i}': i * 1.2345 for i in range(60)}


class _QuietLogger:
    """Takes the seal's lines and keeps none — the probe prints its own table."""

    def info(self, message: str) -> None:
        """
        Drop an info line.

        Args:
            message: The line
        """

    def error(self, message: str) -> None:
        """
        Drop an error line.

        Args:
            message: The line
        """


class _StatCounter:
    """Counts the file status calls made while it is installed."""

    def __init__(self) -> None:
        self._calls = 0

    def count(self) -> None:
        """Count one call."""
        self._calls += 1

    def get_calls(self) -> int:
        """
        Returns:
            The calls counted
        """
        return self._calls


@contextlib.contextmanager
def _counting_stats() -> Iterator[_StatCounter]:
    """
    Count every file status call — a path's stat, is_file and is_dir all make one — while the
    block runs. A folder listing's entry types and a parquet file opened by pyarrow are not
    counted: neither asks for a status.

    Returns:
        The counter, filled when the block ends
    """
    counter = _StatCounter()
    original = os.stat

    def counted(*args: Any, **kwargs: Any) -> os.stat_result:
        counter.count()
        return original(*args, **kwargs)

    os.stat = counted
    try:
        yield counter
    finally:
        os.stat = original


def _frame(moment: datetime) -> Dict[str, object]:
    """
    One state frame as the stream holds it.

    Args:
        moment: Its frame time

    Returns:
        The frame
    """
    return {'record_kind': 'frame', 'frame_time': moment.isoformat(), 'written_for': 'cadence',
            'worker_outputs': _OUTPUTS}


def _build(run_dir: Path, sealed_days: int) -> None:
    """
    Write a session's state-frame stream: its sealed trading days of an M1 cadence, then the day
    it is in.

    Args:
        run_dir: The run directory to write
        sealed_days: How many days are sealed before the running one
    """
    sealer = RunStreamSealer(_QuietLogger())
    writer = RunEntryWriter(run_dir, RUN_TYPE_AUTOTRADER, streams=sealer)
    stream = writer.open_stream(
        RunEntryMemberId.STATE_FRAMES, 'cost_probe', None, stops='-',
        header_fields={'unit_name': 'probe', 'cadence_timeframe': 'M1', 'cadence_s': 60.0})
    for day in range(sealed_days + 1):
        frames = _FRAMES_PER_DAY if day < sealed_days else _RUNNING_DAY_FRAMES
        opened = _START + timedelta(days=day)
        for minute in range(frames):
            stream.write(_frame(opened + timedelta(minutes=minute, milliseconds=412)))
        if day < sealed_days:
            sealer.seal(opened.date(), (opened + timedelta(days=1)).date())
    stream.close()


def _timed(read: Callable[[], object], repeats: int) -> Tuple[List[float], int]:
    """
    Time a read, and count the file status calls of its last repeat.

    Args:
        read: The read
        repeats: How many timed reads

    Returns:
        (one duration per read in milliseconds, the status calls of one read)
    """
    samples: List[float] = []
    calls = 0
    for _ in range(repeats):
        with _counting_stats() as counter:
            started = time.perf_counter()
            read()
            samples.append((time.perf_counter() - started) * 1000)
        calls = counter.get_calls()
    return samples, calls


def _cell(samples: List[float], calls: int) -> str:
    """
    Durations and a status-call count as one table cell.

    Args:
        samples: Durations in milliseconds
        calls: The status calls of one read

    Returns:
        Median, minimum and maximum with the count, as one cell
    """
    return (f'median {statistics.median(samples):8.1f} ms · min {min(samples):8.1f} · '
            f'max {max(samples):8.1f} · {calls:>4} stat calls')


def _page(reader: RunDirReader, since: Optional[datetime], limit: int) -> Callable[[], object]:
    """
    A window read from an instant to the end of the stream.

    Args:
        reader: The run directory
        since: Where the window starts, None for the stream's start
        limit: The most records the page holds

    Returns:
        The read
    """
    window = StreamTimeWindow(since=since)
    return lambda: reader.read_stream_page(RunEntryMemberId.STATE_FRAMES, window, limit)


def _measure(root: Path, sealed_days: int, repeats: int) -> None:
    """
    Measure the reads under one directory, in a folder removed again afterwards.

    Args:
        root: The directory whose disk is measured
        sealed_days: How many sealed days the stream holds
        repeats: Timed reads per measurement
    """
    folder = Path(tempfile.mkdtemp(dir=root, prefix='cost_probe_'))
    try:
        run_dir = folder / 'run'
        started = time.perf_counter()
        _build(run_dir, sealed_days)
        print(f'\n{root} — {sealed_days} sealed day(s) and the running one, written in '
              f'{time.perf_counter() - started:.1f} s')
        reader = RunDirReader(run_dir)
        timeline = functools.partial(reader.read_stream_overview, RunEntryMemberId.STATE_FRAMES)
        cold, cold_calls = _timed(timeline, 1)
        print(f'  timeline, first read           {_cell(cold, cold_calls)}')
        print(f'  timeline                       {_cell(*_timed(timeline, repeats))}')
        ends = functools.partial(reader.read_stream_ends, RunEntryMemberId.STATE_FRAMES)
        print(f'  latest frame (its two ends)    {_cell(*_timed(ends, repeats))}')
        middle = _START + timedelta(days=sealed_days // 2, hours=12)
        running = _START + timedelta(days=sealed_days)
        print(f'  page from the start            '
              f'{_cell(*_timed(_page(reader, None, _PAGE_LIMIT), repeats))}')
        print(f'  seek mid-stream, limit 1       '
              f'{_cell(*_timed(_page(reader, middle, 1), repeats))}')
        print(f'  page in the running day        '
              f'{_cell(*_timed(_page(reader, running, _PAGE_LIMIT), repeats))}')
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def main() -> None:
    """Measure under every --dir and print the table."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--dir', action='append', type=Path,
                        help='a directory whose disk to measure; repeat for several '
                             '(default: the system temporary directory)')
    parser.add_argument('--days', type=int, default=29,
                        help='sealed days before the running one (default: day 30 of a session)')
    parser.add_argument('--repeats', type=int, default=5, help='timed reads per measurement')
    args = parser.parse_args()

    print(f'Recorded stream read cost probe · {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC')
    for root in args.dir or [Path(tempfile.gettempdir())]:
        _measure(root, args.days, args.repeats)


if __name__ == '__main__':
    main()
