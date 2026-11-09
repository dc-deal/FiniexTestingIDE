"""
The block-splitting report's fold tables (#557) — one profile block per row, then one symbol.

A symbol's row folds its blocks: their trades still open at the block end and their trades closed
inside, the P&L of each added block by block. The report's totals fold the symbol rows.
"""

from typing import Any, Dict, Sequence

from python.framework.types.aggregation_types import (
    Addition,
    FigureDeclaration,
    FoldTable,
    GroupOrder,
    Reduction,
)
from python.framework.types.api.report_types import BlockSplittingReport, BlockSplittingSymbolRow
from python.framework.types.block_splitting_types import BlockOfSymbol


def trades_in_blocks(combined: Dict[str, Any], rows: Sequence[Any]) -> int:
    """
    A symbol's trades: open at a block end plus closed inside one.

    Args:
        combined: The figures being built, both counts already folded
        rows: The symbol's blocks

    Returns:
        Their total
    """
    return combined['open_at_boundary_trades'] + combined['natural_closed_trades']


def pnl_in_blocks(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    A symbol's P&L: unrealised at the block ends plus realised inside them.

    Args:
        combined: The figures being built, both totals already folded
        rows: The symbol's blocks

    Returns:
        Their total
    """
    return combined['open_at_boundary_pnl'] + combined['natural_closed_pnl']


def share_open_at_boundary(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    The share of a symbol's trades still open at a block end.

    Args:
        combined: The figures being built, the counts already derived
        rows: The symbol's blocks

    Returns:
        The share in percent; 0.0 without a trade
    """
    total = combined['total_trades']
    return (combined['open_at_boundary_trades'] / total * 100) if total else 0.0


def disposition(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    How much of a symbol's P&L the block ends left unrealised, as magnitudes.

    Args:
        combined: The figures being built, the totals already derived
        rows: The symbol's blocks

    Returns:
        The share in percent; 0.0 without P&L
    """
    total = combined['total_pnl']
    return (abs(combined['open_at_boundary_pnl']) / abs(total) * 100) if total else 0.0


BLOCK_SYMBOL_TABLE = FoldTable(
    name='block_splitting_symbol',
    figures={
        # The group's key, read as every row carries it
        'symbol': FigureDeclaration(Reduction.FIRST_ROW),
        'generator_mode': FigureDeclaration(Reduction.FIRST_ROW),
        'block_count': FigureDeclaration(Reduction.COUNT),
        # Over no block zero, the row's own defaults
        'open_at_boundary_trades': FigureDeclaration(Reduction.SUM, source='open_trades',
                                                     empty_value=0),
        # Each block's P&L added in a loop, as the row always added them — #657
        'open_at_boundary_pnl': FigureDeclaration(Reduction.SUM, source='open_pnl',
                                                  addition=Addition.SEQUENTIAL, empty_value=0.0),
        'natural_closed_trades': FigureDeclaration(Reduction.SUM, source='closed_trades',
                                                   empty_value=0),
        'natural_closed_pnl': FigureDeclaration(Reduction.SUM, source='closed_pnl',
                                                addition=Addition.SEQUENTIAL, empty_value=0.0),
        'discarded_pending_orders': FigureDeclaration(Reduction.SUM, source='discarded_orders',
                                                      empty_value=0),
        'total_trades': FigureDeclaration(Reduction.DERIVE),
        'total_pnl': FigureDeclaration(Reduction.DERIVE),
        'open_at_boundary_pct': FigureDeclaration(Reduction.DERIVE),
        'disposition_pct': FigureDeclaration(Reduction.DERIVE),
    },
    derivations={
        'total_trades': trades_in_blocks,
        'total_pnl': pnl_in_blocks,
        'open_at_boundary_pct': share_open_at_boundary,
        'disposition_pct': disposition,
    },
    order=GroupOrder.SORTED,
    grain=('symbol',),
    row_model=BlockOfSymbol,
    served_as=BlockSplittingSymbolRow,
)


def share_open_overall(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    The share of all symbols' trades still open at a block end.

    Args:
        combined: The figures being built, the counts already folded
        rows: The symbol rows

    Returns:
        The share in percent; 0.0 without a trade
    """
    total = combined['agg_total_trades']
    return (combined['agg_open_at_boundary_trades'] / total * 100) if total else 0.0


def disposition_overall(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    How much of all symbols' P&L the block ends left unrealised, as magnitudes.

    Args:
        combined: The figures being built, the totals already folded
        rows: The symbol rows

    Returns:
        The share in percent; 0.0 without P&L
    """
    total = combined['agg_pnl']
    return (abs(combined['agg_open_pnl']) / abs(total) * 100) if total else 0.0


BLOCK_TOTALS_TABLE = FoldTable(
    name='block_splitting_totals',
    figures={
        # Over no symbol zero, as the report always answered
        'agg_open_at_boundary_trades': FigureDeclaration(
            Reduction.SUM, source='open_at_boundary_trades', empty_value=0),
        'agg_total_trades': FigureDeclaration(Reduction.SUM, source='total_trades',
                                              empty_value=0),
        'agg_open_pnl': FigureDeclaration(Reduction.SUM, source='open_at_boundary_pnl',
                                          empty_value=0.0),
        'agg_pnl': FigureDeclaration(Reduction.SUM, source='total_pnl', empty_value=0.0),
        'agg_open_at_boundary_pct': FigureDeclaration(Reduction.DERIVE),
        'agg_disposition_pct': FigureDeclaration(Reduction.DERIVE),
    },
    derivations={'agg_open_at_boundary_pct': share_open_overall,
                 'agg_disposition_pct': disposition_overall},
    row_model=BlockSplittingSymbolRow,
    served_as=BlockSplittingReport,
    outside_the_fold={
        'run_id': 'the identity of the run',
        'symbols': 'a nested table: the symbol rows, folded by their table',
        'agg_open_pnl': 'folded, not served: an input of the disposition share',
        'agg_pnl': 'folded, not served: an input of the disposition share',
    },
)
