"""
The booking periods' tables (#537, #557) — a period's own figures, and the run totals, the line
under the period table.

A period's figures fold from the trades it realised, under the ledger's own names, because a sealed
period IS a ledger row: its win rate and its profit factor are rebuilt by the same derivations that
rebuild them across the periods, so the two levels cannot answer the question differently.

The run totals take two folds, because the closing figure needs a step between: the periods add up to the run's P&L,
fees and trades directly, while the run's closing equity is the sum of each UNIT's closing equity,
and a unit's closing equity is its last period's — folded from the periods by the ledger's own table
into the unit totals first.
"""

from typing import Any, Dict, Sequence

from python.framework.reporting.aggregation.tables.ledger_figure_table import (
    derive_profit_factor,
    derive_win_rate,
)
from python.framework.reporting.aggregation.tables.trade_figure_table import (
    losing_trade,
    loss_of_loser,
    profit_of_winner,
    winning_trade,
)
from python.framework.types.aggregation_types import FigureDeclaration, FoldTable, Reduction
from python.framework.types.api.report_types import (
    BookingPeriodRow,
    BookingPeriodsReport,
    BookingUnitTotalRow,
    TradeHistoryRow,
)


def net_of_the_gross_halves(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    A period's net P&L as its gross profit less its gross loss — built from the two halves, so the
    three figures cannot disagree.

    Args:
        combined: The figures being built, the halves already folded
        rows: The period's trades

    Returns:
        Gross profit less gross loss
    """
    return combined['gross_profit'] - combined['gross_loss']


# One period's figures, from the trades it realised. Served as no model of its own: the figures are
# the columns of the ledger row the period becomes, which the ledger's catalog declares.
BOOKING_PERIOD_TRADES_TABLE = FoldTable(
    name='booking_period_trades',
    figures={
        'total_trades': FigureDeclaration(Reduction.COUNT),
        'winning_trades': FigureDeclaration(Reduction.SUM, source=winning_trade, empty_value=0),
        'losing_trades': FigureDeclaration(Reduction.SUM, source=losing_trade, empty_value=0),
        'gross_profit': FigureDeclaration(Reduction.SUM, source=profit_of_winner,
                                          empty_value=0.0),
        'gross_loss': FigureDeclaration(Reduction.SUM, source=loss_of_loser, empty_value=0.0),
        'total_fees': FigureDeclaration(Reduction.SUM, empty_value=0.0),
        'net_pnl': FigureDeclaration(Reduction.DERIVE),
        'win_rate': FigureDeclaration(Reduction.DERIVE),
        'profit_factor': FigureDeclaration(Reduction.DERIVE),
    },
    derivations={'net_pnl': net_of_the_gross_halves,
                 'win_rate': derive_win_rate,
                 'profit_factor': derive_profit_factor},
    row_model=TradeHistoryRow,
)

BOOKING_RUN_TOTALS_TABLE = FoldTable(
    name='booking_run_totals',
    figures={
        'total_net_pnl': FigureDeclaration(Reduction.SUM, source='net_pnl'),
        'total_fees': FigureDeclaration(Reduction.SUM),
        'total_trades': FigureDeclaration(Reduction.SUM, source='trade_count'),
        # The deepest SINGLE-period decline, which is what this table's column holds. Not the
        # run's drawdown: a fall that runs across a period boundary is deeper than any one
        # period's own, and the run summary is where that figure lives. A plain maximum, where the
        # ledger takes the largest magnitude — #657
        'deepest_period_drawdown': FigureDeclaration(Reduction.MAX, source='max_drawdown'),
    },
    row_model=BookingPeriodRow,
    served_as=BookingPeriodsReport,
    serves_whole=False,
)

BOOKING_UNIT_CLOSE_TABLE = FoldTable(
    name='booking_unit_close',
    figures={
        # A total only when every unit closed with a valuation
        'total_final_equity': FigureDeclaration(Reduction.SUM_COMPLETE, source='final_equity'),
    },
    row_model=BookingUnitTotalRow,
    served_as=BookingPeriodsReport,
    serves_whole=False,
)
