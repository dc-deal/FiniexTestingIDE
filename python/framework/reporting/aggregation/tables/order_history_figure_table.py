"""
The order history's fold tables (#557) — one failed order per row.

The failed orders the history lists, counted per run unit and per status and reason. The history
of a long session keeps only its newest rows, so these count what is listed, never every failure:
the execution statistics count those. The reason a row names is resolved once into a fold row of
its own, so the orders can be grouped by it.
"""

from python.framework.types.aggregation_types import (
    FigureDeclaration,
    FoldTable,
    GroupOrder,
    Reduction,
)
from python.framework.types.api.report_types import (
    FailedOrderReason,
    FailedOrdersOfUnit,
    OrderHistoryRow,
)
from python.framework.types.trading_env_types.order_types import FailedOrderOfReason

FAILED_ORDERS_OF_UNIT_TABLE = FoldTable(
    name='failed_orders_of_unit',
    figures={
        'scenario_name': FigureDeclaration(Reduction.FIRST_ROW),
        'listed': FigureDeclaration(Reduction.COUNT),
    },
    order=GroupOrder.FIRST_SEEN,
    grain=('scenario_name',),
    row_model=OrderHistoryRow,
    served_as=FailedOrdersOfUnit,
)

FAILED_ORDER_REASON_TABLE = FoldTable(
    name='failed_order_reason',
    figures={
        'status': FigureDeclaration(Reduction.FIRST_ROW),
        'reason': FigureDeclaration(Reduction.FIRST_ROW),
        'count': FigureDeclaration(Reduction.COUNT),
    },
    order=GroupOrder.FIRST_SEEN,
    grain=('status', 'reason'),
    row_model=FailedOrderOfReason,
    served_as=FailedOrderReason,
)
