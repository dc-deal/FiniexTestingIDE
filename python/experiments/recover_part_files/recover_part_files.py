"""
Finishes the MT5 tick files a hard stop of the collector host left unfinished (*_ticks.json.part),
so that the importer can read them. A one-time repair; nothing imports it.

Why it exists: the MT5 TickCollector writes the closing bracket, the errors block and the summary
of a file only on a clean close, and its writes reach the disk in blocks. A hard stop therefore
leaves a file cut in the middle of a tick, which no JSON reader accepts. The importer never changes
a raw file, so the repair happens here, before the import.

What it writes: every byte of the part file up to and including its last complete tick, unchanged,
then the closing of the tick array and a summary in the collector's own layout. The summary carries
total_ticks (the importer checks it against the rows), the end time of the last kept tick in the
file's server time, and a recovery block. The recovery block holds the bytes that were dropped, so
the part file is restored byte for byte as recovered[:cut_at_byte] + dropped_tail — write checks
exactly that before it keeps a file. There is no errors block and no quality_metrics: the collector
writes them only on a clean close, so what they would have said is unknown, not zero.

It writes <name>_ticks.json beside each part file and refuses when that file exists already. It
never changes or deletes a part file; an empty one has nothing to recover and is reported.

Recorded runs:
  2026-10-24 · host hard stop 2026-10-20 04:21:20Z · 8 part files · 7 recovered, 249,268 ticks,
    150-391 bytes dropped per file · 1 empty (USDCAD, opened 04:01, nothing on disk)

Usage:
    python python/experiments/recover_part_files/recover_part_files.py plan
    python python/experiments/recover_part_files/recover_part_files.py write \\
        --reason "host hard stop 2026-10-20 04:21:20Z, file never closed"
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from python.configuration.import_config_manager import ImportConfigManager
from python.experiments.recover_part_files.recover_part_files_types import PartFileRecovery

PART_SUFFIX = '.part'
TICKS_ARRAY_OPENING = b'"ticks": ['
# Fields the collector writes only on a clean close, named in the recovery block
NOT_RECORDED = 'errors, quality_metrics: the collector writes them only on a clean close'


def plan_recovery(part_path: Path) -> PartFileRecovery:
    """
    Find where a part file is cut and what its complete ticks are.

    Args:
        part_path: The unfinished file

    Returns:
        The recovery of the file, with a refusal when it cannot be recovered
    """
    data = part_path.read_bytes()
    recovery = PartFileRecovery(
        part_path=part_path,
        recovered_path=part_path.with_name(part_path.name[:-len(PART_SUFFIX)]),
        original_size_bytes=len(data),
        cut_at_byte=0,
        tick_count=0,
        first_collected_msc=None,
        last_collected_msc=None,
        last_timestamp=None,
        dropped_tail='',
        refusal=None
    )
    if not data:
        recovery.refusal = 'empty: nothing reached the disk'
        return recovery

    newline = _detect_newline(data)
    ticks_start = data.find(TICKS_ARRAY_OPENING)
    # A tick object closes at an indent of four; so do the nested blocks of the metadata, which
    # is why the cut must lie behind the opening of the tick array
    cut = data.rfind(newline + b'    }')
    if ticks_start < 0 or cut < ticks_start:
        recovery.refusal = 'no complete tick behind the opening of the tick array'
        return recovery

    recovery.cut_at_byte = cut + len(newline + b'    }')
    try:
        recovery.dropped_tail = data[recovery.cut_at_byte:].decode('utf-8')
    except UnicodeDecodeError:
        recovery.refusal = 'the dropped bytes are not valid UTF-8'
        return recovery

    ticks = json.loads(data[:recovery.cut_at_byte] + newline + b'  ]' + newline + b'}')['ticks']
    recovery.tick_count = len(ticks)
    recovery.first_collected_msc = ticks[0]['collected_msc']
    recovery.last_collected_msc = ticks[-1]['collected_msc']
    recovery.last_timestamp = ticks[-1]['timestamp']
    if recovery.recovered_path.exists():
        recovery.refusal = f'{recovery.recovered_path.name} exists already'
    return recovery


def build_recovered_bytes(
    recovery: PartFileRecovery,
    original: bytes,
    reason: str,
    recovered_at_utc: datetime
) -> bytes:
    """
    Close the kept prefix of a part file with a summary that states the recovery.

    Args:
        recovery: The planned recovery of the file
        original: The part file's bytes
        reason: Why the file was left unfinished, in a few words
        recovered_at_utc: When the recovery ran — a provenance stamp

    Returns:
        The bytes of the finished file
    """
    newline = _detect_newline(original)
    summary: Dict[str, Any] = {
        'total_ticks': recovery.tick_count,
        'data_stream_status': 'RECOVERED',
        'timing': {'end_time': recovery.last_timestamp},
        'recovery': {
            'original_file': recovery.part_path.name,
            'original_size_bytes': recovery.original_size_bytes,
            'cut_at_byte': recovery.cut_at_byte,
            'dropped_tail': recovery.dropped_tail,
            'reason': reason,
            'recovered_at_utc': recovered_at_utc.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'not_recorded': NOT_RECORDED
        }
    }
    # The collector indents by two, and every line of the summary sits one level deep
    body = json.dumps(summary, indent=2, ensure_ascii=False).encode('utf-8')
    body = body.replace(b'\n', newline + b'  ')
    return (original[:recovery.cut_at_byte] + newline + b'  ],' + newline
            + b'  "summary": ' + body + newline + b'}')


def check_recovered_bytes(recovery: PartFileRecovery, original: bytes, recovered: bytes) -> List[str]:
    """
    Prove a finished file against its part file before it is kept.

    Args:
        recovery: The planned recovery of the file
        original: The part file's bytes
        recovered: The bytes of the finished file

    Returns:
        What does not hold; empty when the file is sound
    """
    problems: List[str] = []
    doc = json.loads(recovered)
    if len(doc['ticks']) != doc['summary']['total_ticks']:
        problems.append(f'{len(doc["ticks"])} ticks, summary declares {doc["summary"]["total_ticks"]}')
    restored = recovered[:recovery.cut_at_byte] + recovery.dropped_tail.encode('utf-8')
    if restored != original:
        problems.append('the part file is not restored byte for byte from the finished file')
    return problems


def run_plan(raw_dir: Path) -> int:
    """
    Report what each part file in the inbox would yield. Reads only.

    Args:
        raw_dir: The importer's inbox

    Returns:
        Exit code
    """
    for recovery in _plan_all(raw_dir):
        print(_describe(recovery))
    return 0


def run_write(raw_dir: Path, reason: str) -> int:
    """
    Write the finished file for each part file that can be recovered.

    Args:
        raw_dir: The importer's inbox
        reason: Why the files were left unfinished, written into each recovery block

    Returns:
        Exit code: 0 when every recoverable file was written and checked
    """
    recovered_at_utc = datetime.now(timezone.utc)
    failed = False
    for recovery in _plan_all(raw_dir):
        print(_describe(recovery))
        if recovery.refusal:
            continue
        original = recovery.part_path.read_bytes()
        recovered = build_recovered_bytes(recovery, original, reason, recovered_at_utc)
        problems = check_recovered_bytes(recovery, original, recovered)
        if problems:
            failed = True
            print(f'    NOT WRITTEN: {"; ".join(problems)}')
            continue
        # Written under a temporary name first: a file the importer sees is complete
        writing_path = recovery.recovered_path.with_name(recovery.recovered_path.name + '.writing')
        writing_path.write_bytes(recovered)
        writing_path.rename(recovery.recovered_path)
        print(f'    written {recovery.recovered_path.name} ({len(recovered):,} B)')
    return 1 if failed else 0


def _plan_all(raw_dir: Path) -> List[PartFileRecovery]:
    """
    Plan the recovery of every part file in the inbox.

    Args:
        raw_dir: The importer's inbox

    Returns:
        One recovery per part file, by name
    """
    return [plan_recovery(path) for path in sorted(raw_dir.glob(f'*_ticks.json{PART_SUFFIX}'))]


def _describe(recovery: PartFileRecovery) -> str:
    """
    One line on what a part file yields.

    Args:
        recovery: The planned recovery

    Returns:
        The line
    """
    if recovery.refusal and not recovery.tick_count:
        return f'{recovery.part_path.name}: {recovery.refusal}'
    first = _format_msc(recovery.first_collected_msc)
    last = _format_msc(recovery.last_collected_msc)
    line = (f'{recovery.part_path.name}: {recovery.tick_count:,} ticks  {first} -> {last}  '
            f'drops {recovery.original_size_bytes - recovery.cut_at_byte} B')
    return f'{line}  REFUSED: {recovery.refusal}' if recovery.refusal else line


def _format_msc(msc: int) -> str:
    """
    Render a collected_msc stamp.

    Args:
        msc: Milliseconds since the epoch, UTC

    Returns:
        The instant as YYYY-MM-DD HH:MM:SS UTC
    """
    return datetime.fromtimestamp(msc / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def _detect_newline(data: bytes) -> bytes:
    """
    The line ending a collector file is written with.

    Args:
        data: The file's bytes

    Returns:
        CRLF when the file uses it, else LF
    """
    return b'\r\n' if b'\r\n' in data[:4096] else b'\n'


def main() -> int:
    """
    Parse the command line and run the phase.

    Returns:
        Exit code
    """
    parser = argparse.ArgumentParser(description='Finish MT5 tick files a hard stop left unfinished.')
    subparsers = parser.add_subparsers(dest='command', required=True)
    subparsers.add_parser('plan', help='report what each part file would yield; reads only')
    write_parser = subparsers.add_parser('write', help='write the finished files beside the part files')
    write_parser.add_argument('--reason', required=True,
                              help='why the files were left unfinished, written into each file')
    args = parser.parse_args()

    raw_dir = Path(ImportConfigManager().get_data_raw_path())
    if args.command == 'plan':
        return run_plan(raw_dir)
    return run_write(raw_dir, args.reason)


if __name__ == '__main__':
    sys.exit(main())
