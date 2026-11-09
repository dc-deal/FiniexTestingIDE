"""
Measure 3 of the divergence report (#512) — how far the two runs' account values ran apart.

Over the cadence slots both runs valued, and at each compared day's opening and closing: the gap,
live minus backtest, and each day's change of value in % of its opening value. The days' differences
are then read as a series — their mean, their spread, a 90 % interval on the mean, and whether one
day follows the day before. Reported, never binding in this run. Pure: it reads the frames and rows
it is handed.
"""

from bisect import bisect_left
from datetime import datetime
from typing import Dict, List, Optional, Sequence

from python.framework.reporting.builders.divergence.gap_list_builder import in_excluded
from python.framework.types.api.divergence_report_types import (
    DailyDifferenceStatistics,
    DayEquityDifference,
    EquityGap,
    EquityTracking,
    ExcludedSpan,
    TradingDayClassification,
)
from python.framework.types.api.report_types import RunResultRow
from python.framework.types.api.state_frame_types import StateFrame
from python.framework.utils.paired_difference_statistics import (
    LJUNG_BOX_LAG_ONE_CRITICAL_5PCT,
    lag_one_autocorrelation,
    ljung_box_lag_one,
    mean_of,
    ninety_percent_interval,
    tracking_error_of,
)


def track_equity(days: Sequence[TradingDayClassification], live_frames: Sequence[StateFrame],
                 backtest_frames: Sequence[StateFrame],
                 live_rows: Dict[datetime, RunResultRow],
                 backtest_rows: Dict[datetime, RunResultRow],
                 excluded: Sequence[ExcludedSpan] = ()) -> EquityTracking:
    """
    The two runs' account values over a segment's compared days.

    Args:
        days: The segment's days; only the compared ones are measured
        live_frames: The live sessions' frames, in time order
        backtest_frames: The backtest scenario's frames, in time order
        live_rows: Each compared day's live ledger row, by its closing
        backtest_rows: The backtest's row closing at the same boundary, by that closing
        excluded: The stretches the window's gaps leave out — no slot inside one is compared

    Returns:
        The slots' gaps, every compared day and the days' differences as a series
    """
    compared = [day for day in days if day.included]
    shared = shared_slot_gaps(compared, live_frames, backtest_frames)
    gaps = [gap for gap in shared if not in_excluded(gap.at, excluded)]
    day_differences = [day_equity_difference(day, live_frames, backtest_frames,
                                              live_rows.get(day.closing),
                                              backtest_rows.get(day.closing))
                       for day in compared]
    return EquityTracking(
        shared_slots=len(gaps), slots_in_gaps=len(shared) - len(gaps),
        largest_gap=max(gaps, key=lambda gap: abs(gap.gap), default=None),
        gap_at_end=gaps[-1] if gaps else None,
        days=day_differences,
        daily=daily_statistics([difference.difference_pct for difference in day_differences
                                if difference.difference_pct is not None]))


def shared_slot_gaps(days: Sequence[TradingDayClassification],
                     live_frames: Sequence[StateFrame],
                     backtest_frames: Sequence[StateFrame]) -> List[EquityGap]:
    """
    The account values of every cadence slot both runs valued inside a compared day.

    Args:
        days: The compared days
        live_frames: The live frames
        backtest_frames: The backtest's frames

    Returns:
        One gap per shared slot, in time order
    """
    live = _valued_slots(live_frames)
    backtest = _valued_slots(backtest_frames)
    gaps: List[EquityGap] = []
    for slot in sorted(set(live) & set(backtest)):
        if any(day.opening <= slot < day.closing for day in days):
            gaps.append(EquityGap(at=slot, live_equity=live[slot], backtest_equity=backtest[slot],
                                  gap=live[slot] - backtest[slot]))
    return gaps


def day_equity_difference(day: TradingDayClassification, live_frames: Sequence[StateFrame],
                          backtest_frames: Sequence[StateFrame],
                          live_row: Optional[RunResultRow],
                          backtest_row: Optional[RunResultRow]) -> DayEquityDifference:
    """
    One compared day's change of account value on both sides.

    Args:
        day: The day
        live_frames: The live frames, in time order
        backtest_frames: The backtest's frames, in time order
        live_row: The day's live ledger row; None where there is none
        backtest_row: The backtest's row closing at the same boundary; None as above

    Returns:
        Both sides' values at the opening and the closing, and their changes
    """
    live_open = _equity_before(live_frames, day.opening)
    live_close = _equity_before(live_frames, day.closing)
    backtest_open = _equity_before(backtest_frames, day.opening)
    backtest_close = _equity_before(backtest_frames, day.closing)
    live_change = _change_pct(live_open, live_close)
    backtest_change = _change_pct(backtest_open, backtest_close)
    return DayEquityDifference(
        trading_day=day.trading_day, closing=day.closing,
        live_opening_equity=live_open, live_closing_equity=live_close,
        backtest_opening_equity=backtest_open, backtest_closing_equity=backtest_close,
        live_change_pct=live_change, backtest_change_pct=backtest_change,
        difference_pct=(live_change - backtest_change
                        if live_change is not None and backtest_change is not None else None),
        closing_gap=(live_close - backtest_close
                     if live_close is not None and backtest_close is not None else None),
        live_ledger_final_equity=live_row.final_equity if live_row is not None else None,
        backtest_ledger_final_equity=(backtest_row.final_equity
                                      if backtest_row is not None else None))


def daily_statistics(differences: Sequence[float]) -> DailyDifferenceStatistics:
    """
    The days' differences of equity change, read as a series.

    Args:
        differences: One difference per compared day, in day order, in percent

    Returns:
        Their mean, spread, 90 % interval, lag-1 autocorrelation and Ljung-Box statistic
    """
    interval = ninety_percent_interval(differences)
    statistic = ljung_box_lag_one(differences)
    return DailyDifferenceStatistics(
        day_count=len(differences),
        mean_pct=mean_of(differences),
        tracking_error_pct=tracking_error_of(differences),
        interval_low_pct=interval[0] if interval is not None else None,
        interval_high_pct=interval[1] if interval is not None else None,
        lag_one_autocorrelation=lag_one_autocorrelation(differences),
        ljung_box_statistic=statistic,
        interval_too_narrow=(statistic > LJUNG_BOX_LAG_ONE_CRITICAL_5PCT
                             if statistic is not None else None))


def valued_frame_before(frames: Sequence[StateFrame], instant: datetime) -> Optional[StateFrame]:
    """
    The last frame strictly before an instant that values the account.

    Args:
        frames: Frames in time order
        instant: The instant

    Returns:
        The frame; None where no valued frame lies before it
    """
    index = bisect_left([frame.frame_time for frame in frames], instant) - 1
    while index >= 0:
        if frames[index].equity is not None:
            return frames[index]
        index -= 1
    return None


def _equity_before(frames: Sequence[StateFrame], instant: datetime) -> Optional[float]:
    """
    The account value at the last valued frame before an instant.

    Args:
        frames: Frames in time order
        instant: The instant

    Returns:
        The value; None where no valued frame lies before it
    """
    frame = valued_frame_before(frames, instant)
    return frame.equity if frame is not None else None


def _valued_slots(frames: Sequence[StateFrame]) -> Dict[datetime, float]:
    """
    The account value of every frame that filled a cadence slot.

    Args:
        frames: Frames in time order

    Returns:
        Slot → the value of the frame that filled it; a slot whose frame is unvalued is absent
    """
    return {frame.cadence_slot: frame.equity for frame in frames
            if frame.cadence_slot is not None and frame.equity is not None}


def _change_pct(opening: Optional[float], closing: Optional[float]) -> Optional[float]:
    """
    A change of account value in % of its opening value.

    Args:
        opening: The value at the opening
        closing: The value at the closing

    Returns:
        The change; None where a value is missing or the opening is zero
    """
    if opening is None or closing is None or opening == 0:
        return None
    return (closing - opening) / opening * 100.0
