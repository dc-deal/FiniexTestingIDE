"""Probe: what do the records a session writes as it goes cost, on a given disk?

The question arose in #476 (sealed trading days), which owns the probe. A session books each
trading day into the results ledger as the day seals — it rewrites its ledger fragment whole, once
per boundary check — streams every closed trade and every diagnostics row as a flushed line, lets
its log buffer go of a sealed day's ordinary lines, and seals each stream's day into a parquet day
record. The answer moves with the machine — this development container writes /app through a bridge
to a Windows drive, a server writes to a local disk — and with the code: the ledger row grows with
every column. That is why this is re-runnable rather than a one-shot script.

Writes only into a temporary folder it creates under each --dir and removes again; no run, ledger
or carry-over of the operator is touched. Like every start of the framework, it leaves its header
in the global log.

Recorded results:
  2026-10-26 11:51 UTC — development container (WSL2): /tmp on the container's overlay, /app/logs on
      the Windows drive behind the bridge; 15 writes, 2,000 lines each. The ledger fragment of 1 / 7
      / 31 days: 9.0 / 9.4 / 7.7 ms median on the overlay, 99 / 104 / 101 ms behind the bridge —
      the write's own cost, hardly the rows'. A trade line (40 fields): 15 µs median, p99 41 µs, on
      the overlay; 218 µs, p99 503 µs, max 7.3 ms behind the bridge. A diagnostics line (10 fields):
      6 µs, p99 17 µs; 168 µs, p99 388 µs. The log buffer let go of 10,000 lines in 2.6 ms and of
      100,000 in 25 ms. Not yet measured on a server.
      Measured inside the suite the same day, the trim read 0.08 ms: the suite's configuration
      buffers no INFO line at all, so the figure measured an empty buffer. Run the probe as a plain
      script.
  2026-10-26 15:30 and 15:32 UTC — the same container, the day seal added: a day of 1,440 records
      of about 3.9 KB each (a day of state frames at an M1 cadence) sealed in 140 ms median with
      the record times parsed by dateutil; parsed by `parse_iso_datetime`, 81 and 94 ms median in
      two runs, max 150 ms, on the overlay, and 172 ms behind the bridge. A day of 10 records:
      2-4 ms, and 70 ms behind the bridge. Most of a large day's seal is parsing its lines.
  2026-10-27 08:43 UTC — the same container, the day record and the ledger fragment now written
      durably (fsync of the file before its rename, of its folder after): the fragment 12-14 ms on
      the overlay (was 8-10) and 105-115 ms behind the bridge (was about 95); a day of 10 records
      6 ms (was 2-4), of 1,440 records 98 ms (was 81-94, within the runs' spread), 189 ms behind
      the bridge.

Usage:
    python python/experiments/cost_probes/probe_run_record_cost.py [--dir PATH ...] [--repeats N]
"""

import argparse
import shutil
import statistics
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

from python.framework.constants.run_layout_constants import RUN_TYPE_AUTOTRADER
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.reporting.builders.booking_period_builder import (
    PeriodSnapshot,
    derive_booking_period,
)
from python.framework.reporting.io.run_stream_sealer import RunStreamSealer
from python.framework.reporting.store.run_results_ledger import RunResultsLedger
from python.framework.store.entry_members.run_entry_writer import RunEntryWriter
from python.framework.types.log_level import LogLevel
from python.framework.types.run_results_types import (
    BookingPeriod,
    PeriodCloseReason,
    RunProvenance,
)
from python.framework.types.store_member_types import RunEntryMemberId

_MONDAY = datetime(2026, 1, 5, tzinfo=timezone.utc)
_FRAGMENT_DAYS = (1, 7, 31)
# A trade line as wide as the trade stream's row, a diagnostics line as a strategy might write it
_TRADE_ROW: Dict[str, object] = {
    f'field_{i}': (i * 1.2345 if i % 3 else f'value_{i}') for i in range(40)}
_DIAGNOSTICS_ROW: Dict[str, object] = {f'column_{i}': i * 0.5 for i in range(10)}
_TRIM_LINES = (10_000, 100_000)
# One sealed day of a stream, each record about as wide as a state frame of a session with a handful
# of workers: a few records, as a day of trades holds, and a day of frames at an M1 cadence
_SEAL_DAY_RECORDS = (10, 1440)
_FRAME_ROW: Dict[str, object] = {
    'frame_time': '2026-01-05T12:00:00+00:00', **{f'output_{i}': i * 1.2345 for i in range(150)}}


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


def _provenance() -> RunProvenance:
    """
    A session's provenance, as its sealed rows carry it.

    Returns:
        The provenance
    """
    return RunProvenance(
        param_hash='probe', status='ok', error=None, run_id='20260105_000000_costprobe',
        run_timestamp=_MONDAY, scenario_set_name='cost_probe', app_version='probe',
        git_commit='probe', git_branch='probe', git_dirty=False,
        decision_logic_type='CORE/probe', decision_version='1.0.0', worker_versions={},
        strategy_config_json='{}', symbols=['BTCUSD'], data_broker_type='kraken_spot',
        sweep_id=None, sweep_params=None, sweep_objective=None, sweep_maximize=None,
        run_type=RUN_TYPE_AUTOTRADER, deployment_id='deploy_cost_probe')


def _day(number: int) -> BookingPeriod:
    """
    One sealed trading day with nothing traded.

    Args:
        number: The day's number, 1-based

    Returns:
        Its booking period
    """
    return derive_booking_period(
        'session', number, _MONDAY + timedelta(days=number - 1), _MONDAY + timedelta(days=number),
        PeriodCloseReason.ANCHOR, [],
        PeriodSnapshot(currency='USD', final_equity=1_000.0, account_max_drawdown=0.0,
                       max_equity=1_000.0, account_max_dd_pct=0.0), tick_count=10)


def _ledger_write_ms(folder: Path, days: int, repeats: int) -> List[float]:
    """
    Time the rewrite of a session's ledger fragment at a seal.

    Args:
        folder: Where the ledger is written
        days: How many sealed days the fragment holds
        repeats: How many timed writes

    Returns:
        One duration per write, milliseconds
    """
    ledger = RunResultsLedger(folder / 'run_results')
    provenance = _provenance()
    periods = [_day(number) for number in range(1, days + 1)]
    ledger.write_sealed_periods(provenance, periods)      # the first write creates the folder
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        ledger.write_sealed_periods(provenance, periods)
        samples.append((time.perf_counter() - started) * 1000)
    return samples


def _stream_line_us(folder: Path, member: RunEntryMemberId, row: Dict[str, object],
                    lines: int, **names: str) -> List[float]:
    """
    Time one appended and flushed line of a run stream.

    Args:
        folder: The run directory the stream is written into
        member: The stream's declared member
        row: The line's content
        lines: How many timed lines
        names: The variable parts of the member's path

    Returns:
        One duration per line, microseconds
    """
    appender = RunEntryWriter(folder, RUN_TYPE_AUTOTRADER).open_stream(
        member, 'cost_probe', None, stops='-', header_fields={'unit_name': 'probe'}, **names)
    samples = []
    for _ in range(lines):
        started = time.perf_counter()
        appender.write(row)
        samples.append((time.perf_counter() - started) * 1e6)
    appender.close()
    return samples


def _seal_ms(folder: Path, lines: int, repeats: int) -> List[float]:
    """
    Time the seal of one trading day of one stream: its file read, its day record written, the file
    started afresh.

    Args:
        folder: Where the run directories are made
        lines: How many records the day holds
        repeats: How many timed seals, each of a fresh day

    Returns:
        One duration per seal, milliseconds
    """
    samples = []
    for repeat in range(repeats):
        sealer = RunStreamSealer(_QuietLogger())
        writer = RunEntryWriter(folder / f'seal_{lines}_{repeat}', RUN_TYPE_AUTOTRADER,
                                streams=sealer)
        stream = writer.open_stream(RunEntryMemberId.STATE_FRAMES, 'cost_probe', None, stops='-')
        for _ in range(lines):
            stream.write(_FRAME_ROW)
        day = _MONDAY.date() + timedelta(days=repeat)
        started = time.perf_counter()
        sealer.seal(day, day + timedelta(days=1))
        samples.append((time.perf_counter() - started) * 1000)
        stream.close()
    return samples


def _trim_ms(lines: int) -> float:
    """
    Time the log buffer letting go of a sealed day's ordinary lines.

    Args:
        lines: How many lines the buffer holds, one warning per thousand

    Returns:
        The trim's duration, milliseconds
    """
    logger = ScenarioLogger(scenario_set_name='cost_probe', scenario_name='trim',
                            run_timestamp=datetime.now(timezone.utc), run_id='cost_probe')
    for i in range(lines):
        if i % 1000 == 0:
            logger.warning(f'warning {i}')
        else:
            logger.info(f'an ordinary line {i}')
    started = time.perf_counter()
    logger.trim_buffer_below(LogLevel.WARNING)
    return (time.perf_counter() - started) * 1000


def _ms(samples: List[float]) -> str:
    """
    Durations in milliseconds as one table cell.

    Args:
        samples: Durations in milliseconds

    Returns:
        Median, minimum and maximum, as one cell
    """
    return (f'median {statistics.median(samples):7.2f} ms · min {min(samples):7.2f} · '
            f'max {max(samples):7.2f} · n={len(samples)}')


def _us(samples: List[float]) -> str:
    """
    Durations in microseconds as one table cell.

    Args:
        samples: Durations in microseconds

    Returns:
        Median, 99th percentile and maximum, as one cell
    """
    ordered = sorted(samples)
    p99 = ordered[int(len(ordered) * 0.99)]
    return (f'median {statistics.median(samples):8.1f} µs · p99 {p99:8.1f} · '
            f'max {max(samples):9.1f} · n={len(samples)}')


def _measure(root: Path, repeats: int, lines: int) -> None:
    """
    Measure the disk-bound costs under one directory, in a folder removed again afterwards.

    Args:
        root: The directory whose disk is measured
        repeats: Timed writes per fragment size
        lines: Timed lines per stream
    """
    folder = Path(tempfile.mkdtemp(dir=root, prefix='cost_probe_'))
    try:
        print(f'\n{root}')
        for days in _FRAGMENT_DAYS:
            samples = _ledger_write_ms(folder, days, repeats)
            print(f'  ledger fragment, {days:>2} day(s)    {_ms(samples)}')
        print(f'  trade line, 40 fields         '
              f'{_us(_stream_line_us(folder, RunEntryMemberId.TRADES, _TRADE_ROW, lines))}')
        diagnostics = _stream_line_us(folder, RunEntryMemberId.DIAGNOSTICS_ROWS,
                                      _DIAGNOSTICS_ROW, lines, sink='probe')
        print(f'  diagnostics line, 10 fields   {_us(diagnostics)}')
        for records in _SEAL_DAY_RECORDS:
            print(f'  seal a day, {records:>5,} records   {_ms(_seal_ms(folder, records, repeats))}')
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def main() -> None:
    """Measure under every --dir, then the log buffer, and print the table."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--dir', action='append', type=Path,
                        help='a directory whose disk to measure; repeat for several '
                             '(default: the system temporary directory)')
    parser.add_argument('--repeats', type=int, default=15, help='timed writes per fragment size')
    parser.add_argument('--lines', type=int, default=2000, help='timed lines per stream')
    args = parser.parse_args()

    print(f'Run record cost probe · {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC')
    for root in args.dir or [Path(tempfile.gettempdir())]:
        _measure(root, args.repeats, args.lines)
    print('\nlog buffer (memory, not disk)')
    for lines in _TRIM_LINES:
        print(f'  trim {lines:>7} lines          {_trim_ms(lines):7.2f} ms')


if __name__ == '__main__':
    main()
