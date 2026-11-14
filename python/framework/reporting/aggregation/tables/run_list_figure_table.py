"""
The run list's fold table (#557) — one folded ledger row per currency of a run.

A run's ledger rows are first folded per run and currency (the ledger's own table); this folds those
into the run's entry in the list. A run-level column is the same on every currency row of a run,
and where the rows disagree the list says nothing rather than pick one.
"""

from typing import Any, Callable, Dict, List, Sequence

from python.framework.types.aggregation_types import (
    FigureDeclaration,
    FoldTable,
    GroupOrder,
    Reduction,
)
from python.framework.types.api.report_types import (
    RunListFigures,
    RunResultFigures,
    RunResultRow,
)


def figures_per_currency(combined: Dict[str, Any], rows: Sequence[Any]) -> List[RunResultFigures]:
    """
    The run's figures, one entry per account currency.

    Args:
        combined: The figures being built
        rows: The run's folded rows, one per currency

    Returns:
        One entry per currency row. The figureless error row a run that produced nothing writes
        has no currency, and its zeros are not figures anybody measured, so it is left out
    """
    return [RunResultFigures(currency=row.currency, net_pnl=row.net_pnl,
                             total_trades=row.total_trades)
            for row in rows if row.currency]


def _agreed_on(column: str) -> Callable[[Dict[str, Any], Sequence[Any]], Any]:
    """
    The derivation of one run-level column: its value when every currency row agrees on it.

    Args:
        column: A column that is one value per run

    Returns:
        A derivation named after the column
    """
    def agreed(combined: Dict[str, Any], rows: Sequence[Any]) -> Any:
        """
        One run-level column's value across the run's currency rows.

        Args:
            combined: The figures being built
            rows: The run's folded rows, one per currency

        Returns:
            The value every row carries, or None when they disagree or none recorded it
        """
        values = {getattr(row, column) for row in rows}
        return values.pop() if len(values) == 1 else None
    agreed.__name__ = agreed.__qualname__ = f'agreed_{column}'
    return agreed


_RUN_LEVEL_COLUMNS = ('run_outcome', 'error_count', 'warning_count', 'log_warning_count',
                      'tick_timespan_seconds')

RUN_LIST_TABLE = FoldTable(
    name='run_list',
    figures={
        'run_id': FigureDeclaration(Reduction.FIRST_ROW),
        'results': FigureDeclaration(Reduction.DERIVE),
        **{column: FigureDeclaration(Reduction.DERIVE) for column in _RUN_LEVEL_COLUMNS},
    },
    derivations={
        'results': figures_per_currency,
        **{column: _agreed_on(column) for column in _RUN_LEVEL_COLUMNS},
    },
    order=GroupOrder.FIRST_SEEN,
    grain=('run_id',),
    row_model=RunResultRow,
    served_as=RunListFigures,
    outside_the_fold={'run_id': 'folded, not served: the key the run list is indexed by'},
)
