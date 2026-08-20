"""
FiniexTestingIDE - Tick Import Validator
Structural validation of tick data at import time and across the archive

The importer validates and refuses — it never repairs. Repairing tick data is a
one-time migration (python/experiments/), not a permanent import path.

Two planes:
  1. Per file  — vectorized over the DataFrame the importer already holds
  2. Cross-file — over the tick index, without opening a single data file

Gap severity is deliberately NOT judged here. Whether a gap is a market closure
or an outage is a verdict and belongs to the coverage layer
(discoveries/data_coverage/data_coverage_report.py), which classifies gaps
against MarketCalendar.
"""

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from python.framework.types.validation_types import TickFileValidationResult

# Largest tolerated distance between collected_msc and the tick's UTC event time.
# This is not the receive lag alone. For repaired files it also carries the
# collector's accumulated session drift (measured ~1 s/day over sessions of 8-11
# days) plus the lift the restoration applies to keep arrival continuous across
# an anchor change. Worst case measured over the repaired archive: 30.1 s, from
# a 21.8 s lift plus 8.4 s of drift across an 8-day session. Five minutes leaves
# an order of magnitude of headroom while staying an order of magnitude below
# the smallest defect class (a 1 h timezone offset).
PLAUSIBLE_LAG_WINDOW_MS = 300_000

# A forward step in collected_msc beyond this is an anchor break, not a market
# gap. The largest legitimate intra-file gap measured across the archive is
# 48.17 h (a weekend); the smallest anchor jump is 21.35 d.
SEGMENT_SPLIT_FORWARD_MS = 7 * 24 * 3_600_000

# Tolerance between the timestamp string (second resolution) and time_msc.
TIMESTAMP_CONSISTENCY_TOLERANCE_MS = 1_000


def split_anchor_segments(collected: np.ndarray) -> List[Tuple[int, int]]:
    """
    Split a collected_msc series where the collector's anchor changed.

    A backwards step is always a break. A forward step beyond
    SEGMENT_SPLIT_FORWARD_MS is a break too — no market closure comes close to
    it, and every known anchor jump exceeds it by orders of magnitude.

    Module-level so the restoration migration works off the same definition
    rather than a second copy that could drift.

    Args:
        collected: collected_msc values in file order

    Returns:
        List of (start, end) index pairs, end exclusive
    """
    if len(collected) < 2:
        return [(0, len(collected))]

    deltas = np.diff(collected)
    breaks = np.where((deltas < 0) | (deltas > SEGMENT_SPLIT_FORWARD_MS))[0]

    bounds = [0] + [int(b) + 1 for b in breaks] + [len(collected)]
    return [(a, b) for a, b in zip(bounds[:-1], bounds[1:])]


class TickImportValidator:
    """
    Validates tick data against the invariants the archive is required to hold.

    Invariants (all measured against the real archive before being encoded):
    - time_msc and collected_msc never step backwards
    - collected_msc sits within PLAUSIBLE_LAG_WINDOW_MS of the tick's UTC event time
    - the declared tick count matches the delivered rows
    - prices are positive and not inverted
    - the timestamp string agrees with time_msc
    - across files of one symbol, coverage never overlaps
    """

    def validate_file(
        self,
        df: pd.DataFrame,
        file_name: str,
        declared_tick_count: Optional[int] = None,
        collected_msc_is_utc: bool = False
    ) -> TickFileValidationResult:
        """
        Validate one imported tick file.

        Expects the DataFrame after UTC offset application, so time_msc is
        already UTC and collected_msc can be compared against it directly.

        Args:
            df: Tick DataFrame as written to parquet
            file_name: Source file name, used in the report
            declared_tick_count: summary.total_ticks from the JSON, when present
            collected_msc_is_utc: True when the file declares collected_msc_timebase
                'utc' — such a file must satisfy the lag window, an older one is
                reported as needing the migration

        Returns:
            TickFileValidationResult carrying errors, warnings and metrics
        """
        result = TickFileValidationResult(is_valid=True, file_name=file_name)

        if df.empty:
            result.add_error("File contains no ticks")
            return result

        self._check_tick_count(df, declared_tick_count, result)
        self._check_prices(df, result)

        # Timing checks need both time columns. A file without them is not
        # rejected — pre-V1.3.0 exports legitimately lack collected_msc — but
        # nothing about its timing can be asserted either.
        if 'time_msc' not in df.columns:
            result.add_warning(
                "No time_msc column — tick timing cannot be validated")
            return result

        self._check_monotonic(df, result)
        self._check_timestamp_consistency(df, result)
        self._check_collected_msc_lag(df, collected_msc_is_utc, result)
        self._collect_burst_metrics(df, result)

        return result

    def _check_tick_count(
        self,
        df: pd.DataFrame,
        declared_tick_count: Optional[int],
        result: TickFileValidationResult
    ) -> None:
        """
        Compare the delivered row count against what the file declares.

        Args:
            df: Tick DataFrame
            declared_tick_count: summary.total_ticks, or None when absent
            result: Result to record findings on
        """
        if declared_tick_count is None:
            return

        if len(df) != declared_tick_count:
            result.add_error(
                f"Tick count mismatch: {len(df)} rows delivered, "
                f"summary.total_ticks declares {declared_tick_count}"
            )

    def _check_prices(self, df: pd.DataFrame, result: TickFileValidationResult) -> None:
        """
        Reject non-positive or inverted prices.

        Args:
            df: Tick DataFrame
            result: Result to record findings on
        """
        bid = df['bid'].to_numpy()
        ask = df['ask'].to_numpy()

        non_positive = int(((bid <= 0) | (ask <= 0)).sum())
        if non_positive > 0:
            result.add_error(f"{non_positive} ticks with bid or ask <= 0")

        inverted = int((ask < bid).sum())
        if inverted > 0:
            result.add_error(f"{inverted} ticks with ask < bid (inverted spread)")

    def _check_monotonic(self, df: pd.DataFrame, result: TickFileValidationResult) -> None:
        """
        Reject backwards steps in either time column.

        Non-decreasing, not strictly increasing: two ticks can genuinely share a
        millisecond, which is normal on burst-heavy feeds.

        Args:
            df: Tick DataFrame
            result: Result to record findings on
        """
        for column in ('time_msc', 'collected_msc'):
            if column not in df.columns:
                continue
            values = df[column].to_numpy().astype('int64')
            if len(values) < 2:
                continue

            deltas = np.diff(values)
            backwards = int((deltas < 0).sum())
            if backwards > 0:
                result.add_error(
                    f"{column} steps backwards {backwards}x "
                    f"(largest step {int(deltas.min())} ms)"
                )

    def _check_timestamp_consistency(
        self,
        df: pd.DataFrame,
        result: TickFileValidationResult
    ) -> None:
        """
        Verify the timestamp string and time_msc describe the same moment.

        Args:
            df: Tick DataFrame
            result: Result to record findings on
        """
        if 'timestamp' not in df.columns:
            return

        # Normalize to milliseconds first — pandas picks the datetime resolution
        # from the source, so astype('int64') alone is not unit-stable.
        stamp_ms = df['timestamp'].to_numpy().astype('datetime64[ms]').astype('int64')
        time_msc = df['time_msc'].to_numpy().astype('int64')
        deviation = np.abs(stamp_ms - time_msc)

        outside = int((deviation > TIMESTAMP_CONSISTENCY_TOLERANCE_MS).sum())
        if outside > 0:
            result.add_error(
                f"{outside} ticks where timestamp and time_msc disagree by more "
                f"than {TIMESTAMP_CONSISTENCY_TOLERANCE_MS} ms "
                f"(worst {int(deviation.max())} ms)"
            )

    def _check_collected_msc_lag(
        self,
        df: pd.DataFrame,
        collected_msc_is_utc: bool,
        result: TickFileValidationResult
    ) -> None:
        """
        Verify collected_msc sits plausibly close to the tick's event time.

        Uses the minimum lag per segment — the least-delayed sample is the most
        honest estimator of the clock offset, the same filter NTP uses.

        Args:
            df: Tick DataFrame
            collected_msc_is_utc: Whether the file declares the UTC timebase
            result: Result to record findings on
        """
        if 'collected_msc' not in df.columns:
            return

        collected = df['collected_msc'].to_numpy().astype('int64')
        time_msc = df['time_msc'].to_numpy().astype('int64')

        if not collected.any():
            result.add_warning(
                "collected_msc is zero throughout — pre-V1.3.0 data, no arrival "
                "timing available"
            )
            return

        segments = split_anchor_segments(collected)
        result.metrics['segments'] = float(len(segments))

        worst_lag = 0
        for start, end in segments:
            lag = int((collected[start:end] - time_msc[start:end]).min())
            if abs(lag) > abs(worst_lag):
                worst_lag = lag

        result.metrics['min_lag_ms'] = float(worst_lag)

        if abs(worst_lag) <= PLAUSIBLE_LAG_WINDOW_MS:
            return

        detail = (
            f"collected_msc sits {worst_lag} ms from the tick event time "
            f"(tolerated: +/-{PLAUSIBLE_LAG_WINDOW_MS} ms)"
        )
        if len(segments) > 1:
            detail += f", across {len(segments)} anchor segments"

        if collected_msc_is_utc:
            result.add_error(
                f"{detail}. The file declares collected_msc_timebase 'utc', so "
                f"this is a collector defect, not a legacy conversion gap."
            )
        else:
            result.add_error(
                f"{detail}. Legacy timing — run the collected_msc restoration "
                f"(python/experiments/restore_collected_msc_v3.py) before importing."
            )

    def _collect_burst_metrics(
        self,
        df: pd.DataFrame,
        result: TickFileValidationResult
    ) -> None:
        """
        Measure how many ticks share an arrival millisecond.

        Pure metric, never a verdict: MT5 and Kraken differ structurally here,
        and equal stamps are legitimate on a burst-heavy feed.

        Args:
            df: Tick DataFrame
            result: Result to record findings on
        """
        if 'collected_msc' not in df.columns:
            return

        collected = df['collected_msc'].to_numpy().astype('int64')
        if len(collected) < 2 or not collected.any():
            return

        simultaneous = int((np.diff(collected) == 0).sum())
        result.metrics['simultaneous_arrivals'] = float(simultaneous)
        result.metrics['simultaneous_share'] = simultaneous / (len(collected) - 1)

    def validate_archive_ordering(
        self,
        index_entries: Dict[str, Dict[str, List[Dict]]]
    ) -> List[str]:
        """
        Verify that files of one symbol never cover overlapping time ranges.

        Runs off the tick index, so no data file is opened. Which files follow
        each other is decided by the tick bounds, not by the file name and not by
        the header — a file opened at the Friday close carries its first tick
        48 h later, so both would mislead.

        Args:
            index_entries: Nested index structure {broker_type: {symbol: [entries]}}

        Returns:
            List of overlap findings, empty when the archive is ordered
        """
        findings: List[str] = []

        for broker_type, symbols in index_entries.items():
            for symbol, entries in symbols.items():
                # The index stores event times as ISO strings; parse before
                # comparing so an overlap is a real duration, not string
                # arithmetic. Arrival bounds are already epoch milliseconds.
                bounds = [
                    (pd.to_datetime(e['start_time'], utc=True),
                     pd.to_datetime(e['end_time'], utc=True),
                     int(e.get('collected_start', 0)),
                     int(e.get('collected_end', 0)),
                     e['file'])
                    for e in entries
                ]
                bounds.sort(key=lambda b: b[0])

                for previous, current in zip(bounds, bounds[1:]):
                    _, prev_end, _, prev_arrival_end, prev_file = previous
                    cur_start, _, cur_arrival_start, _, cur_file = current

                    if cur_start < prev_end:
                        findings.append(
                            f"{broker_type}/{symbol}: {prev_file} overlaps "
                            f"{cur_file} by {prev_end - cur_start} (event time)"
                        )

                    # Arrival is a physical sequence: a tick cannot be observed
                    # before one that arrived earlier. Zero means the index
                    # predates the arrival bounds — nothing to compare.
                    if not prev_arrival_end or not cur_arrival_start:
                        continue

                    if cur_arrival_start < prev_arrival_end:
                        findings.append(
                            f"{broker_type}/{symbol}: collected_msc steps back "
                            f"{prev_arrival_end - cur_arrival_start} ms from "
                            f"{prev_file} to {cur_file} (arrival time)"
                        )

        return findings
