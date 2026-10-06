"""One-shot migration: move MT5 arrival times in US standard time onto the server's real clock.

An MT5 time_msc is the broker server's wall clock, and that server runs on New York time
plus 7 hours: UTC+2 while New York keeps standard time, UTC+3 while it keeps daylight
time. The importer converted it with a fixed -3 h, which is right for the daylight half of
the year only. The V3 restoration (restore_collected_msc_v3.py), as run in August 2026,
aligned collected_msc, the arrival time, to that converted event time. So in every file
whose ticks lie in US standard time (2025-11-02 to 2026-03-08) the arrival time is one hour
early, exactly as the event time was (measured 2026-10-05: 1,640 such files, every one of
them a V3 hour snap of -3 h).

The importer now converts time_msc by the rule (#562). Left as they are, those files would
then put the arrival one hour BEFORE the event, and the importer refuses a file whose
arrival time sits more than five minutes from the event time. Measured on a EURUSD file
opened 2026-01-16, arrival minus event time over all ticks:

    event time by the fixed -3 h, as imported so far           -999 ... +897 ms
    event time by the rule                               -3,600,999 ... -3,599,103 ms
    event time by the rule, collected_msc + 3,600,000          -999 ... +897 ms

Per *_ticks.json in the directory:

  select  only a file whose metadata names the broker type mt5 (broker_type, or the legacy
          data_collector), that carries a V3 block with version 3, method hour_snap and
          shift_ms [-10800000], and that has no V4 marker yet. Every other file is skipped
          with its reason. An mt5 file under another V3 block whose first tick lies in
          standard time is measured by the rule as well, and flagged when the importer
          will refuse it: nothing here corrects it
  decide  from the file's own ticks, through the importer's rule (server_clock in
          market_config.json): every tick in daylight time -> unchanged; every tick in
          standard time -> collected_msc + 3,600,000; a mix, a stamp in an hour the zone
          repeats or skips, or any other offset -> refused, never written
  check   per anchor segment, the smallest arrival minus event time after the shift lies
          inside the importer's window, or the file is refused
  write   (--apply) streams the file into <file>.part, checks that copy — exact size,
          re-scanned values, lag window, loaded once as JSON — and only then moves it
          over the original

Only the digits of every "collected_msc" value change, plus this marker inserted right
after the closing line of the V3 block; the rule is written from the configuration:

    "collected_msc_server_clock_correction": {
      "version": 4,
      "applied": "<UTC, %Y-%m-%dT%H:%M:%SZ>",
      "rule": "America/New_York+7h",
      "shift_ms": 3600000
    },

Every other byte stays as it was. A file carrying the marker is skipped, so an interrupted
run is simply run again. A file left unchanged gets no marker: its decision follows from its
own ticks every time.

Runs on EXTRACTED copies in a working directory — never inside an archive: extract the
members first, correct them, then import or re-pack them. Single-use: once the extracted
files are corrected, it has no further purpose.

Dry-run by default — pass --apply to write. Exits 1 when any file is refused or fails a check.

Usage:
    python python/experiments/restore_collected_msc_v4.py --dir <extracted folder>
    python python/experiments/restore_collected_msc_v4.py --dir <extracted folder> --verbose
    python python/experiments/restore_collected_msc_v4.py --dir <extracted folder> --apply
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

from python.configuration.import_config_manager import ImportConfigManager
from python.configuration.market_config_manager import MarketConfigManager
from python.framework.types.config_types.market_config_types import ServerClockConfig
from python.framework.utils.time_utils import server_clock_to_utc_ms
from python.framework.validators.tick_import_validator import (
    PLAUSIBLE_LAG_WINDOW_MS,
    split_anchor_segments,
)

CORRECTION_VERSION = 4
BROKER_TYPE = 'mt5'
MARKER_KEY = 'collected_msc_server_clock_correction'
RESTORATION_KEY = 'collected_msc_restoration'
HOUR_MS = 3_600_000

# The V3 block a selected file carries: a whole-hour snap of -3 h over one anchor segment.
V3_VERSION = 3
V3_METHOD = 'hour_snap'
V3_SHIFT_MS = [-3 * HOUR_MS]

# Event time minus time_msc as the rule answers it, per half of the year. V3 aligned the
# arrival to time_msc - 3 h, so the daylight half is right already and the standard half is
# one hour early.
DAYLIGHT_TIME_OFFSET_MS = -3 * HOUR_MS
STANDARD_TIME_OFFSET_MS = -2 * HOUR_MS
CORRECTION_SHIFT_MS = STANDARD_TIME_OFFSET_MS - DAYLIGHT_TIME_OFFSET_MS
OFFSET_LABELS = {
    DAYLIGHT_TIME_OFFSET_MS: 'US daylight time',
    STANDARD_TIME_OFFSET_MS: 'US standard time',
}

# A collector file's metadata is about 2 KB; this one read has to contain all of it.
HEAD_READ_BYTES = 65_536
REPORT_FILE_LIMIT = 20
REASON_PAD = ' ' * 17

_METADATA_KEY_RE = re.compile(r'"metadata"\s*:\s*\{')
_FIRST_TIME_RE = re.compile(r'"time_msc"\s*:\s*(\d+)')
# Neither pattern matches "collected_msc_timebase" or "collected_msc_restoration": the
# closing quote is part of the key.
_PAIR_RE = re.compile(rb'"(time|collected)_msc"\s*:\s*(\d+)')
_COLLECTED_RE = re.compile(rb'"collected_msc"\s*:\s*(\d+)')
_RESTORATION_OPEN_RE = re.compile(rb'^([ \t]*)"collected_msc_restoration"\s*:\s*\{\s*$')


class Outcome(Enum):
    """What V4 did, or would do, with one file."""
    SHIFT = 'shift'
    UNCHANGED = 'unchanged'
    REFUSED = 'refused'
    FAILED = 'failed'
    SKIPPED = 'skipped'


@dataclass
class LagProfile:
    """Arrival minus event time over one file, in the form the importer's lag check reads it."""
    segments: int
    # The anchor segment's smallest lag farthest from zero — the number the importer judges
    worst_ms: int
    min_ms: int
    max_ms: int

    def inside_window(self) -> bool:
        """
        Whether the importer's lag check accepts the file.

        Returns:
            True when every anchor segment's smallest lag lies inside the window
        """
        return abs(self.worst_ms) <= PLAUSIBLE_LAG_WINDOW_MS


@dataclass
class FileTimes:
    """The time series of one file that the decision and the write checks work on."""
    time_msc: np.ndarray
    collected: np.ndarray
    utc_event: np.ndarray


@dataclass
class FilePlan:
    """What V4 decided for one file."""
    path: str
    name: str
    outcome: Outcome
    # Groups files in the report; detail carries what belongs to this file alone
    reason: str = ''
    detail: str = ''
    tick_count: int = 0
    shift_ms: int = 0
    lag_before: Optional[LagProfile] = None
    lag_after: Optional[LagProfile] = None
    # A skipped V3 file in US standard time whose arrival sits outside the window by the rule:
    # the importer will refuse it, and nothing here corrects it
    uncovered_standard_time: bool = False


def _read_head(path: str) -> Tuple[Optional[Dict[str, Any]], Optional[int]]:
    """
    Read the metadata object and the first tick's time_msc without reading the ticks.

    Args:
        path: Path to the tick JSON

    Returns:
        Tuple of (metadata, first time_msc), each None when it is not in the head
    """
    with open(path, 'rb') as handle:
        text = handle.read(HEAD_READ_BYTES).decode('utf-8', errors='replace')

    key = _METADATA_KEY_RE.search(text)
    if not key:
        return None, None
    try:
        metadata, end = json.JSONDecoder().raw_decode(text, key.end() - 1)
    except json.JSONDecodeError:
        return None, None
    if not isinstance(metadata, dict):
        return None, None

    first = _FIRST_TIME_RE.search(text, end)
    return metadata, int(first.group(1)) if first else None


def _broker_type_of(metadata: Dict[str, Any]) -> str:
    """
    Read the broker type as the importer does: broker_type, else the legacy data_collector.

    Args:
        metadata: The file's metadata object

    Returns:
        Normalized broker type, empty when the file names none
    """
    broker_type = metadata.get('broker_type')
    if broker_type is None:
        broker_type = metadata.get('data_collector')
    if broker_type is None:
        return ''
    return re.sub(r'[^a-z0-9_]', '_', str(broker_type).lower().strip())


def _restoration_mismatch(restoration: Dict[str, Any]) -> str:
    """
    Say how a restoration block differs from the V3 hour snap this migration corrects.

    Args:
        restoration: The file's collected_msc_restoration block

    Returns:
        Empty for the V3 hour snap of -3 h, otherwise a reason that groups files well
    """
    version = restoration.get('version')
    if version != V3_VERSION:
        return f'restoration version {version}'
    method = restoration.get('method')
    if method != V3_METHOD:
        return f'V3 method {method}'
    shift_ms = restoration.get('shift_ms')
    if shift_ms != V3_SHIFT_MS:
        return f'V3 hour_snap with shift_ms {shift_ms}'
    return ''


def _offset_name(offset_ms: int) -> str:
    """
    Name an event-time offset the rule produced.

    Args:
        offset_ms: Event time minus time_msc

    Returns:
        The half of the year it belongs to, or the raw offset when it belongs to neither
    """
    return OFFSET_LABELS.get(offset_ms, f'offset {offset_ms:+,} ms')


def _season_of(time_msc: Optional[int], clock: ServerClockConfig) -> str:
    """
    Name the half of the year one server stamp falls in, by the rule.

    Args:
        time_msc: A server wall-clock stamp, None when none was found
        clock: The broker's server clock rule

    Returns:
        The half of the year, or what kept the stamp from being placed
    """
    if time_msc is None:
        return 'no tick in the head'
    try:
        utc = server_clock_to_utc_ms(
            np.array([time_msc], dtype=np.int64), clock.timezone, clock.hours_ahead)
    except ValueError:
        return 'an hour the zone repeats or skips'
    return _offset_name(int(utc[0]) - time_msc)


def _scan_pairs(path: str) -> Tuple[np.ndarray, np.ndarray]:
    """
    Collect the (time_msc, collected_msc) series of a file with one pass over its bytes.

    Args:
        path: Path to the tick JSON

    Returns:
        Two int64 arrays in file order
    """
    time_msc: List[int] = []
    collected: List[int] = []

    with open(path, 'rb') as handle:
        raw = handle.read()

    for match in _PAIR_RE.finditer(raw):
        target = time_msc if match.group(1) == b'time' else collected
        target.append(int(match.group(2)))

    return (np.array(time_msc, dtype='int64'),
            np.array(collected, dtype='int64'))


def _lag_profile(collected: np.ndarray, utc_event: np.ndarray) -> LagProfile:
    """
    Measure arrival minus event time the way the importer's lag check does.

    Args:
        collected: collected_msc values in file order
        utc_event: The ticks' UTC event times, same order

    Returns:
        LagProfile with the per-segment verdict number and the range over all ticks
    """
    lag = collected - utc_event
    segments = split_anchor_segments(collected)

    worst = 0
    for start, end in segments:
        segment_min = int(lag[start:end].min())
        if abs(segment_min) > abs(worst):
            worst = segment_min

    return LagProfile(segments=len(segments), worst_ms=worst,
                      min_ms=int(lag.min()), max_ms=int(lag.max()))


def _measure_skipped(plan: FilePlan, clock: ServerClockConfig) -> None:
    """
    Measure a skipped V3 file in US standard time by the rule, and flag it when the importer
    will refuse it.

    Its V3 block alone does not say which event time the arrival was aligned to: the fixed
    -3 h of the August 2026 run, or the rule of a later one. The lag does.

    Args:
        plan: The skipped file's plan, described and flagged in place
        clock: The broker's server clock rule
    """
    time_msc, collected = _scan_pairs(plan.path)
    if len(collected) == 0 or len(time_msc) != len(collected):
        plan.uncovered_standard_time = True
        plan.detail = 'not measurable, time_msc and collected_msc do not pair up'
        return

    try:
        utc_event = server_clock_to_utc_ms(time_msc, clock.timezone, clock.hours_ahead)
    except ValueError as error:
        plan.uncovered_standard_time = True
        plan.detail = f'not measurable, {error}'
        return

    as_is = _lag_profile(collected, utc_event)
    plan.uncovered_standard_time = not as_is.inside_window()
    verdict = 'inside' if as_is.inside_window() else 'outside'
    plan.detail = (f'as it stands, worst segment minimum {as_is.worst_ms:+,} ms, '
                   f'{verdict} the window')


def plan_file(path: str, clock: ServerClockConfig) -> Tuple[FilePlan, Optional[FileTimes]]:
    """
    Select one file and decide its shift from its own ticks — nothing is written here.

    Args:
        path: Path to the tick JSON
        clock: The broker's server clock rule

    Returns:
        Tuple of (plan, time series) — the series only for a file that may be written
    """
    name = os.path.basename(path)
    plan = FilePlan(path=path, name=name, outcome=Outcome.SKIPPED)

    metadata, first_time_msc = _read_head(path)
    if metadata is None:
        plan.reason = 'metadata not readable in the head'
        return plan, None

    broker_type = _broker_type_of(metadata)
    if broker_type != BROKER_TYPE:
        plan.reason = f'broker type {broker_type}' if broker_type else 'no broker type'
        return plan, None

    if MARKER_KEY in metadata:
        plan.reason = 'already corrected (V4 marker present)'
        return plan, None

    restoration = metadata.get(RESTORATION_KEY)
    if not isinstance(restoration, dict):
        plan.reason = 'no V3 restoration block'
        return plan, None

    mismatch = _restoration_mismatch(restoration)
    if mismatch:
        season = _season_of(first_time_msc, clock)
        plan.reason = f'{mismatch}, first tick in {season}'
        if (restoration.get('version') == V3_VERSION
                and season == OFFSET_LABELS[STANDARD_TIME_OFFSET_MS]):
            _measure_skipped(plan, clock)
        return plan, None

    # Selected: from here on the file is shifted, left unchanged, or refused.
    plan.outcome = Outcome.REFUSED
    time_msc, collected = _scan_pairs(path)
    plan.tick_count = len(collected)

    if len(collected) == 0:
        plan.reason = 'no collected_msc values'
        return plan, None

    if len(time_msc) != len(collected):
        plan.reason = 'time_msc and collected_msc counts differ'
        plan.detail = f'{len(time_msc)} time_msc vs {len(collected)} collected_msc'
        return plan, None

    try:
        utc_event = server_clock_to_utc_ms(time_msc, clock.timezone, clock.hours_ahead)
    except ValueError as error:
        plan.reason = 'a tick in an hour the zone repeats or skips'
        plan.detail = str(error)
        return plan, None

    offsets, counts = np.unique(utc_event - time_msc, return_counts=True)
    if len(offsets) > 1:
        # Said with the lag as the file stands, because the answer differs by file: V3 took
        # each segment's smallest lag, and where that came from the daylight-time ticks the
        # file may already agree with the rule.
        as_is = _lag_profile(collected, utc_event)
        verdict = 'inside' if as_is.inside_window() else 'outside'
        plan.reason = 'mixed offsets'
        plan.detail = (' · '.join(f'{_offset_name(int(offset))} x{int(count)}'
                                  for offset, count in zip(offsets, counts))
                       + f' · as it stands, worst segment minimum {as_is.worst_ms:+,} ms, '
                         f'{verdict} the window')
        return plan, None

    offset = int(offsets[0])
    if offset == DAYLIGHT_TIME_OFFSET_MS:
        shift_ms = 0
    elif offset == STANDARD_TIME_OFFSET_MS:
        shift_ms = CORRECTION_SHIFT_MS
    else:
        plan.reason = 'an offset neither half of the year produces'
        plan.detail = _offset_name(offset)
        return plan, None

    plan.shift_ms = shift_ms
    plan.lag_before = _lag_profile(collected, utc_event)
    plan.lag_after = _lag_profile(collected + shift_ms, utc_event)

    # The self-check: the importer's own estimator, applied before anything is written.
    if not plan.lag_after.inside_window():
        plan.reason = ('lag outside the window after the shift' if shift_ms
                       else 'lag outside the window as it stands')
        plan.detail = (f'worst segment minimum {plan.lag_after.worst_ms:+,} ms '
                       f'(window +/-{PLAUSIBLE_LAG_WINDOW_MS:,} ms)')
        return plan, None

    plan.outcome = Outcome.SHIFT if shift_ms else Outcome.UNCHANGED
    times = FileTimes(time_msc=time_msc, collected=collected, utc_event=utc_event)
    return plan, times


def _marker_bytes(indent: bytes, newline: bytes, shift_ms: int,
                  clock: ServerClockConfig) -> bytes:
    """
    Build the metadata lines that record what V4 did.

    Args:
        indent: Leading whitespace of the V3 block's key line, so the marker lines up with it
        newline: The line ending the file uses
        shift_ms: The shift written into collected_msc
        clock: The broker's server clock rule the shift follows from

    Returns:
        The marker as ready-to-write bytes
    """
    applied = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    rule = f'{clock.timezone}{clock.hours_ahead:+d}h'
    pad = indent.decode('ascii')

    lines = [
        f'{pad}"{MARKER_KEY}": {{',
        f'{pad}  "version": {CORRECTION_VERSION},',
        f'{pad}  "applied": "{applied}",',
        f'{pad}  "rule": {json.dumps(rule)},',
        f'{pad}  "shift_ms": {shift_ms}',
        f'{pad}}},',
    ]
    return b''.join(line.encode('utf-8') + newline for line in lines)


def _rewrite(plan: FilePlan, clock: ServerClockConfig, part_path: str) -> int:
    """
    Stream one file into part_path with the shift and the marker applied.

    Args:
        plan: The decided plan, outcome SHIFT
        clock: The broker's server clock rule
        part_path: Where the rewritten copy goes

    Returns:
        The size in bytes the rewritten copy must have
    """
    expected_size = os.path.getsize(plan.path)
    rewritten = 0

    with open(plan.path, 'rb') as source, open(part_path, 'wb') as target:
        lines = iter(source)

        # The metadata, up to and including the closing line of the V3 block
        indent: Optional[bytes] = None
        for line in lines:
            target.write(line)
            if indent is None:
                opening = _RESTORATION_OPEN_RE.match(line)
                if opening:
                    indent = opening.group(1)
                continue

            body = line.rstrip()
            if body == indent + b'}':
                raise RuntimeError(
                    'the V3 block closes the metadata, which is not the layout '
                    'V3 writes — file untouched')
            if body == indent + b'},':
                marker = _marker_bytes(indent, line[len(line.rstrip(b'\r\n')):],
                                       plan.shift_ms, clock)
                target.write(marker)
                expected_size += len(marker)
                break
        else:
            raise RuntimeError(
                'no V3 block in the layout V3 writes — file untouched')

        # The ticks
        for line in lines:
            if b'"collected_msc"' in line:
                match = _COLLECTED_RE.search(line)
                if match:
                    shifted = str(int(match.group(1)) + plan.shift_ms).encode('ascii')
                    expected_size += len(shifted) - len(match.group(1))
                    line = line[:match.start(1)] + shifted + line[match.end(1):]
                    rewritten += 1
            target.write(line)

    if rewritten != plan.tick_count:
        raise RuntimeError(
            f'rewrote {rewritten} of {plan.tick_count} collected_msc values '
            f'— file untouched')

    return expected_size


def _check_written(part_path: str, plan: FilePlan, times: FileTimes, expected_size: int) -> None:
    """
    Prove the rewritten copy says exactly what was planned, before it replaces the original.

    Args:
        part_path: The rewritten copy
        plan: The decided plan
        times: The file's time series as planned
        expected_size: Original size plus the marker and any change in digit count
    """
    size = os.path.getsize(part_path)
    if size != expected_size:
        raise RuntimeError(
            f'{size} bytes written, expected {expected_size} — file untouched')

    expected = times.collected + plan.shift_ms
    time_msc, collected = _scan_pairs(part_path)
    if not np.array_equal(time_msc, times.time_msc):
        raise RuntimeError('time_msc changed in the rewrite — file untouched')
    if not np.array_equal(collected, expected):
        raise RuntimeError(
            'collected_msc not shifted as planned — file untouched')

    lag = _lag_profile(collected, times.utc_event)
    if not lag.inside_window():
        raise RuntimeError(
            f'worst segment minimum {lag.worst_ms:+,} ms after the rewrite '
            f'— file untouched')

    try:
        with open(part_path, 'rb') as handle:
            data = json.load(handle)
    except ValueError as error:
        raise RuntimeError(
            f'not valid JSON after the rewrite ({error}) — file untouched') from error

    marker = data.get('metadata', {}).get(MARKER_KEY)
    if not isinstance(marker, dict) or marker.get('shift_ms') != plan.shift_ms:
        raise RuntimeError('the parsed metadata lacks the marker — file untouched')

    # What the importer will read, through its own parser rather than the scan pattern
    parsed = np.array([tick.get('collected_msc', -1) for tick in data.get('ticks', [])],
                      dtype='int64')
    if not np.array_equal(parsed, expected):
        raise RuntimeError(
            'the parsed ticks disagree with the planned values — file untouched')


def write_shift(plan: FilePlan, times: FileTimes, clock: ServerClockConfig) -> None:
    """
    Rewrite one file in place: shift every collected_msc and insert the marker.

    The copy is written and checked as <file>.part; only a copy that passed every check
    replaces the original, and a failed one is removed.

    Args:
        plan: The decided plan, outcome SHIFT
        times: The file's time series as planned
        clock: The broker's server clock rule
    """
    part_path = f'{plan.path}.part'
    try:
        expected_size = _rewrite(plan, clock, part_path)
        _check_written(part_path, plan, times, expected_size)
    except Exception:
        if os.path.exists(part_path):
            os.remove(part_path)
        raise

    os.replace(part_path, plan.path)


def process_file(path: str, clock: ServerClockConfig, apply: bool) -> FilePlan:
    """
    Plan one file and, with apply, write it when it needs the shift.

    Args:
        path: Path to the tick JSON
        clock: The broker's server clock rule
        apply: Whether to write

    Returns:
        The plan, outcome FAILED when a write check refused the rewritten copy
    """
    plan, times = plan_file(path, clock)
    if not apply or plan.outcome is not Outcome.SHIFT or times is None:
        return plan

    try:
        write_shift(plan, times, clock)
    except RuntimeError as error:
        plan.outcome = Outcome.FAILED
        plan.reason = 'a check on the written copy failed'
        plan.detail = str(error)
    return plan


def _lag_range(profiles: Iterable[LagProfile]) -> Tuple[int, int]:
    """
    Span several files' lag ranges.

    Args:
        profiles: One lag profile per file

    Returns:
        Tuple of (smallest lag, largest lag) over all of them
    """
    listed = list(profiles)
    return min(p.min_ms for p in listed), max(p.max_ms for p in listed)


def _file_line(plan: FilePlan) -> str:
    """
    Describe one file in a line for the verbose listing.

    Args:
        plan: The file's plan

    Returns:
        The line without the name and the outcome
    """
    if plan.lag_after is None:
        return f'{plan.reason}: {plan.detail}' if plan.detail else plan.reason

    after = plan.lag_after
    line = (f'{plan.tick_count} ticks · {after.segments} seg · '
            f'lag {after.min_ms:+,} … {after.max_ms:+,} ms')
    if plan.shift_ms and plan.lag_before is not None:
        line += f' (before the shift {plan.lag_before.min_ms:+,} … {plan.lag_before.max_ms:+,} ms)'
    if plan.outcome in (Outcome.REFUSED, Outcome.FAILED):
        line += f' — {plan.detail or plan.reason}'
    return line


def _print_report(plans: List[FilePlan], apply: bool, verbose: bool) -> None:
    """
    Print what was selected, decided and written, and the lag the importer will see.

    Args:
        plans: All file plans of this run
        apply: Whether changes were written
        verbose: Whether to print one line per file
    """
    if verbose:
        print('\n  per file')
        for plan in plans:
            print(f'  {plan.name:<36} {plan.outcome.value:<9} {_file_line(plan)}')

    by_outcome: Dict[Outcome, List[FilePlan]] = {outcome: [] for outcome in Outcome}
    for plan in plans:
        by_outcome[plan.outcome].append(plan)

    headlines = {
        Outcome.SHIFT: f'collected_msc {CORRECTION_SHIFT_MS:+,} ms, ticks in US standard time',
        Outcome.UNCHANGED: 'ticks in US daylight time, where V3 was already right',
        Outcome.REFUSED: 'never written',
        Outcome.FAILED: 'a check on the written copy failed, original untouched',
        Outcome.SKIPPED: 'outside the selection',
    }

    selected = len(plans) - len(by_outcome[Outcome.SKIPPED])
    print(f'\n  {selected} of {len(plans)} files selected')
    print('\n  outcome          files    detail')
    for outcome in Outcome:
        group = by_outcome[outcome]
        if not group:
            continue
        print(f'  {outcome.value:<14} {len(group):>7}    {headlines[outcome]}')

        if outcome in (Outcome.SHIFT, Outcome.UNCHANGED):
            continue
        # A failed write has one reason, the headline; its detail is what to read
        if outcome is not Outcome.FAILED:
            reasons: Dict[str, int] = {}
            for plan in group:
                reasons[plan.reason] = reasons.get(plan.reason, 0) + 1
            for reason, count in sorted(reasons.items()):
                print(f'{REASON_PAD}{count:>7}      {reason}')
        if outcome in (Outcome.REFUSED, Outcome.FAILED):
            for plan in group[:REPORT_FILE_LIMIT]:
                print(f'       ❌ {plan.name}: {plan.detail or plan.reason}')
            if len(group) > REPORT_FILE_LIMIT:
                print(f'       … and {len(group) - REPORT_FILE_LIMIT} more')

    shifted = by_outcome[Outcome.SHIFT]
    unchanged = by_outcome[Outcome.UNCHANGED]
    if shifted or unchanged:
        print('\n  arrival minus event time by the rule, over all ticks')
        print(f'  (the importer judges the smallest lag of each anchor segment, '
              f'window +/-{PLAUSIBLE_LAG_WINDOW_MS:,} ms):')
    if shifted:
        after_low, after_high = _lag_range(p.lag_after for p in shifted if p.lag_after)
        before_low, before_high = _lag_range(p.lag_before for p in shifted if p.lag_before)
        worst = max((p.lag_after.worst_ms for p in shifted if p.lag_after), key=abs)
        print(f'    shifted     {after_low:+,} … {after_high:+,} ms after the shift, '
              f'worst segment minimum {worst:+,} ms')
        print(f'                {before_low:+,} … {before_high:+,} ms before it')
    if unchanged:
        low, high = _lag_range(p.lag_after for p in unchanged if p.lag_after)
        worst = max((p.lag_after.worst_ms for p in unchanged if p.lag_after), key=abs)
        print(f'    unchanged   {low:+,} … {high:+,} ms as they stand, '
              f'worst segment minimum {worst:+,} ms')

    uncovered = [p for p in plans if p.uncovered_standard_time]
    if uncovered:
        print(f'\n  ❗ skipped, yet outside the window by the rule: {len(uncovered)} mt5 file(s) '
              f'under another V3 block,')
        print('     first tick in US standard time. The importer will refuse them as they')
        print('     stand, and this migration does not correct them:')
        for plan in uncovered[:REPORT_FILE_LIMIT]:
            print(f'       {plan.name}: {plan.detail}')
        if len(uncovered) > REPORT_FILE_LIMIT:
            print(f'       … and {len(uncovered) - REPORT_FILE_LIMIT} more')

    if not apply:
        print('\n  dry run — nothing written.')
    elif shifted:
        print(f'\n  written: {len(shifted)}, each checked as a copy (exact size, re-scanned,')
        print('  loaded as JSON) before it replaced its original.')
    else:
        print('\n  nothing needed writing.')


def main() -> None:
    """Select the files V3 aligned to a fixed -3 h, decide each by the rule, optionally write."""
    parser = argparse.ArgumentParser(
        description='Move MT5 arrival times in US standard time onto the server clock rule (V4)')
    parser.add_argument('--apply', action='store_true',
                        help='write changes (default is a dry run)')
    parser.add_argument('--dir', default=None,
                        help='folder of EXTRACTED *_ticks.json files '
                             '(default: import config data_raw)')
    parser.add_argument('--verbose', action='store_true',
                        help='print one line per file, with its lag range')
    args = parser.parse_args()

    source_dir = args.dir or ImportConfigManager().get_data_raw_path()
    if not os.path.isdir(source_dir):
        print(f'Directory not found: {source_dir}')
        sys.exit(1)

    clock = MarketConfigManager().get_server_clock(BROKER_TYPE)
    files = sorted(f for f in os.listdir(source_dir) if f.endswith('_ticks.json'))

    mode = 'APPLY — writing changes' if args.apply else 'DRY RUN (use --apply to write)'
    print(f'V4 collected_msc server clock correction — {mode}')
    print(f'source: {source_dir}  ·  {len(files)} files')
    print(f'rule: {BROKER_TYPE} server clock = {clock.timezone} {clock.hours_ahead:+d} h')

    plans: List[FilePlan] = []
    for index, name in enumerate(files, 1):
        plans.append(process_file(os.path.join(source_dir, name), clock, args.apply))
        if index % 250 == 0:
            print(f'  … {index}/{len(files)}')

    _print_report(plans, args.apply, args.verbose)

    if any(plan.outcome in (Outcome.REFUSED, Outcome.FAILED) for plan in plans):
        sys.exit(1)


if __name__ == '__main__':
    main()
