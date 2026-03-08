"""One-shot report: collected_msc quality analysis across imported Parquets.

Reads all tick Parquet files from data/processed/, computes inter-tick
interval statistics based on collected_msc, and prints a summary report.
"""

import os
import sys

import numpy as np
import pyarrow.parquet as pq

PROCESSED_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'processed')
)


def _find_tick_parquets(base_dir: str) -> list:
    """Find all tick Parquet files grouped by broker/symbol.

    Args:
        base_dir: Root processed directory

    Returns:
        List of (broker_type, symbol, filepath) tuples
    """
    results = []
    for broker_type in sorted(os.listdir(base_dir)):
        ticks_dir = os.path.join(base_dir, broker_type, 'ticks')
        if not os.path.isdir(ticks_dir):
            continue
        for symbol in sorted(os.listdir(ticks_dir)):
            symbol_dir = os.path.join(ticks_dir, symbol)
            if not os.path.isdir(symbol_dir):
                continue
            for f in sorted(os.listdir(symbol_dir)):
                if f.endswith('.parquet'):
                    results.append((broker_type, symbol, os.path.join(symbol_dir, f)))
    return results


def _analyze_file(filepath: str) -> dict:
    """Analyze a single Parquet file for collected_msc quality.

    Args:
        filepath: Path to tick Parquet file

    Returns:
        Dict with analysis results
    """
    table = pq.read_table(filepath, columns=['time_msc', 'collected_msc'])
    time_msc = table.column('time_msc').to_numpy()
    collected_msc = table.column('collected_msc').to_numpy()
    n = len(time_msc)

    result = {
        'ticks': n,
        'has_collected_msc': not np.all(collected_msc == 0),
        'zeros': int(np.sum(collected_msc == 0)),
    }

    if n < 2:
        return result

    # Inter-tick intervals based on collected_msc
    deltas = np.diff(collected_msc).astype(np.float64)
    result['interval_min_ms'] = float(np.min(deltas))
    result['interval_max_ms'] = float(np.max(deltas))
    result['interval_mean_ms'] = float(np.mean(deltas))
    result['interval_median_ms'] = float(np.median(deltas))
    result['interval_p95_ms'] = float(np.percentile(deltas, 95))
    result['interval_p99_ms'] = float(np.percentile(deltas, 99))

    # Suspect intervals
    result['negative_intervals'] = int(np.sum(deltas < 0))
    result['zero_intervals'] = int(np.sum(deltas == 0))
    result['sub_5ms_intervals'] = int(np.sum((deltas > 0) & (deltas < 5)))
    result['over_60s_intervals'] = int(np.sum(deltas > 60000))
    result['over_5min_intervals'] = int(np.sum(deltas > 300000))

    # Monotonicity check
    result['is_monotonic'] = bool(np.all(deltas > 0))

    # Compare collected_msc vs time_msc drift
    drift = collected_msc - time_msc
    result['drift_min_ms'] = float(np.min(drift))
    result['drift_max_ms'] = float(np.max(drift))
    result['drift_mean_ms'] = float(np.mean(drift))

    # Synthetic detection: check if intervals are suspiciously uniform
    if n > 100:
        unique_intervals = len(np.unique(deltas[:1000]))
        result['unique_intervals_first1k'] = unique_intervals

    return result


def main() -> None:
    """Run collected_msc quality report."""
    if not os.path.isdir(PROCESSED_DIR):
        print(f"Directory not found: {PROCESSED_DIR}")
        sys.exit(1)

    files = _find_tick_parquets(PROCESSED_DIR)
    print(f"Found {len(files)} tick Parquet files\n")

    for broker_type, symbol, filepath in files:
        filename = os.path.basename(filepath)
        meta = pq.read_metadata(filepath).metadata or {}
        version = meta.get(b'source_meta_data_format_version', b'unknown').decode()

        print(f"{'=' * 70}")
        print(f"{broker_type}/{symbol} — {filename}  (v{version})")
        print(f"{'=' * 70}")

        stats = _analyze_file(filepath)

        print(f"  Ticks:              {stats['ticks']:,}")
        print(f"  collected_msc:      {'present' if stats['has_collected_msc'] else 'ALL ZEROS'}")
        print(f"  Zero values:        {stats['zeros']:,}")

        if stats['ticks'] < 2:
            print()
            continue

        print(f"  Monotonic:          {'YES' if stats['is_monotonic'] else 'NO'}")
        print()

        print(f"  Inter-tick intervals (collected_msc):")
        print(f"    Min:              {stats['interval_min_ms']:.0f} ms")
        print(f"    Max:              {stats['interval_max_ms']:,.0f} ms")
        print(f"    Mean:             {stats['interval_mean_ms']:,.1f} ms")
        print(f"    Median:           {stats['interval_median_ms']:,.1f} ms")
        print(f"    P95:              {stats['interval_p95_ms']:,.1f} ms")
        print(f"    P99:              {stats['interval_p99_ms']:,.1f} ms")
        print()

        print(f"  Suspect intervals:")
        print(f"    Negative:         {stats['negative_intervals']:,}")
        print(f"    Zero (0ms):       {stats['zero_intervals']:,}")
        print(f"    < 5ms:            {stats['sub_5ms_intervals']:,}")
        print(f"    > 60s:            {stats['over_60s_intervals']:,}")
        print(f"    > 5min:           {stats['over_5min_intervals']:,}")
        print()

        print(f"  Drift (collected_msc - time_msc):")
        print(f"    Min:              {stats['drift_min_ms']:,.0f} ms")
        print(f"    Max:              {stats['drift_max_ms']:,.0f} ms")
        print(f"    Mean:             {stats['drift_mean_ms']:,.1f} ms")

        if 'unique_intervals_first1k' in stats:
            print(f"  Unique intervals (first 1k): {stats['unique_intervals_first1k']}")

        print()

    print("Done.")


if __name__ == '__main__':
    main()