"""
The booking periods' run totals (#537, #557) — the line under the period table.

Two folds, because the closing figure needs a step between: the periods add up to the run's P&L,
fees and trades directly, while the run's closing equity is the sum of each UNIT's closing equity,
and a unit's closing equity is its last period's — folded from the periods by the ledger's own table
into the unit totals first.
"""

from python.framework.types.aggregation_types import FigureDeclaration, FoldTable, Reduction
from python.framework.types.api.report_types import (
    BookingPeriodRow,
    BookingPeriodsReport,
    BookingUnitTotalRow,
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
