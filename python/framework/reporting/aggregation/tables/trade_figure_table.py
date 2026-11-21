"""
The trade tables (#389, #557) — one trade per row.

The trade analytics per account currency: the R and excursion figures, means over the trades of a
kind, the counts by direction, outcome and close, the totals per trade, the longest streaks. Its P&L-denominated figures never mix currencies, because the currency is
the grain.

The per-scenario totals under the trade table: each scenario's trades add up to its footer line. A
unit that read its ticks without a quote crossed no recorded spread, so its spread total is not
measured — that is a fact about the UNIT, which its trade rows do not carry, so its trades fold with
a table of their own that says so.
"""

from typing import Any, Dict, List, Optional, Sequence

from python.framework.types.aggregation_types import (
    FigureDeclaration,
    FoldTable,
    GroupOrder,
    Reduction,
)
from python.framework.types.api.report_types import (
    TradeAnalytics,
    TradeHistoryRow,
    TradeScenarioTotals,
)

def r_of_winner(row: Any) -> Optional[float]:
    """
    A winning trade's R, where it has one.

    Args:
        row: A trade row

    Returns:
        The R multiple of a trade that won; None for any other trade or one without an R
    """
    return row.r_multiple if row.r_multiple is not None and row.net_pnl > 0 else None


def r_of_loser(row: Any) -> Optional[float]:
    """
    A losing trade's R, where it has one.

    Args:
        row: A trade row

    Returns:
        The R multiple of a trade that lost; None for any other trade or one without an R
    """
    return row.r_multiple if row.r_multiple is not None and row.net_pnl < 0 else None


def mae_of_winner(row: Any) -> Optional[float]:
    """
    A winning trade's adverse excursion.

    Args:
        row: A trade row

    Returns:
        Its MAE when it won, else None
    """
    return row.mae_pnl if row.net_pnl > 0 else None


def mae_of_loser(row: Any) -> Optional[float]:
    """
    A losing trade's adverse excursion.

    Args:
        row: A trade row

    Returns:
        Its MAE when it lost, else None
    """
    return row.mae_pnl if row.net_pnl < 0 else None


def mfe_of_loser(row: Any) -> Optional[float]:
    """
    A losing trade's favourable excursion — how much it gave back.

    Args:
        row: A trade row

    Returns:
        Its MFE when it lost, else None
    """
    return row.mfe_pnl if row.net_pnl < 0 else None


def mae_magnitude(row: Any) -> float:
    """
    A trade's adverse excursion as a magnitude — MAE is recorded as an adverse P&L and its sign
    convention is not worth relying on for the worst one.

    Args:
        row: A trade row

    Returns:
        The absolute MAE
    """
    return abs(row.mae_pnl)


def mfe_magnitude(row: Any) -> float:
    """
    A trade's favourable excursion as a magnitude.

    Args:
        row: A trade row

    Returns:
        The absolute MFE
    """
    return abs(row.mfe_pnl)


def long_trade(row: Any) -> int:
    """
    Whether a trade was long — one count each.

    Args:
        row: A trade row

    Returns:
        1 when it was, else 0
    """
    return 1 if row.direction == 'long' else 0


def winning_trade(row: Any) -> int:
    """
    Whether a trade won — one count each. A trade that realised nothing is neither.

    Args:
        row: A trade row

    Returns:
        1 when its net P&L was positive, else 0
    """
    return 1 if row.net_pnl > 0 else 0


def losing_trade(row: Any) -> int:
    """
    Whether a trade lost — one count each. A trade that realised nothing is neither.

    Args:
        row: A trade row

    Returns:
        1 when its net P&L was negative, else 0
    """
    return 1 if row.net_pnl < 0 else 0


def profit_of_winner(row: Any) -> Optional[float]:
    """
    What a winning trade realised — one part of a gross profit.

    Args:
        row: A trade row

    Returns:
        Its net P&L when positive, else None
    """
    return row.net_pnl if row.net_pnl > 0 else None


def loss_of_loser(row: Any) -> Optional[float]:
    """
    What a losing trade lost, as a magnitude — one part of a gross loss.

    Args:
        row: A trade row

    Returns:
        Its net P&L negated when negative, else None
    """
    return -row.net_pnl if row.net_pnl < 0 else None


def closed_by_stop_loss(row: Any) -> int:
    """
    Whether a trade was closed by its stop loss — one count each.

    Args:
        row: A trade row

    Returns:
        1 when it was, else 0
    """
    return 1 if row.close_reason == 'sl_triggered' else 0


def closed_by_take_profit(row: Any) -> int:
    """
    Whether a trade was closed by its take profit — one count each.

    Args:
        row: A trade row

    Returns:
        1 when it was, else 0
    """
    return 1 if row.close_reason == 'tp_triggered' else 0


def closed_at_scenario_end(row: Any) -> int:
    """
    Whether a trade was closed by the end of its scenario — one count each. Reserved today: nothing
    closes a trade that way until the real session-end close (#487) exists.

    Args:
        row: A trade row

    Returns:
        1 when it was, else 0
    """
    return 1 if row.close_reason == 'scenario_end' else 0


def short_trade_count(combined: Dict[str, Any], rows: Sequence[Any]) -> int:
    """
    The group's short trades — every trade that was not long.

    Args:
        combined: The figures being built, the counts already folded
        rows: The trades

    Returns:
        The trade count less the long trades
    """
    return combined['trade_count'] - combined['long_trades']


def manual_close_count(combined: Dict[str, Any], rows: Sequence[Any]) -> int:
    """
    The group's trades closed by neither stop loss, take profit nor the scenario's end.

    Args:
        combined: The figures being built, the counts already folded
        rows: The trades

    Returns:
        The trade count less the three named kinds of close
    """
    return (combined['trade_count'] - combined['sl_closes'] - combined['tp_closes']
            - combined['scenario_end_closes'])


def _per_trade(combined: Dict[str, Any], total: str) -> float:
    """
    One folded total per trade of the group.

    Args:
        combined: The figures being built, the total and the trade count already folded
        total: The figure to divide

    Returns:
        The total over the trade count; 0.0 over no trade
    """
    count = combined['trade_count']
    return combined[total] / count if count else 0.0


def gross_pnl_per_trade(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    The group's gross P&L per trade.

    Args:
        combined: The figures being built
        rows: The trades

    Returns:
        The gross P&L over the trade count; 0.0 over no trade
    """
    return _per_trade(combined, 'gross_pnl')


def fees_per_trade(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    The group's fees per trade.

    Args:
        combined: The figures being built
        rows: The trades

    Returns:
        The fees over the trade count; 0.0 over no trade
    """
    return _per_trade(combined, 'total_fees')


def net_pnl_per_trade(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    The group's net P&L per trade.

    Args:
        combined: The figures being built
        rows: The trades

    Returns:
        The net P&L over the trade count; 0.0 over no trade
    """
    return _per_trade(combined, 'net_pnl')


def _longest_streak(rows: Sequence[Any], winners: bool) -> int:
    """
    The longest unbroken run of winners or losers, in REALISATION order.

    Ordered by exit time rather than taken as given, and PER UNIT: a streak is a statement about
    the sequence ONE account experienced, and a backtest's scenarios are separate accounts —
    interleaving their trades by time strung A's win, B's win and A's win into a run of three
    that no account had. The group's streak is the longest of its units'. A break-even trade
    (net exactly zero) ends both streaks — it is neither, and treating it as either would invent
    a run the account did not have.

    Args:
        rows: The trade rows of one currency group
        winners: True for the winning streak, False for the losing one

    Returns:
        The longest run of any one unit; 0 when the group holds none of that kind
    """
    by_unit: Dict[str, List[Any]] = {}
    for row in rows:
        by_unit.setdefault(row.scenario_name, []).append(row)
    longest = 0
    for unit_rows in by_unit.values():
        current = 0
        for row in sorted(unit_rows, key=lambda r: r.exit_time):
            hit = row.net_pnl > 0 if winners else row.net_pnl < 0
            current = current + 1 if hit else 0
            longest = max(longest, current)
    return longest


def longest_winning_streak(combined: Dict[str, Any], rows: Sequence[Any]) -> int:
    """
    The longest run of winners any one unit had.

    Args:
        combined: The figures being built
        rows: The trades of one currency

    Returns:
        Its length
    """
    return _longest_streak(rows, winners=True)


def longest_losing_streak(combined: Dict[str, Any], rows: Sequence[Any]) -> int:
    """
    The longest run of losers any one unit had.

    Args:
        combined: The figures being built
        rows: The trades of one currency

    Returns:
        Its length
    """
    return _longest_streak(rows, winners=False)


TRADE_ANALYTICS_TABLE = FoldTable(
    name='trade_analytics',
    figures={
        # The group's key, read as every row carries it
        'currency': FigureDeclaration(Reduction.FIRST_ROW),
        'trade_count': FigureDeclaration(Reduction.COUNT),
        # The R subsets carry their own counts: a run can have R-defined trades and no winner
        # among them, so r_trade_count cannot tell a reader whether avg_win_r was measured.
        'expectancy': FigureDeclaration(Reduction.MEAN, source='r_multiple'),
        'avg_win_r': FigureDeclaration(Reduction.MEAN, source=r_of_winner),
        'avg_loss_r': FigureDeclaration(Reduction.MEAN, source=r_of_loser),
        'r_trade_count': FigureDeclaration(Reduction.COUNT, source='r_multiple'),
        'r_win_count': FigureDeclaration(Reduction.COUNT, source=r_of_winner),
        'r_loss_count': FigureDeclaration(Reduction.COUNT, source=r_of_loser),
        # 0.0 for an empty set of winners or losers, beside the None the R means answer — #657
        'avg_mae_winners': FigureDeclaration(Reduction.MEAN, source=mae_of_winner, empty_value=0.0),
        'avg_mae_losers': FigureDeclaration(Reduction.MEAN, source=mae_of_loser, empty_value=0.0),
        'avg_mfe_losers': FigureDeclaration(Reduction.MEAN, source=mfe_of_loser, empty_value=0.0),
        'gross_pnl': FigureDeclaration(Reduction.SUM),
        'net_pnl': FigureDeclaration(Reduction.SUM),
        'total_fees': FigureDeclaration(Reduction.SUM),
        # The worst single excursion beside the means — the largest magnitude
        'largest_mae': FigureDeclaration(Reduction.MAX, source=mae_magnitude, empty_value=0.0),
        'largest_mfe': FigureDeclaration(Reduction.MAX, source=mfe_magnitude, empty_value=0.0),
        'avg_trade_duration_s': FigureDeclaration(Reduction.MEAN, source='duration_s',
                                                  empty_value=0.0),
        'avg_trade_duration_ticks': FigureDeclaration(Reduction.MEAN, source='duration_ticks',
                                                      empty_value=0.0),
        'min_trade_duration_ticks': FigureDeclaration(Reduction.MIN, source='duration_ticks',
                                                      empty_value=0),
        'max_trade_duration_ticks': FigureDeclaration(Reduction.MAX, source='duration_ticks',
                                                      empty_value=0),
        'long_trades': FigureDeclaration(Reduction.SUM, source=long_trade, empty_value=0),
        'short_trades': FigureDeclaration(Reduction.DERIVE),
        'winning_trades': FigureDeclaration(Reduction.SUM, source=winning_trade, empty_value=0),
        'losing_trades': FigureDeclaration(Reduction.SUM, source=losing_trade, empty_value=0),
        'sl_closes': FigureDeclaration(Reduction.SUM, source=closed_by_stop_loss, empty_value=0),
        'tp_closes': FigureDeclaration(Reduction.SUM, source=closed_by_take_profit,
                                       empty_value=0),
        'scenario_end_closes': FigureDeclaration(Reduction.SUM, source=closed_at_scenario_end,
                                                 empty_value=0),
        'manual_closes': FigureDeclaration(Reduction.DERIVE),
        'avg_gross_pnl': FigureDeclaration(Reduction.DERIVE),
        'avg_fees': FigureDeclaration(Reduction.DERIVE),
        'avg_net_pnl': FigureDeclaration(Reduction.DERIVE),
        'max_consecutive_wins': FigureDeclaration(Reduction.DERIVE),
        'max_consecutive_losses': FigureDeclaration(Reduction.DERIVE),
    },
    derivations={'short_trades': short_trade_count,
                 'manual_closes': manual_close_count,
                 'avg_gross_pnl': gross_pnl_per_trade,
                 'avg_fees': fees_per_trade,
                 'avg_net_pnl': net_pnl_per_trade,
                 'max_consecutive_wins': longest_winning_streak,
                 'max_consecutive_losses': longest_losing_streak},
    order=GroupOrder.SORTED,
    grain=('currency',),
    row_model=TradeHistoryRow,
    served_as=TradeAnalytics,
)


_SCENARIO_TOTALS_FIGURES = {
    # The group's key, read as every row carries it — an empty name included
    'scenario_name': FigureDeclaration(Reduction.FIRST_ROW),
    'currency': FigureDeclaration(Reduction.FIRST_ROW),
    'trade_count': FigureDeclaration(Reduction.COUNT),
    'gross_pnl': FigureDeclaration(Reduction.SUM),
    'net_pnl': FigureDeclaration(Reduction.SUM),
    'total_fees': FigureDeclaration(Reduction.SUM),
    'total_swap': FigureDeclaration(Reduction.SUM, source='swap_cost'),
    'total_spread_cost': FigureDeclaration(Reduction.SUM, source='spread_cost'),
    'total_modelled_spread_cost': FigureDeclaration(Reduction.SUM, source='modelled_spread_cost'),
}

TRADE_SCENARIO_TOTALS_TABLE = FoldTable(
    name='trade_scenario_totals',
    figures=_SCENARIO_TOTALS_FIGURES,
    order=GroupOrder.FIRST_SEEN,
    grain=('scenario_name',),
    row_model=TradeHistoryRow,
    served_as=TradeScenarioTotals,
)


def spread_not_measured(combined: Dict[str, Any], rows: Sequence[Any]) -> None:
    """
    The spread total of a unit that read its ticks without a quote: no fill of its crossed a
    recorded spread, so there is nothing to add up.

    Args:
        combined: The figures being built
        rows: The unit's trades

    Returns:
        None, always
    """
    return None


TRADE_SCENARIO_TOTALS_WITHOUT_QUOTE_TABLE = FoldTable(
    name='trade_scenario_totals_without_quote',
    figures={**_SCENARIO_TOTALS_FIGURES,
             'total_spread_cost': FigureDeclaration(Reduction.DERIVE)},
    derivations={'total_spread_cost': spread_not_measured},
    order=GroupOrder.FIRST_SEEN,
    grain=('scenario_name',),
    row_model=TradeHistoryRow,
    served_as=TradeScenarioTotals,
)
