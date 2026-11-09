"""
The ledger's fold table (#537, #557) — one booking period of one unit per row.

The figures are declared in the figure catalog (`LEDGER_FIGURES`), because the ledger's own column
list is held to them. What the fold needs beyond the declarations lives here: how each rate is
rebuilt, which field names a row's account, and which stamps order a row against its siblings.

**A DERIVE column is not folded, it is REBUILT**, and each one needs its own domain knowledge:
a win rate comes from the summed counts, a profit factor from the summed components, a mean from
its own weight. Two are honestly unrecoverable here and answer None — the streaks, because a run
of winners can CROSS a period boundary and no arithmetic over two summaries can see that.
"""

from typing import Any, Dict, Optional, Sequence

from python.framework.reporting.aggregation.figure_catalog import LEDGER_FIGURES
from python.framework.types.aggregation_types import FoldTable, GroupOrder

# How a row is ordered when a column asks for "the most recent". The first stamp a row actually
# carries wins: a booking period is closed at `period_closed_at`, a row that books no period was
# written at `recorded_at_utc`, and everything older than that column has only its run timestamp.
LEDGER_RECENCY_KEYS = ('period_closed_at', 'recorded_at_utc', 'run_timestamp')


def _derive_win_rate(combined: Dict[str, Any], rows: Sequence[Any]) -> Optional[float]:
    """
    The win rate from the summed counts.

    A group that measured nothing — the rows of runs that failed before they measured anything —
    has no win rate rather than one of zero (#646); a group without a trade keeps its 0.0.

    Args:
        combined: The row being built, its flows already folded
        rows: The group

    Returns:
        The winners' share of the trades, 0.0 without a trade, None when nothing was measured
    """
    if combined.get('total_trades') is None:
        return None
    total = combined.get('total_trades') or 0
    winners = combined.get('winning_trades') or 0
    return (winners / total) if total else 0.0


def _derive_profit_factor(combined: Dict[str, Any], rows: Sequence[Any]) -> Optional[float]:
    """
    The profit factor from the summed gross halves.

    Args:
        combined: The row being built, its flows already folded
        rows: The group

    Returns:
        Gross profit over gross loss; None without a trade or without a loss beside a profit,
        0.0 when neither half moved
    """
    if not (combined.get('total_trades') or 0):
        return None
    gross_profit = combined.get('gross_profit') or 0.0
    gross_loss = combined.get('gross_loss') or 0.0
    if gross_loss > 0:
        return gross_profit / gross_loss
    return 0.0 if gross_profit == 0 else None


def _derive_weakest_signal_channel(combined: Dict[str, Any], rows: Sequence[Any]) -> Optional[float]:
    """
    The run's weakest channel, by its own definition (#433): a mean would hide a dead feed behind
    a healthy one.

    Args:
        combined: The row being built
        rows: The group

    Returns:
        The smallest fresh ratio any row carried, or None when no row carried one
    """
    present = [ratio for ratio in (getattr(row, 'signal_fresh_ratio', None) for row in rows)
               if ratio is not None]
    return min(present) if present else None


def _derive_nothing_for_a_sequence(combined: Dict[str, Any], rows: Sequence[Any]) -> None:
    """
    A streak describes a SEQUENCE, and a run of winners can cross a period boundary: 2 and 3 in
    adjacent periods can be a run of 5, so no arithmetic over two summaries can recover it. It is
    re-derived over the records of the wider window or it is not answered.

    Args:
        combined: The row being built
        rows: The group

    Returns:
        None, always
    """
    return None


# One booking period of one unit per row. Every DERIVE figure that is no weighted mean has its
# derivation here; a test holds them and the catalog's weighted means to the DERIVE figures, each
# exactly once, so a new rate cannot arrive without saying how it is rebuilt.
LEDGER_TABLE = FoldTable(
    name='ledger',
    figures=LEDGER_FIGURES,
    derivations={
        'win_rate': _derive_win_rate,
        'profit_factor': _derive_profit_factor,
        'signal_fresh_ratio': _derive_weakest_signal_channel,
        'max_consecutive_wins': _derive_nothing_for_a_sequence,
        'max_consecutive_losses': _derive_nothing_for_a_sequence,
    },
    # One account per unit: a backtest's scenarios each trade their own balance.
    account_key='unit_name',
    recency=LEDGER_RECENCY_KEYS,
    order=GroupOrder.SORTED,
)
