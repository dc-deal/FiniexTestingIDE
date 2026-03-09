"""One-shot script to backfill collected_msc in raw JSON files lacking it.

Processes data/finished/ grouped by symbol. Synthesizes collected_msc from
existing tick data since the original collection timestamp was not recorded
in pre-V1.3.0 data.

Strategy per broker_type:
  MT5:    Interpolate within same-second groups (timestamp-based epoch + even spacing)
  Kraken: Ticker ticks (BID ASK) → copy time_msc; Trade ticks (BUY/SELL) → time_msc + index offset

Cross-file harmonization: All files for one symbol are processed globally to
ensure monotonicity across file boundaries.
"""

import json
import os
import sys
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

FINISHED_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'finished')
)

# Dry-run by default — pass --apply to write changes
# --force re-synthesizes collected_msc even if already present
DRY_RUN = '--apply' not in sys.argv
FORCE = '--force' in sys.argv


def _parse_timestamp_to_epoch_ms(ts_str: str) -> int:
    """Parse 'YYYY.MM.DD HH:MM:SS' to epoch milliseconds.

    Args:
        ts_str: Timestamp string in MQL5 format

    Returns:
        Epoch milliseconds (int)
    """
    dt = datetime.strptime(ts_str, '%Y.%m.%d %H:%M:%S')
    return int(dt.timestamp() * 1000)


def _add_hours_to_timestamp(ts_str: str, hours: int) -> str:
    """Add hours to a timestamp string.

    Args:
        ts_str: Timestamp string in 'YYYY.MM.DD HH:MM:SS' format
        hours: Hours to add (can be negative)

    Returns:
        Adjusted timestamp string in same format
    """
    dt = datetime.strptime(ts_str, '%Y.%m.%d %H:%M:%S')
    dt += timedelta(hours=hours)
    return dt.strftime('%Y.%m.%d %H:%M:%S')


def _get_broker_type(metadata: OrderedDict) -> str:
    """Extract broker_type from metadata, falling back to data_collector.

    Args:
        metadata: File metadata dict

    Returns:
        Broker type string ('mt5' or 'kraken_spot')
    """
    return metadata.get('broker_type', metadata.get('data_collector', 'unknown'))


def _get_symbol(metadata: OrderedDict) -> str:
    """Extract symbol from metadata.

    Args:
        metadata: File metadata dict

    Returns:
        Symbol string
    """
    return metadata.get('symbol', 'UNKNOWN')


def _get_start_time_unix(metadata: OrderedDict) -> int:
    """Extract start_time_unix for file ordering.

    Args:
        metadata: File metadata dict

    Returns:
        Unix timestamp (seconds) or 0 if not available
    """
    val = metadata.get('start_time_unix')
    if val is not None:
        return int(val)
    # Fallback: parse start_time string
    start_time = metadata.get('start_time', '')
    if start_time:
        try:
            return int(_parse_timestamp_to_epoch_ms(start_time) / 1000)
        except ValueError:
            pass
    return 0


def _synthesize_mt5(ticks: List[OrderedDict]) -> None:
    """Synthesize collected_msc for MT5 ticks using timestamp interpolation.

    Within each second (same timestamp string), ticks are evenly spaced
    across the 1000ms window. Across seconds, values naturally increase.

    Args:
        ticks: List of tick dicts (modified in place)
    """
    i = 0
    n = len(ticks)
    while i < n:
        ts_str = ticks[i].get('timestamp', '')
        base_ms = _parse_timestamp_to_epoch_ms(ts_str)

        # Find group of ticks with same timestamp
        j = i + 1
        while j < n and ticks[j].get('timestamp', '') == ts_str:
            j += 1

        group_size = j - i
        for k in range(group_size):
            if group_size == 1:
                offset = 0
            else:
                # Spread evenly across 0..999ms
                offset = int(k * 999 / (group_size - 1))
            ticks[i + k]['collected_msc'] = base_ms + offset

        i = j


def _synthesize_kraken(ticks: List[OrderedDict]) -> None:
    """Synthesize collected_msc for Kraken ticks.

    Ticker ticks (BID ASK): time_msc is already local receive time → copy.
    Trade ticks (BUY/SELL): Multiple fills share time_msc → add 1ms per fill
    within the same time_msc group.

    Args:
        ticks: List of tick dicts (modified in place)
    """
    i = 0
    n = len(ticks)
    while i < n:
        time_msc = ticks[i].get('time_msc', 0)
        flags = ticks[i].get('tick_flags', '')

        if flags == 'BID ASK':
            # Ticker tick — time_msc is local receive time
            ticks[i]['collected_msc'] = time_msc
            i += 1
        else:
            # Trade tick — find group with same time_msc
            j = i + 1
            while j < n and ticks[j].get('time_msc', 0) == time_msc:
                j += 1

            for k in range(j - i):
                ticks[i + k]['collected_msc'] = time_msc + k

            i = j


def _ensure_global_monotonicity(ticks: List[OrderedDict]) -> int:
    """Post-process: fix any non-monotonic collected_msc values.

    If a tick's collected_msc <= previous tick's, bump it to previous + 1.
    This handles edge cases at file boundaries.

    Args:
        ticks: List of tick dicts (modified in place)

    Returns:
        Number of corrections made
    """
    corrections = 0
    for i in range(1, len(ticks)):
        if ticks[i]['collected_msc'] <= ticks[i - 1]['collected_msc']:
            ticks[i]['collected_msc'] = ticks[i - 1]['collected_msc'] + 1
            corrections += 1
    return corrections


def _load_file(filepath: str) -> Tuple[OrderedDict, str]:
    """Load a JSON file preserving key order.

    Args:
        filepath: Path to JSON file

    Returns:
        Tuple of (parsed data, broker_type)
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.loads(f.read(), object_pairs_hook=OrderedDict)
    broker_type = _get_broker_type(data.get('metadata', {}))
    return data, broker_type


def _write_file(filepath: str, data: OrderedDict) -> None:
    """Write JSON file back preserving formatting.

    Args:
        filepath: Path to write
        data: JSON data dict
    """
    text = json.dumps(data, indent=2, ensure_ascii=False) + '\n'
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(text)


def process_symbol_files(filepaths: List[str], broker_type: str) -> Dict[str, int]:
    """Process all files for a single symbol globally.

    Loads all ticks across files, synthesizes collected_msc globally,
    ensures monotonicity, and writes back to original files.

    Args:
        filepaths: Sorted list of file paths for this symbol
        broker_type: 'mt5' or 'kraken_spot'

    Returns:
        Dict with stats: total_ticks, files, corrections, skipped
    """
    stats = {'total_ticks': 0, 'files': len(filepaths), 'corrections': 0, 'skipped': 0,
             'server_time_removed': 0, 'metadata_fixed': 0}

    # Load all files and collect ticks with file-of-origin
    file_data: List[Tuple[str, OrderedDict]] = []
    all_ticks: List[OrderedDict] = []
    tick_file_indices: List[int] = []  # maps tick index → file index

    for file_idx, filepath in enumerate(filepaths):
        data, _ = _load_file(filepath)
        metadata = data.get('metadata', {})
        ticks = data.get('ticks', [])

        # Fix metadata timestamps: local_device_time and broker_server_time
        # Phase 2a bug: written at finalization, not start
        # Kraken UTC bug: local_device_time written as UTC instead of local clock
        start_time = metadata.get('start_time', '')
        needs_meta_fix = False
        if start_time:
            if broker_type == 'kraken_spot':
                # Kraken: start_time is UTC, local server is GMT+1
                correct_local = _add_hours_to_timestamp(start_time, 1)
                correct_broker = start_time  # Kraken server is UTC
            else:
                # MT5: start_time is broker time (GMT+3), local server is GMT+1
                correct_local = _add_hours_to_timestamp(start_time, -2)
                correct_broker = start_time  # MT5 broker is GMT+3
            if metadata.get('local_device_time', '') != correct_local:
                metadata['local_device_time'] = correct_local
                needs_meta_fix = True
            if metadata.get('broker_server_time', '') != correct_broker:
                metadata['broker_server_time'] = correct_broker
                needs_meta_fix = True
        if needs_meta_fix:
            stats['metadata_fixed'] += 1

        # Remove server_time from all ticks (legacy field, redundant with time_msc)
        has_server_time = ticks and 'server_time' in ticks[0]
        if has_server_time:
            for tick in ticks:
                tick.pop('server_time', None)
            stats['server_time_removed'] += len(ticks)

        # Check if already has collected_msc (V1.3.0 data)
        if ticks and 'collected_msc' in ticks[0] and not FORCE:
            stats['skipped'] += 1
            file_data.append((filepath, data))
            # Still need to write back if server_time or metadata was fixed
            if (has_server_time or needs_meta_fix) and not DRY_RUN:
                _write_file(filepath, data)
            continue

        file_data.append((filepath, data))
        for tick in ticks:
            all_ticks.append(tick)
            tick_file_indices.append(file_idx)

    if not all_ticks:
        return stats

    stats['total_ticks'] = len(all_ticks)

    # Synthesize collected_msc in JSON array order (= authentic arrival order)
    if broker_type == 'mt5':
        _synthesize_mt5(all_ticks)
    elif broker_type == 'kraken_spot':
        _synthesize_kraken(all_ticks)
    else:
        print(f"  WARNING: Unknown broker_type '{broker_type}', using MT5 strategy")
        _synthesize_mt5(all_ticks)

    # Ensure global monotonicity across file boundaries
    stats['corrections'] = _ensure_global_monotonicity(all_ticks)

    if DRY_RUN:
        return stats

    # Write ticks back to their original files in original order
    ticks_per_file: Dict[int, List[OrderedDict]] = {}
    for tick_idx, file_idx in enumerate(tick_file_indices):
        if file_idx not in ticks_per_file:
            ticks_per_file[file_idx] = []
        ticks_per_file[file_idx].append(all_ticks[tick_idx])

    for file_idx, (filepath, data) in enumerate(file_data):
        if file_idx in ticks_per_file:
            data['ticks'] = ticks_per_file[file_idx]
            _write_file(filepath, data)

    return stats


def main() -> None:
    """Discover and process all raw JSON files grouped by symbol."""
    raw_dir = FINISHED_DIR
    if not os.path.isdir(raw_dir):
        print(f"Directory not found: {raw_dir}")
        sys.exit(1)

    mode = 'DRY RUN' if DRY_RUN else 'APPLY MODE'
    force_label = ' + FORCE (re-synthesize all)' if FORCE else ''
    if DRY_RUN:
        print(f"=== {mode}{force_label} (pass --apply to write changes) ===\n")
    else:
        print(f"=== {mode}{force_label} — writing changes ===\n")

    # Discover all JSON files
    json_files = sorted(f for f in os.listdir(raw_dir) if f.endswith('_ticks.json'))
    print(f"Found {len(json_files)} JSON files in {raw_dir}\n")

    if not json_files:
        print('No files to process.')
        return

    # Group by symbol, track broker_type and start_time_unix per file
    symbol_files: Dict[str, List[Tuple[int, str, str]]] = {}  # symbol → [(start_unix, filepath, broker_type)]

    for filename in json_files:
        filepath = os.path.join(raw_dir, filename)
        data, broker_type = _load_file(filepath)
        metadata = data.get('metadata', {})
        symbol = _get_symbol(metadata)
        start_unix = _get_start_time_unix(metadata)

        if symbol not in symbol_files:
            symbol_files[symbol] = []
        symbol_files[symbol].append((start_unix, filepath, broker_type))

    # Process per symbol
    total_stats = {'symbols': 0, 'files': 0, 'ticks': 0, 'corrections': 0, 'skipped': 0,
                    'server_time_removed': 0, 'metadata_fixed': 0}

    for symbol in sorted(symbol_files.keys()):
        entries = sorted(symbol_files[symbol], key=lambda x: x[0])  # sort by start_time_unix
        broker_type = entries[0][2]  # all files for a symbol share broker_type
        filepaths = [e[1] for e in entries]

        print(f"Processing {symbol} ({broker_type}): {len(filepaths)} files")

        stats = process_symbol_files(filepaths, broker_type)

        total_stats['symbols'] += 1
        total_stats['files'] += stats['files']
        total_stats['ticks'] += stats['total_ticks']
        total_stats['corrections'] += stats['corrections']
        total_stats['skipped'] += stats['skipped']
        total_stats['server_time_removed'] += stats['server_time_removed']
        total_stats['metadata_fixed'] += stats['metadata_fixed']

        detail = f"  {stats['total_ticks']} ticks"
        if stats['corrections']:
            detail += f", {stats['corrections']} monotonicity corrections"
        if stats['skipped']:
            detail += f", {stats['skipped']} files skipped (already have collected_msc)"
        if stats['server_time_removed']:
            detail += f", server_time removed from {stats['server_time_removed']} ticks"
        if stats['metadata_fixed']:
            detail += f", {stats['metadata_fixed']} files metadata fixed"
        print(detail)

    # Summary
    print(f"\n{'=' * 50}")
    print(f"SUMMARY {'(DRY RUN)' if DRY_RUN else '(APPLIED)'}")
    print(f"{'=' * 50}")
    print(f"  Symbols:     {total_stats['symbols']}")
    print(f"  Files:       {total_stats['files']}")
    print(f"  Ticks:       {total_stats['ticks']}")
    print(f"  Corrections: {total_stats['corrections']}")
    print(f"  Skipped:     {total_stats['skipped']}")
    print(f"  server_time: {total_stats['server_time_removed']} ticks cleaned")
    print(f"  Meta fixed:  {total_stats['metadata_fixed']} files (local_device_time/broker_server_time → start_time)")


if __name__ == '__main__':
    main()
