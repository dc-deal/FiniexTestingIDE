"""
FiniexTestingIDE - Warmup History Validator (#588)

One answer to "is this warmup history enough?", asked by the backtest's data validation and by
both AutoTrader paths. Three checks used to ask it with three outcomes: the batch validator
skipped a timeframe with no bars at all, the scenario subprocess logged an error against the
FIRST worker reading a timeframe rather than the most demanding one, and the AutoTrader warned
in a mock session and refused in a live one. Each pipeline still decides what a shortfall
means for it — a backtest excludes the scenario, a session refuses to start — but they now
decide on the same shortfalls.
"""

from typing import Dict, List, Sequence

from python.framework.types.market_types.warmup_history_types import WarmupShortfall


def find_warmup_shortfalls(
    available_by_timeframe: Dict[str, int],
    required_by_timeframe: Dict[str, int],
) -> List[WarmupShortfall]:
    """
    The timeframes whose history holds fewer bars than their workers require.

    A timeframe the history does not carry at all counts as available 0 — the case the batch
    validator used to skip in silence.

    Args:
        available_by_timeframe: Bars the history holds, by timeframe
        required_by_timeframe: Bars the most demanding worker requires, by timeframe

    Returns:
        One shortfall per timeframe that falls short, in the requirement's order
    """
    return [
        WarmupShortfall(timeframe=timeframe, available=available_by_timeframe.get(timeframe, 0),
                        required=required)
        for timeframe, required in required_by_timeframe.items()
        if available_by_timeframe.get(timeframe, 0) < required
    ]


def find_capacity_shortfalls(
    required_by_timeframe: Dict[str, int],
    capacity: int,
) -> List[WarmupShortfall]:
    """
    The timeframes whose requirement exceeds the bar history the renderer keeps.

    The renderer's history holds at most `bar_max_history` bars per timeframe, so a larger
    requirement is trimmed at injection — the workers would run on fewer bars than they
    declared, and nothing would say so.

    Args:
        required_by_timeframe: Bars the most demanding worker requires, by timeframe
        capacity: The renderer's bar_max_history

    Returns:
        One shortfall per timeframe the history cannot hold, `available` being the capacity
    """
    return [
        WarmupShortfall(timeframe=timeframe, available=capacity, required=required)
        for timeframe, required in required_by_timeframe.items()
        if required > capacity
    ]


def describe_shortfalls(shortfalls: Sequence[WarmupShortfall]) -> str:
    """
    One line naming each shortfall as available/required.

    Args:
        shortfalls: The shortfalls to name

    Returns:
        E.g. 'M5: 30/71 · D1: 0/3'
    """
    return ' · '.join(
        f'{shortfall.timeframe}: {shortfall.available}/{shortfall.required}'
        for shortfall in shortfalls)
