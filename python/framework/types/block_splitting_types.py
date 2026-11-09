"""
Block splitting types (#557) — the row the block-splitting report folds its symbol rows from.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class BlockOfSymbol:
    """
    One profile block's trades at its end, with the symbol and generator mode it belongs to — the
    row a block-splitting symbol row folds from. Its figures are the block's own, read from its tick
    loop: one record, not yet combined with any other block.

    Args:
        symbol: The block's symbol, parsed from its scenario name
        generator_mode: The mode the symbol's profile was generated in
        open_trades: Positions still open at the block's end
        open_pnl: Their unrealised P&L
        closed_trades: Trades the block closed itself
        closed_pnl: Their net P&L
        discarded_orders: Orders the block's end met in flight, expired before the venue took them
    """
    symbol: str
    generator_mode: str
    open_trades: int
    open_pnl: float
    closed_trades: int
    closed_pnl: float
    discarded_orders: int
