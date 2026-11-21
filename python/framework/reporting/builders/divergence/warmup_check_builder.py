"""
The warmup a backtest of a session's window started from (#512): held to the session's record bar
by bar before measure 1, and each session's record held to the archive's bars as an input line.

The backtest starts from the bars the session recorded, so the first check can only find what was
lost or changed on the way from the record into the backtest — the verdict rules ask for it anyway,
because a decision compared on a warmup nobody checked is a comparison of two unknown starts. The
second check decides nothing: it shows how far the venue's bars the session warmed up on stand from
the bars the archive renders from the collector's ticks, which is what a re-warmup explanation and
a capture difference rest on. Pure: it compares the records and bars it is handed.
"""

import math
from datetime import datetime
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

from python.framework.types.api.divergence_report_types import (
    WarmupArchiveComparison,
    WarmupInjectionCheck,
    WarmupInjectionState,
)
from python.framework.types.market_types.warmup_history_types import WarmupHistoryRecord
from python.framework.utils.time_utils import parse_iso_datetime

# The prices of a recorded bar held to the archive's, by their place in the record's bar
_BAR_PRICES = (('open', 1), ('high', 2), ('low', 3), ('close', 4))

# Where a recorded bar holds its volume
_BAR_VOLUME = 5

# How many differences an archive line names
_NAMED_EXAMPLES = 3

# Two prices or volumes closer than this are one value written twice
_RELATIVE_TOLERANCE = 1e-9


def check_warmup_injection(session: Optional[WarmupHistoryRecord],
                           backtest: Optional[WarmupHistoryRecord],
                           unreadable: str = '') -> WarmupInjectionCheck:
    """
    The backtest's injected warmup held to the session's record, bar by bar.

    Args:
        session: The warmup the session recorded; None where it recorded none
        backtest: The warmup the backtest recorded as injected; None where it recorded none
        unreadable: Why a record that exists could not be read; '' where both were read

    Returns:
        EQUAL, DIFFERENT with every difference, or NOT_CHECKED with why — a record missing on
        either side or both is no evidence of an equal start
    """
    if unreadable:
        return WarmupInjectionCheck(state=WarmupInjectionState.NOT_CHECKED, detail=unreadable)
    if session is None and backtest is None:
        return WarmupInjectionCheck(
            state=WarmupInjectionState.NOT_CHECKED,
            detail='neither the session nor the backtest recorded a warmup')
    if session is None or backtest is None:
        missing = 'the session' if session is None else 'the backtest'
        return WarmupInjectionCheck(
            state=WarmupInjectionState.NOT_CHECKED,
            detail=f'{missing} recorded no warmup, the other side did')
    differences = _record_differences(session, backtest)
    counts = ', '.join(f'{timeframe} {len(entry.bars)}'
                       for timeframe, entry in sorted(session.timeframes.items()))
    if differences:
        return WarmupInjectionCheck(
            state=WarmupInjectionState.DIFFERENT, differences=differences,
            detail=f'{len(differences)} difference(s) from the session\'s record ({counts} bars)')
    return WarmupInjectionCheck(
        state=WarmupInjectionState.EQUAL,
        detail=f'every bar as the session recorded it ({counts} bars), complete at '
               f'{session.selected_for}')


def compare_warmup_with_archive(run_id: str, record: WarmupHistoryRecord,
                                archive_bars: Mapping[str, Optional[pd.DataFrame]]
                                ) -> List[WarmupArchiveComparison]:
    """
    One session's recorded warmup held to the archive's bars of the same periods, timeframe by
    timeframe.

    Args:
        run_id: The session
        record: The warmup it recorded
        archive_bars: Timeframe → the archive's bar file as read; None where it holds none

    Returns:
        One line per recorded timeframe, in timeframe order
    """
    lines: List[WarmupArchiveComparison] = []
    for timeframe, entry in sorted(record.timeframes.items()):
        bars = archive_bars.get(timeframe)
        if bars is None:
            lines.append(WarmupArchiveComparison(
                run_id=run_id, timeframe=timeframe,
                not_compared_because='the archive holds no bar file of the timeframe'))
            continue
        opens = [parse_iso_datetime(bar[0]) for bar in entry.bars]
        by_open = _archive_by_open(bars, opens)
        compared = differing = missing = 0
        recorded_volume = archived_volume = 0.0
        examples: List[str] = []
        for bar, opened in zip(entry.bars, opens):
            archived = by_open.get(opened)
            if archived is None:
                missing += 1
                continue
            compared += 1
            recorded_volume += bar[_BAR_VOLUME]
            archived_volume += float(archived['volume'])
            apart = [f'{name} {bar[place]:g} recorded, {archived[name]:g} archived'
                     for name, place in _BAR_PRICES
                     if not math.isclose(bar[place], archived[name], rel_tol=_RELATIVE_TOLERANCE)]
            if apart:
                differing += 1
                if len(examples) < _NAMED_EXAMPLES:
                    examples.append(f'{bar[0]}: ' + ', '.join(apart))
        lines.append(WarmupArchiveComparison(
            run_id=run_id, timeframe=timeframe, compared=compared, differing=differing,
            recorded_to_archived_volume=(recorded_volume / archived_volume
                                         if compared and archived_volume > 0 else None),
            missing_in_archive=missing, examples=examples))
    return lines


def _record_differences(session: WarmupHistoryRecord,
                        backtest: WarmupHistoryRecord) -> List[str]:
    """
    Every way the backtest's injected warmup differs from the session's record: the instant the
    bars are complete at, a timeframe only one side holds, a bar count, and each bar.

    Args:
        session: The session's record
        backtest: The backtest's

    Returns:
        The differences in words, in timeframe order; empty where there is none
    """
    differences: List[str] = []
    if session.selected_for != backtest.selected_for:
        differences.append(f'complete at {session.selected_for} in the record, '
                           f'{backtest.selected_for} injected')
    for timeframe in sorted(set(session.timeframes) | set(backtest.timeframes)):
        recorded = session.timeframes.get(timeframe)
        injected = backtest.timeframes.get(timeframe)
        if recorded is None or injected is None:
            side = 'the record' if recorded is None else 'the injection'
            differences.append(f'{timeframe}: missing from {side}')
            continue
        differences += _bar_differences(timeframe, recorded.bars, injected.bars)
    return differences


def _bar_differences(timeframe: str, recorded: Sequence[Tuple], injected: Sequence[Tuple]
                     ) -> List[str]:
    """
    One timeframe's bars held to each other, place by place.

    Args:
        timeframe: The timeframe, for the words
        recorded: The session's bars, oldest first
        injected: The backtest's, oldest first

    Returns:
        The differences in words; empty where every bar is equal
    """
    differences: List[str] = []
    if len(recorded) != len(injected):
        differences.append(f'{timeframe}: {len(recorded)} bars recorded, {len(injected)} injected')
    for left, right in zip(recorded, injected):
        if tuple(left) != tuple(right):
            differences.append(f'{timeframe} bar {left[0]}: {list(left)} recorded, '
                               f'{list(right)} injected')
    return differences


def _archive_by_open(bars: pd.DataFrame, opens: Sequence[datetime]) -> Dict[datetime, pd.Series]:
    """
    The archive's bars of the given periods, by the instant each opens — only those rows, since a
    bar file of a short timeframe holds far more periods than a warmup reads.

    Args:
        bars: A bar file as read
        opens: The opening instants wanted, UTC

    Returns:
        Opening instant (UTC) → its row, for each wanted period the file holds
    """
    stamps = pd.to_datetime(bars['timestamp'], utc=True)
    wanted = stamps.isin(pd.DatetimeIndex(opens))
    return {stamp.to_pydatetime(): row
            for stamp, (_, row) in zip(stamps[wanted], bars[wanted].iterrows())}
