"""One-shot script to backfill a scalar envelope field in raw signal JSONL archives.

The RAG producer gained its provenance fields (`data_origin`, later
`config_fingerprint`) after archives had already been collected, so every
envelope written before the deployment lacks them. This tool stamps a known
constant into those envelopes so the archive is uniform.

Only valid where the value is genuinely known for the WHOLE folder — e.g.
`data_origin=live` for a folder that only ever held producer output. It cannot
derive anything; it writes what it is told.

Method (deliberate, do not "simplify"):
  - BINARY mode throughout. The archives use CRLF line endings; text mode
    silently rewrites them to LF and corrupts the byte-identity of the audit source.
  - Surgical byte insert directly after the opening brace — the envelope is never
    parsed or re-serialized. Re-serializing would change float formatting and
    escape non-ASCII in the news titles. The raw JSONL is the audit source; only
    the inserted token may differ.
  - Idempotent: an envelope already carrying the key is written through unchanged.
  - Atomic per file (temp + os.replace), guarded by an exact size check that
    aborts if anything but the token changed.

Field order is not preserved: the key lands first in the object. JSON key order
carries no meaning, and the producer's own order already differs between its
generator and its live export.

Date bounds (`--from-date` / `--until-date`) select whole archive buckets by their
file name, which IS the bucket date per the archive layout contract. Bounding the
run matters for two reasons: a value may only be valid from a known date on (a
producer config change), and an upper bound keeps a later re-run from stamping
days that were collected after the value was established.

Usage:
    python python/experiments/restore_signal_envelope_field.py \\
        --field data_origin --value live \\
        --folder crypto_sentiment --folder forex_macro_sentiment [--dry-run]

    python python/experiments/restore_signal_envelope_field.py \\
        --field config_fingerprint --value 904c2e16bbfb --folder crypto_sentiment \\
        --from-date 2026-07-24 --until-date 2026-08-16
"""

import argparse
import glob
import os
import sys
from datetime import date
from typing import List, Optional, Tuple

SIGNAL_RAW_ROOT = 'data/raw/signals'


def stamp_file(path: str, token: bytes, key: bytes, dry_run: bool) -> Tuple[int, int, int]:
    """
    Stamp one JSONL file, writing atomically.

    Args:
        path: Raw JSONL file path
        token: Complete insert including key, value, comma and space
        key: Presence marker used for the idempotency check
        dry_run: Count only, write nothing

    Returns:
        Tuple of (lines seen, envelopes stamped, envelopes already carrying the key)
    """
    size_before = os.path.getsize(path)
    tmp = f'{path}.tmp'
    inserted = kept = lines = 0

    with open(path, 'rb') as src:
        dst = None if dry_run else open(tmp, 'wb')
        try:
            for line in src:
                if not line.strip():
                    if dst:
                        dst.write(line)
                    continue
                lines += 1
                if key in line:
                    if dst:
                        dst.write(line)
                    kept += 1
                    continue
                brace = line.index(b'{')
                if dst:
                    dst.write(line[:brace + 1] + token + line[brace + 1:])
                inserted += 1
        finally:
            if dst:
                dst.close()

    if dry_run:
        return lines, inserted, kept

    # Only the token may have been added — anything else means the byte identity broke
    expected = size_before + inserted * len(token)
    size_after = os.path.getsize(tmp)
    if size_after != expected:
        os.remove(tmp)
        sys.exit(
            f"ABORT {path}: {size_after} bytes, expected {expected} — "
            f"only the token may have been added (CRLF rewritten?)"
        )

    os.replace(tmp, path)
    return lines, inserted, kept


def bucket_date(path: str) -> Optional[date]:
    """
    The archive bucket date a file name encodes.

    Args:
        path: JSONL file path

    Returns:
        The parsed date, or None when the stem is not a plain YYYY-MM-DD bucket
    """
    stem = os.path.basename(path)[:-len('.jsonl')]
    try:
        return date.fromisoformat(stem)
    except ValueError:
        return None


def in_range(path: str, from_date: Optional[date],
             until_date: Optional[date]) -> bool:
    """
    Whether a bucket falls inside the requested date bounds.

    A file whose name is not a date bucket is EXCLUDED whenever bounds are given —
    silently stamping an unbounded file would defeat the purpose of the bound.

    Args:
        path: JSONL file path
        from_date: Lower bound, inclusive (None = open)
        until_date: Upper bound, inclusive (None = open)

    Returns:
        True when the file should be processed
    """
    if from_date is None and until_date is None:
        return True

    bucket = bucket_date(path)
    if bucket is None:
        print(f'   {os.path.basename(path):18} SKIPPED — name is not a date bucket')
        return False

    return not (from_date and bucket < from_date) and \
        not (until_date and bucket > until_date)


def run(field: str, value: str, folders: List[str], dry_run: bool,
        from_date: Optional[date] = None,
        until_date: Optional[date] = None) -> None:
    """
    Stamp the field across the JSONL files of the given signal source folders.

    Args:
        field: Envelope key to insert
        value: Constant string value
        folders: Signal source folder names under data/raw/signals/
        dry_run: Count only, write nothing
        from_date: Only buckets on/after this date (None = open)
        until_date: Only buckets on/before this date (None = open)
    """
    token = f'"{field}": "{value}", '.encode('utf-8')
    key = f'"{field}":'.encode('utf-8')

    total_lines = total_inserted = total_kept = total_skipped = 0

    for folder in folders:
        files = sorted(glob.glob(os.path.join(SIGNAL_RAW_ROOT, folder, '*.jsonl')))
        if not files:
            print(f"\n=== {folder}: no JSONL found — skipped")
            continue

        print(f"\n=== {folder}: {len(files)} file(s)")
        for path in files:
            if not in_range(path, from_date, until_date):
                total_skipped += 1
                continue
            lines, inserted, kept = stamp_file(path, token, key, dry_run)
            total_lines += lines
            total_inserted += inserted
            total_kept += kept
            print(f'   {os.path.basename(path):18} {lines:4} lines  '
                  f'+{inserted:4} stamped  {kept:3} already present')

    mode = 'WOULD STAMP' if dry_run else 'stamped'
    print(f"\nTotal: {total_lines:,} lines · {total_inserted:,} {mode} · "
          f"{total_kept:,} unchanged · {total_skipped} file(s) outside the date bounds")


def main() -> None:
    """Parse arguments and run the backfill."""
    parser = argparse.ArgumentParser(
        description='Backfill a scalar envelope field in raw signal JSONL archives.')
    parser.add_argument('--field', required=True,
                        help="Envelope key to insert (e.g. 'data_origin')")
    parser.add_argument('--value', required=True,
                        help="Constant value to write (e.g. 'live')")
    parser.add_argument('--folder', required=True, action='append', dest='folders',
                        help='Signal source folder under data/raw/signals (repeatable)')
    parser.add_argument('--from-date', default=None, metavar='YYYY-MM-DD',
                        help='Only buckets on/after this date (inclusive)')
    parser.add_argument('--until-date', default=None, metavar='YYYY-MM-DD',
                        help='Only buckets on/before this date (inclusive) — bound a '
                             'run so a later re-run cannot touch newer days')
    parser.add_argument('--dry-run', action='store_true', default=False,
                        help='Count only, write nothing')
    args = parser.parse_args()

    from_date = date.fromisoformat(args.from_date) if args.from_date else None
    until_date = date.fromisoformat(args.until_date) if args.until_date else None

    run(args.field, args.value, args.folders, args.dry_run, from_date, until_date)


if __name__ == '__main__':
    main()
