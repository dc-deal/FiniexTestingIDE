"""One-shot migration: bring collected_msc into UTC and repair broken anchors.

Third restoration of this field. V1 and V2 synthesized it for files that never
carried it and left no marker, which is why their output had to be identified by
fingerprint afterwards. V3 marks every file it touches.

Two defects are repaired at once:
  - the MT5 collector anchored collected_msc on a counter that overflows after
    2^64 / 1e13 s (21d 8h 24m 34s) of runtime, so 72% of collector-era files
    carry an unusable absolute position
  - the importer never converted the field to UTC, so it sat in device-local
    time next to a UTC-converted time_msc

One estimator, three branches, applied per anchor segment:

    lag = min(collected_msc - utc_event_time)   over the segment

    |lag| <= window -> no-op            already correct
    near a whole hour -> hour shift     timezone class; the residual must land
                                        inside the window, which is the check
    otherwise       -> shift by lag     anchor garbage, NTP-style minimum filter

The window is 5 minutes, not seconds: a repaired file's residual carries the
collector's session drift (~1 s/day over 8-11 days) plus the lift applied at an
anchor change to keep arrival continuous. Both are legitimate.

Runs line-oriented in two passes rather than through json.load: the archive is
93 GB, and only the collected_msc digits plus a few metadata lines change. Files
stay byte-identical apart from those.

Dry-run by default — pass --apply to write.
"""

import argparse
import datetime as dt
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..')))

from python.configuration.import_config_manager import ImportConfigManager
from python.framework.validators.tick_import_validator import (
    PLAUSIBLE_LAG_WINDOW_MS,
    split_anchor_segments,
)

RESTORATION_VERSION = 3
HOUR_MS = 3_600_000
TIMEZONE_BRANCH_LIMIT_MS = 24 * HOUR_MS
# Lag spread tolerated within one collector session: above the drift it
# accumulates (~1 s/day over 9-11 days), far below the smallest anchor error.
ANCHOR_GROUP_TOLERANCE_MS = 60_000

# Scanning pass: one combined pattern over the raw bytes — roughly five times
# faster than looping the file line by line, which matters over 93 GB.
# Neither alternative matches "timestamp_msc" or "collected_msc_timebase",
# because the closing quote is part of the pattern.
_PAIR_RE = re.compile(rb'"(time|collected)_msc"\s*:\s*(\d+)')
# Rewriting pass: line-oriented, so formatting is preserved byte for byte.
_COLLECTED_RE = re.compile(r'^(\s*"collected_msc"\s*:\s*)(\d+)(.*)$')
_VERSION_RE = re.compile(r'^(\s*)"data_format_version"\s*:')
_META_STR_RE = r'"%s"\s*:\s*"([^"]*)"'


@dataclass
class SegmentPlan:
    """One anchor segment of a file and the shift it needs."""
    start: int
    end: int
    lag_ms: int
    first_value: int
    last_value: int
    shift_ms: int = 0
    method: str = 'pending'


@dataclass
class FilePlan:
    """What V3 intends to do with one file."""
    path: str
    name: str
    broker_type: str
    tick_count: int
    segments: List[SegmentPlan] = field(default_factory=list)
    skip_reason: Optional[str] = None
    # Bounds after the planned shift, for the archive-wide continuity check
    first_event: int = 0
    first_collected: int = 0
    last_collected: int = 0
    repaired_by_v3: bool = False

    def method(self) -> str:
        """
        Overall branch of the file, taken from its strongest segment.

        Returns:
            'noop', 'hour_snap', 'min_filter' or 'skipped'
        """
        if self.skip_reason:
            return 'skipped'
        methods = {s.method.replace('+seam', '') for s in self.segments}
        for candidate in ('min_filter', 'hour_snap'):
            if candidate in methods:
                return candidate
        return 'noop'

    def touches_data(self) -> bool:
        """
        Whether any tick value actually changes.

        Returns:
            True when at least one segment carries a non-zero shift
        """
        return any(s.shift_ms != 0 for s in self.segments)


def _read_metadata_head(path: str) -> Dict[str, str]:
    """
    Read the string metadata fields without parsing the whole file.

    Args:
        path: Path to the tick JSON

    Returns:
        Dict of the metadata fields V3 needs
    """
    with open(path, 'r', encoding='utf-8') as handle:
        head = handle.read(4000)

    fields = {}
    for key in ('broker_type', 'data_collector', 'data_format_version',
                'collected_msc_timebase'):
        match = re.search(_META_STR_RE % key, head)
        fields[key] = match.group(1) if match else ''

    # Two reasons to skip, and they mean different things: the collector wrote
    # the file already in UTC (normal), or a previous V3 run repaired it
    # (only normal if that run completed).
    fields['already_repaired'] = 'collected_msc_restoration' in head
    fields['self_describing'] = ('collected_msc_restoration' in head
                                 or 'collected_msc_timebase' in head)
    return fields


def _scan_pairs(path: str) -> Tuple[np.ndarray, np.ndarray]:
    """
    Collect the (time_msc, collected_msc) series of a file line by line.

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


def _plan_segment(lag_ms: int) -> Tuple[int, str]:
    """
    Decide the correction for one segment from its minimum lag.

    Args:
        lag_ms: Smallest collected_msc minus UTC event time in the segment

    Returns:
        Tuple of (shift to apply, branch name)
    """
    if abs(lag_ms) <= PLAUSIBLE_LAG_WINDOW_MS:
        return 0, 'noop'

    # A timezone error is a whole number of hours and never exceeds a day. Take
    # that branch only when snapping to the nearest hour actually lands inside
    # the window — otherwise the offset is something else and the minimum filter
    # is the honest treatment.
    if abs(lag_ms) < TIMEZONE_BRANCH_LIMIT_MS:
        hours = int(round(lag_ms / HOUR_MS))
        if hours and abs(lag_ms - hours * HOUR_MS) <= PLAUSIBLE_LAG_WINDOW_MS:
            return -hours * HOUR_MS, 'hour_snap'

    return -lag_ms, 'min_filter'


def scan_file(path: str, offsets: Dict[str, int]) -> FilePlan:
    """
    Read one file and describe its anchor segments — no decision yet.

    Deciding needs the whole archive: the anchor error is a property of the
    collector session, not of a single file, and a session spans many files.

    Args:
        path: Path to the tick JSON
        offsets: Broker offset hours from the import registry

    Returns:
        FilePlan with raw segments; shifts are filled in by plan_shifts()
    """
    name = os.path.basename(path)
    meta = _read_metadata_head(path)
    broker_type = meta['broker_type'] or meta['data_collector'] or 'unknown'

    plan = FilePlan(path=path, name=name, broker_type=broker_type, tick_count=0)

    if meta['self_describing']:
        plan.repaired_by_v3 = meta['already_repaired']
        plan.skip_reason = ("already repaired by a previous V3 run"
                            if meta['already_repaired']
                            else "collector already writes UTC")
        return plan

    time_msc, collected = _scan_pairs(path)
    plan.tick_count = len(collected)

    if len(collected) == 0:
        plan.skip_reason = "no collected_msc values"
        return plan

    if len(time_msc) != len(collected):
        plan.skip_reason = (f"pair mismatch: {len(time_msc)} time_msc vs "
                            f"{len(collected)} collected_msc")
        return plan

    # time_msc is broker-local epoch; the registry offset converts it to UTC,
    # exactly as the importer does it.
    offset_ms = offsets.get(broker_type, 0) * HOUR_MS
    utc_event = time_msc + offset_ms
    plan.first_event = int(time_msc[0])

    for start, end in split_anchor_segments(collected):
        plan.segments.append(SegmentPlan(
            start=start, end=end,
            lag_ms=int((collected[start:end] - utc_event[start:end]).min()),
            first_value=int(collected[start]),
            last_value=int(collected[end - 1])))

    return plan


def plan_shifts(plans: List[FilePlan]) -> None:
    """
    Decide the shift for every segment, grouped by collector anchor.

    The unit of repair is the anchor group, not the file. All segments produced
    while the collector held one anchor share one error, so they must receive
    one identical shift — otherwise the relative distances between neighbouring
    files are altered and the concatenated stream gains steps it never had.
    Measured: repairing per file introduced 8 backwards steps in a single
    symbol-month whose source data was continuous throughout.

    A new group starts where the raw lag departs from the current group by more
    than ANCHOR_GROUP_TOLERANCE_MS — comfortably above the drift a session
    accumulates (~1 s/day over 9-11 days) and far below the smallest real anchor
    error (1 h).

    Args:
        plans: All file plans of this run, shifts filled in place
    """
    by_symbol: Dict[Tuple[str, str], List[FilePlan]] = {}
    for plan in plans:
        if plan.skip_reason:
            continue
        by_symbol.setdefault(
            (plan.broker_type, plan.name.split('_')[0]), []).append(plan)

    for (broker_type, _symbol), group_plans in sorted(by_symbol.items()):
        group_plans.sort(key=lambda p: p.first_event)

        # Flatten to the chronological chain of segments across all files
        chain = [seg for plan in group_plans for seg in plan.segments]
        if not chain:
            continue

        groups: List[List[SegmentPlan]] = [[chain[0]]]
        for segment in chain[1:]:
            if abs(segment.lag_ms - groups[-1][0].lag_ms) > ANCHOR_GROUP_TOLERANCE_MS:
                groups.append([segment])
            else:
                groups[-1].append(segment)

        previous_end = None
        for group in groups:
            group_lag = min(seg.lag_ms for seg in group)
            shift, method = _plan_segment(group_lag)

            # A real anchor change is a discontinuity in the source. Lift the
            # group by the minimum amount that keeps arrival non-decreasing.
            if previous_end is not None:
                gap = previous_end - (group[0].first_value + shift)
                if gap > 0:
                    shift += gap
                    method = f"{method}+seam"

            for segment in group:
                segment.shift_ms = shift
                segment.method = method

            previous_end = group[-1].last_value + shift

        for plan in group_plans:
            plan.first_collected = plan.segments[0].first_value + plan.segments[0].shift_ms
            plan.last_collected = plan.segments[-1].last_value + plan.segments[-1].shift_ms


def _marker_lines(plan: FilePlan, indent: str) -> List[str]:
    """
    Build the metadata lines that record what V3 did.

    Args:
        plan: The plan being applied
        indent: Leading whitespace of the anchor line, so the block lines up

    Returns:
        List of ready-to-write lines including newlines
    """
    applied = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    shifts = ', '.join(str(s.shift_ms) for s in plan.segments)

    return [
        f'{indent}"collected_msc_timebase": "utc",\n',
        f'{indent}"collected_msc_restoration": {{\n',
        f'{indent}  "version": {RESTORATION_VERSION},\n',
        f'{indent}  "applied": "{applied}",\n',
        f'{indent}  "method": "{plan.method()}",\n',
        f'{indent}  "segments": {len(plan.segments)},\n',
        f'{indent}  "shift_ms": [{shifts}]\n',
        f'{indent}}},\n',
    ]


def apply_plan(plan: FilePlan) -> None:
    """
    Rewrite one file in place: shift collected_msc and insert the marker.

    Streams line by line and writes to a sibling temp file, which replaces the
    original only after the full pass succeeded.

    Args:
        plan: The plan to apply
    """
    shift_per_tick = np.zeros(plan.tick_count, dtype='int64')
    for segment in plan.segments:
        shift_per_tick[segment.start:segment.end] = segment.shift_ms

    temp_path = f"{plan.path}.v3tmp"
    ordinal = 0
    marker_written = False

    with open(plan.path, 'r', encoding='utf-8') as source, \
            open(temp_path, 'w', encoding='utf-8') as target:
        for line in source:
            if not marker_written:
                version_match = _VERSION_RE.match(line)
                if version_match:
                    target.write(line)
                    target.writelines(_marker_lines(plan, version_match.group(1)))
                    marker_written = True
                    continue

            collected_match = _COLLECTED_RE.match(line)
            if collected_match:
                shifted = int(collected_match.group(2)) + int(shift_per_tick[ordinal])
                target.write(f"{collected_match.group(1)}{shifted}{collected_match.group(3)}\n")
                ordinal += 1
                continue

            target.write(line)

    if ordinal != plan.tick_count:
        os.remove(temp_path)
        raise RuntimeError(
            f"{plan.name}: rewrote {ordinal} of {plan.tick_count} ticks — file untouched")

    if not marker_written:
        os.remove(temp_path)
        raise RuntimeError(
            f"{plan.name}: no data_format_version line to anchor the marker — file untouched")

    os.replace(temp_path, plan.path)


def verify_file(path: str, offsets: Dict[str, int]) -> Tuple[bool, str]:
    """
    Re-scan a repaired file and confirm the invariant now holds.

    Args:
        path: Path to the tick JSON
        offsets: Broker offset hours from the import registry

    Returns:
        Tuple of (ok, detail) — detail is empty when ok
    """
    meta = _read_metadata_head(path)
    broker_type = meta['broker_type'] or meta['data_collector'] or 'unknown'
    time_msc, collected = _scan_pairs(path)

    if len(collected) == 0:
        return True, ''

    offset_ms = offsets.get(broker_type, 0) * HOUR_MS
    utc_event = time_msc + offset_ms

    backwards = int((np.diff(collected) < 0).sum()) if len(collected) > 1 else 0
    if backwards:
        return False, f"{backwards} backwards steps remain"

    worst = 0
    for start, end in split_anchor_segments(collected):
        lag = int((collected[start:end] - utc_event[start:end]).min())
        if abs(lag) > abs(worst):
            worst = lag

    if abs(worst) > PLAUSIBLE_LAG_WINDOW_MS:
        return False, f"lag {worst} ms still outside +/-{PLAUSIBLE_LAG_WINDOW_MS} ms"

    return True, ''


def check_archive_continuity(plans: List[FilePlan]) -> List[str]:
    """
    Verify arrival time never runs backwards between consecutive files.

    Each file is repaired on its own, so two neighbours could land on slightly
    different residual lags — the same seam problem that exists between segments
    within a file, one level up. Arrival is a physical sequence: a tick cannot
    be observed before one that arrived earlier.

    Args:
        plans: All file plans of this run, with post-shift bounds filled in

    Returns:
        List of continuity findings, empty when the archive is coherent
    """
    groups: Dict[Tuple[str, str], List[FilePlan]] = {}
    for plan in plans:
        if plan.skip_reason or not plan.last_collected:
            continue
        symbol = plan.name.split('_')[0]
        groups.setdefault((plan.broker_type, symbol), []).append(plan)

    findings: List[str] = []
    for (broker_type, symbol), group in sorted(groups.items()):
        group.sort(key=lambda p: p.first_event)
        for previous, current in zip(group, group[1:]):
            if current.first_collected >= previous.last_collected:
                continue
            findings.append(
                f"{broker_type}/{symbol}: collected_msc steps back "
                f"{previous.last_collected - current.first_collected} ms from "
                f"{previous.name} to {current.name}"
            )

    return findings


def _print_report(plans: List[FilePlan], apply: bool) -> None:
    """
    Print the branch breakdown and the notable individual files.

    Args:
        plans: All file plans of this run
        apply: Whether changes were written
    """
    by_method: Dict[str, List[FilePlan]] = {}
    for plan in plans:
        by_method.setdefault(plan.method(), []).append(plan)

    print("\n  branch          files    detail")
    for method in ('noop', 'hour_snap', 'min_filter', 'skipped'):
        group = by_method.get(method, [])
        if not group:
            continue

        if method == 'hour_snap':
            hours: Dict[int, int] = {}
            for plan in group:
                for segment in plan.segments:
                    if segment.method.startswith('hour_snap'):
                        hours[segment.shift_ms // HOUR_MS] = hours.get(
                            segment.shift_ms // HOUR_MS, 0) + 1
            detail = ' · '.join(f"{h:+d} h ({n})" for h, n in sorted(hours.items()))
        elif method == 'skipped':
            reasons: Dict[str, int] = {}
            for plan in group:
                reasons[plan.skip_reason] = reasons.get(plan.skip_reason, 0) + 1
            detail = ' · '.join(f"{r} ({n})" for r, n in sorted(reasons.items()))
        elif method == 'noop':
            detail = ' · '.join(sorted({p.broker_type for p in group}))
        else:
            detail = f"largest shift {max(abs(s.shift_ms) for p in group for s in p.segments)} ms"

        print(f"  {method:<14} {len(group):>5}    {detail}")

    multi = [p for p in plans if len(p.segments) > 1]
    print(f"  {'multi-segment':<14} {len(multi):>5}    files whose anchor changed mid-file")

    for plan in multi[:3]:
        print(f"\n  ❗ {plan.name}   {len(plan.segments)} segments")
        for segment in plan.segments:
            print(f"       seg [{segment.start:>6}:{segment.end:<6}] "
                  f"lag {segment.lag_ms:>21} ms  → {segment.method} {segment.shift_ms:+d} ms")
    if len(multi) > 3:
        print(f"     … and {len(multi) - 3} more")

    changed = sum(1 for p in plans if p.touches_data())
    print(f"\n  {changed} of {len(plans)} files need a tick-value change; "
          f"{len(plans) - changed} get the marker only.")
    print("  nothing written." if not apply else "  changes written.")


def main() -> None:
    """Scan the raw archive, plan the repair, and optionally apply it."""
    parser = argparse.ArgumentParser(
        description='Restore collected_msc to UTC and repair broken anchors (V3)')
    parser.add_argument('--apply', action='store_true',
                        help='write changes (default is a dry run)')
    parser.add_argument('--dir', default=None,
                        help='source directory (default: import config data_raw)')
    parser.add_argument('--limit', type=int, default=0,
                        help='process at most N files (0 = all)')
    args = parser.parse_args()

    config = ImportConfigManager()
    source_dir = args.dir or config.get_data_raw_path()
    offsets = {bt: config.get_default_offset(bt)
               for bt in config.get_offset_registry()}

    if not os.path.isdir(source_dir):
        print(f"Directory not found: {source_dir}")
        sys.exit(1)

    files = sorted(f for f in os.listdir(source_dir) if f.endswith('_ticks.json'))
    if args.limit:
        files = files[:args.limit]

    mode = 'APPLY — writing changes' if args.apply else 'DRY RUN (use --apply to write)'
    print(f"V3 collected_msc restoration — {mode}")
    print(f"source: {source_dir}  ·  {len(files)} files")
    print(f"broker offsets: " + ', '.join(f"{bt} {off:+d}h" for bt, off in offsets.items()))

    plans: List[FilePlan] = []
    failures: List[str] = []

    print("\nscanning …")
    for index, name in enumerate(files, 1):
        plans.append(scan_file(os.path.join(source_dir, name), offsets))
        if index % 250 == 0:
            print(f"  … {index}/{len(files)}")

    # Deciding needs every file: an anchor group spans many of them.
    plan_shifts(plans)

    # A directory holding both V3-marked and unmarked files is ambiguous: either
    # a previous run was interrupted, or new data arrived after a completed one.
    # It only matters when the unmarked files actually need a value change —
    # their anchor groups would then be computed without their already-repaired
    # neighbours, placing a step exactly where this migration removes one.
    # Files that only need the marker carry no such risk.
    repaired = [p for p in plans if p.repaired_by_v3]
    shifting = [p for p in plans if not p.skip_reason and p.touches_data()]
    if repaired and shifting:
        print(f"\n  ❌ ABORT — {len(repaired)} files already carry a V3 marker while")
        print(f"     {len(shifting)} unmarked files need a tick-value change.")
        print("     Anchor groups are derived from ALL files of a symbol, so planning")
        print("     these without their repaired neighbours would place a step at the")
        print("     boundary between the two halves.")
        print("     If a previous run was interrupted: restore from backup and start over.")
        print("     If this is new data: import the repaired files first, so they leave")
        print("     the source directory, then run again.")
        sys.exit(1)

    if args.apply:
        print("\nwriting …")
        for index, plan in enumerate(plans, 1):
            if plan.skip_reason:
                continue
            try:
                apply_plan(plan)
            except RuntimeError as error:
                failures.append(str(error))
                continue

            ok, detail = verify_file(plan.path, offsets)
            if not ok:
                failures.append(f"{plan.name}: {detail}")

            if index % 250 == 0:
                print(f"  … {index}/{len(files)}")

    _print_report(plans, args.apply)

    continuity = check_archive_continuity(plans)
    if continuity:
        print(f"\n  ❗ arrival time steps backwards at {len(continuity)} file boundaries:")
        for finding in continuity[:20]:
            print(f"     {finding}")
        failures.extend(continuity)
    else:
        print("  ✅ arrival time continuous across every file boundary.")

    if failures:
        print(f"\n  ❌ {len(failures)} failures:")
        for failure in failures[:20]:
            print(f"     {failure}")
        sys.exit(1)

    if args.apply:
        print("  ✅ every rewritten file re-scanned and inside the plausible window.")


if __name__ == '__main__':
    main()
