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

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from python.framework.types.config_types.market_config_types import (
    DayAnchorConfig,
    PriceFormation,
)
from python.framework.types.data_origin_types import (
    ORIGIN_BLOCK_REQUIRED_FROM,
    origin_block_is_required,
)
from python.framework.types.validation_types import TickFileValidationResult
from python.framework.utils.trading_day_anchor import boundary_opening

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

_HOUR_MS = 3_600_000

# A weekend closure seen inside one file: the last tick before the gap falls on a Friday in
# the market's own timezone and the gap lasts about two days. Measured on the MT5 archive:
# 47-49 h, the extremes being the weekends of a season change. A longer gap is an outage or
# a holiday, where the next tick says nothing about the server's clock.
WEEKEND_GAP_MIN_MS = 40 * _HOUR_MS
WEEKEND_GAP_MAX_MS = 56 * _HOUR_MS

# How far from the market's day anchor a regular week opens. Measured on the MT5 archive:
# 0-3 min after 17:00 New York, in both seasons, once the server clock is converted right.
WEEKEND_OPEN_TOLERANCE_MS = 30 * 60_000


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
    - a week that opens inside a file opens at the market's day anchor, not hours off it
    - across files of one symbol, coverage never overlaps
    """

    def validate_file(
        self,
        df: pd.DataFrame,
        file_name: str,
        declared_tick_count: Optional[int] = None,
        collected_msc_is_utc: bool = False,
        price_formation: Optional[PriceFormation] = None,
        data_format_version: str = '',
        stated_instance_id: Optional[str] = None,
        weekend_anchor: Optional[DayAnchorConfig] = None
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
            price_formation: How the venue forms its prices, from market_config. An
                order-driven venue must deliver a traded price; None skips the check
            data_format_version: What the file declares. At or above the origin boundary a
                missing identity is a defect rather than history; below it, nothing is asked
            stated_instance_id: The identity read out of the file's `origin` block, or None
                when the block is absent or carries none
            weekend_anchor: Where the market's day flips, for a market that closes on
                weekends; None for one that trades around the clock, which skips the check

        Returns:
            TickFileValidationResult carrying errors, warnings and metrics
        """
        result = TickFileValidationResult(is_valid=True, file_name=file_name)

        if df.empty:
            result.add_error('File contains no ticks')
            return result

        self._check_tick_count(df, declared_tick_count, result)
        self._check_prices(df, result)
        self._check_traded_price(df, price_formation, result)
        self._check_origin_block(data_format_version, stated_instance_id, result)

        # Timing checks need both time columns. A file without them is not
        # rejected — pre-V1.3.0 exports legitimately lack collected_msc — but
        # nothing about its timing can be asserted either.
        if 'time_msc' not in df.columns:
            result.add_warning(
                'No time_msc column — tick timing cannot be validated')
            return result

        self._check_monotonic(df, result)
        self._check_timestamp_consistency(df, result)
        self._check_collected_msc_lag(df, collected_msc_is_utc, result)
        self._check_weekend_open(df, weekend_anchor, result)
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
                f'Tick count mismatch: {len(df)} rows delivered, '
                f'summary.total_ticks declares {declared_tick_count}'
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
            result.add_error(f'{non_positive} ticks with bid or ask <= 0')

        inverted = int((ask < bid).sum())
        if inverted > 0:
            result.add_error(f'{inverted} ticks with ask < bid (inverted spread)')

    def _check_traded_price(
        self,
        df: pd.DataFrame,
        price_formation: Optional[PriceFormation],
        result: TickFileValidationResult
    ) -> None:
        """
        An order-driven venue must deliver a traded price on every tick.

        This is what turns `price_formation` from a declaration into something checkable.
        Where trades print centrally, `last` is a real event and its absence is malformed
        producer output — not a reason to fall back quietly to the midpoint, which is what
        would otherwise happen and would change what a bar MEANS without anyone noticing.

        A quote-driven venue is not checked: it has no traded price by construction and
        writes 0.0, which is an absence rather than a defect.

        Args:
            df: Tick DataFrame
            price_formation: Declared formation, or None to skip
            result: Result to record findings on
        """
        if price_formation != PriceFormation.ORDER_DRIVEN:
            return

        if 'last' not in df.columns:
            result.add_error(
                'Venue is declared order_driven but the file carries no `last` column — '
                'a traded price is expected on every tick'
            )
            return

        missing = int((df['last'].fillna(0.0) <= 0).sum())
        if missing > 0:
            result.add_error(
                f'{missing} ticks without a traded price, on a venue declared '
                f'order_driven — `last` must be positive where trades print'
            )

    def _check_origin_block(
        self,
        data_format_version: str,
        stated_instance_id: Optional[str],
        result: TickFileValidationResult
    ) -> None:
        """
        From the origin boundary on, a producer must state who wrote the file.

        Below the boundary a missing identity is history: those files were written before the
        block existed and a dated attestation is what covers them. At or above it the producer
        has the field and left it empty, which is malformed output — and the damage is that it
        is SILENT. The file imports, resolves to `unknown`, and nothing names the producer that
        stopped identifying itself. Refusing here is the same shape the traded-price check
        uses, and for the same reason: an absence that reaches the archive unremarked becomes
        indistinguishable from an absence that was always expected.

        Args:
            data_format_version: What the file declares
            stated_instance_id: The identity from its `origin` block, or None
            result: Collects the error
        """
        if not origin_block_is_required(data_format_version):
            return
        if stated_instance_id:
            return

        result.add_error(
            f'No origin identity in a {data_format_version} file. From '
            f'{ORIGIN_BLOCK_REQUIRED_FROM} a producer states its own identity, so this is a '
            f'defective producer rather than an archive written before the block existed — '
            f'an attestation cannot and must not cover it.')

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
                    f'{column} steps backwards {backwards}x '
                    f'(largest step {int(deltas.min())} ms)'
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
                f'{outside} ticks where timestamp and time_msc disagree by more '
                f'than {TIMESTAMP_CONSISTENCY_TOLERANCE_MS} ms '
                f'(worst {int(deviation.max())} ms)'
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
                'collected_msc is zero throughout — pre-V1.3.0 data, no arrival '
                'timing available'
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
            f'collected_msc sits {worst_lag} ms from the tick event time '
            f'(tolerated: +/-{PLAUSIBLE_LAG_WINDOW_MS} ms)'
        )
        if len(segments) > 1:
            detail += f', across {len(segments)} anchor segments'

        whole_hours = int(round(worst_lag / _HOUR_MS))
        if collected_msc_is_utc and whole_hours and \
                abs(worst_lag - whole_hours * _HOUR_MS) <= PLAUSIBLE_LAG_WINDOW_MS:
            # Late delivery is never this exact. Two clocks a whole hour apart are: the event
            # time went through one conversion and the arrival time through another.
            result.add_error(
                f"{detail}. That is {whole_hours:+d} h almost exactly, which is a clock "
                f"conversion, not a late delivery: the event time and the arrival time were "
                f"converted by rules a whole hour apart. Check the broker's server_clock in "
                f"market_config.json, or whether the arrival time was restored under an older "
                f"rule."
            )
        elif collected_msc_is_utc:
            result.add_error(
                f"{detail}. The file declares collected_msc_timebase 'utc', so "
                f"this is a collector defect, not a legacy conversion gap."
            )
        else:
            result.add_error(
                f'{detail}. Legacy timing — run the collected_msc restoration '
                f'(python/experiments/restore_collected_msc_v3.py) before importing.'
            )

    def _check_weekend_open(
        self,
        df: pd.DataFrame,
        weekend_anchor: Optional[DayAnchorConfig],
        result: TickFileValidationResult
    ) -> None:
        """
        Verify that a week opening inside this file opens at the market's day anchor.

        The only check that sees a wrong server clock on a file whose arrival time was
        restored from its own event time — there the arrival check cannot, because both
        times carry the same error. A regular week opens a few minutes after the anchor
        (17:00 New York for forex); a first tick a whole number of hours away from it is a
        clock conversion off by those hours and refuses the file. An open that is late by
        anything else — a holiday, a feed gap — is only reported.

        Args:
            df: Tick DataFrame, time_msc already converted to UTC
            weekend_anchor: Where the market's day flips; None skips the check
            result: Result to record findings on
        """
        if weekend_anchor is None or len(df) < 2:
            return

        time_msc = df['time_msc'].to_numpy().astype('int64')
        gaps = np.diff(time_msc)
        candidates = np.where(
            (gaps >= WEEKEND_GAP_MIN_MS) & (gaps <= WEEKEND_GAP_MAX_MS))[0]
        if not len(candidates):
            return

        zone = ZoneInfo(weekend_anchor.timezone)
        checked = 0
        for index in candidates:
            closed = datetime.fromtimestamp(int(time_msc[index]) / 1000, tz=timezone.utc)
            if closed.astimezone(zone).weekday() != 4:
                continue
            checked += 1
            opened = datetime.fromtimestamp(int(time_msc[index + 1]) / 1000, tz=timezone.utc)
            local_day = opened.astimezone(zone).date()
            anchor = min(
                (boundary_opening(local_day + timedelta(days=step), weekend_anchor)
                 for step in (-1, 0, 1)),
                key=lambda boundary: abs((opened - boundary).total_seconds()))
            deviation_ms = int((opened - anchor).total_seconds() * 1000)
            if abs(deviation_ms) <= WEEKEND_OPEN_TOLERANCE_MS:
                continue

            stated = (f'The week opens at {opened:%Y-%m-%d %H:%M} UTC, '
                      f'{deviation_ms / 60_000:+.0f} min from its anchor '
                      f'({weekend_anchor.local_time} {weekend_anchor.timezone} = '
                      f'{anchor:%H:%M} UTC)')
            whole_hours = int(round(deviation_ms / _HOUR_MS))
            if whole_hours and \
                    abs(deviation_ms - whole_hours * _HOUR_MS) <= PLAUSIBLE_LAG_WINDOW_MS:
                result.add_error(
                    f"{stated}: {whole_hours:+d} h almost exactly, so the server clock was "
                    f"converted by the wrong number of hours. Check the broker's server_clock "
                    f"in market_config.json.")
            else:
                result.add_warning(
                    f'{stated} — later than a regular open (a holiday or a feed gap)')

        result.metrics['weekend_opens_checked'] = float(checked)

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
        unchecked = 0
        transitions = 0

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
                transitions += max(len(bounds) - 1, 0)

                for previous, current in zip(bounds, bounds[1:]):
                    _, prev_end, _, prev_arrival_end, prev_file = previous
                    cur_start, _, cur_arrival_start, _, cur_file = current

                    if cur_start < prev_end:
                        findings.append(
                            f'{broker_type}/{symbol}: {prev_file} overlaps '
                            f'{cur_file} by {prev_end - cur_start} (event time)'
                        )

                    # Arrival is a physical sequence: a tick cannot be observed
                    # before one that arrived earlier. Zero means the index
                    # carries no arrival bounds — nothing to compare, and the
                    # skip is reported rather than silent: an unverified plane
                    # that logs like a passed one is worse than no plane.
                    if not prev_arrival_end or not cur_arrival_start:
                        unchecked += 1
                        continue

                    if cur_arrival_start < prev_arrival_end:
                        findings.append(
                            f'{broker_type}/{symbol}: collected_msc steps back '
                            f'{prev_arrival_end - cur_arrival_start} ms from '
                            f'{prev_file} to {cur_file} (arrival time)'
                        )

        if unchecked:
            findings.append(
                f'Arrival bounds missing on {unchecked} of {transitions} file '
                f'transitions — collected_msc continuity NOT verified there. '
                f'Rebuild the tick index.'
            )

        return findings
