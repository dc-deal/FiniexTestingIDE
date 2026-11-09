"""
The portfolio's fold tables (#391, #397, #557) — one run unit's portfolio per row.

The headline per account currency, and the aggregated portfolio of the executive block: the same
units, the headline's sums reused for the aggregated portfolio's rates, and parts beside it — the
units' execution counts, their pending orders and, at spot, their dual balances.

Over no unit, every figure answers what the aggregate always answered there — zero, and no unit
named — so the rates are rebuilt from zeros rather than from nothing. No caller folds an empty
group today; #657 decides whether nothing should read None.

Ratios are rebuilt from the summed components, never summed. The deepest decline, the peak it fell
from and the share it was travel together: whichever unit owns the deepest decline supplies all
three (#497).
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

from python.framework.reporting.aggregation.tables.execution_figure_table import (
    AGGREGATED_EXECUTION_TABLE,
)
from python.framework.types.aggregation_types import (
    Addition,
    FigureDeclaration,
    FoldTable,
    GroupOrder,
    Reduction,
)
from python.framework.types.api.report_types import (
    AggregatedPortfolioRow,
    AggregatedPortfolioSpotScenarioRow,
    FeeSourceFills,
    PendingOrdersUnitRow,
    PortfolioAggregateRow,
    PortfolioUnitRow,
)
from python.framework.types.trading_env_types.broker_types import FeeSource


def open_position_count(row: Any) -> int:
    """
    A unit's open positions, counted.

    Args:
        row: A portfolio unit row

    Returns:
        How many positions it holds open
    """
    return len(row.open_positions)


def win_rate(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    The winners' share of the trades, from the summed counts.

    Args:
        combined: The figures being built, the counts already folded
        rows: The units

    Returns:
        The share; 0.0 without a trade
    """
    total = combined['total_trades']
    return combined['winning_trades'] / total if total > 0 else 0.0


def profit_factor(combined: Dict[str, Any], rows: Sequence[Any]) -> Optional[float]:
    """
    Gross profit over gross loss, from the summed halves.

    None when nothing was traded — the same "undefined" the no-losing-trade case carries; 0.0 is
    reserved for a run that DID trade and produced no profit.

    Args:
        combined: The figures being built, the halves already folded
        rows: The units

    Returns:
        The factor, 0.0 or None as above
    """
    if combined['total_trades'] == 0:
        return None
    total_profit, total_loss = combined['total_profit'], combined['total_loss']
    return total_profit / total_loss if total_loss > 0 else (0.0 if total_profit == 0 else None)


def net_profit(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    Gross profit less gross loss.

    Args:
        combined: The figures being built, the halves already folded
        rows: The units

    Returns:
        The net profit
    """
    return combined['total_profit'] - combined['total_loss']


def final_equity_of_one_account(combined: Dict[str, Any], rows: Sequence[Any]) -> Optional[float]:
    """
    The closing equity of ONE account. Each unit is its own account: their closing equities add up
    to a TOTAL, served as one beside the capital it started from, and `final_equity` has none to
    describe once there are several.

    Args:
        combined: The figures being built, the total already folded
        rows: The units

    Returns:
        The total when the group holds one unit, else None
    """
    return combined['total_final_equity'] if combined['unit_count'] == 1 else None


def fill_fees_by_source(combined: Dict[str, Any], rows: Sequence[Any]) -> Dict[FeeSource, FeeSourceFills]:
    """
    The units' fill fees per fee source, summed (#508) — a fill count and a fee total both add up
    across the accounts of one currency.

    Args:
        combined: The figures being built
        rows: The units

    Returns:
        One entry per fee source any unit carries, in the order FeeSource declares them
    """
    summed: Dict[FeeSource, FeeSourceFills] = {}
    for source in FeeSource:
        parts = [r.fill_fees_by_source[source] for r in rows if source in r.fill_fees_by_source]
        if parts:
            summed[source] = FeeSourceFills(
                fill_count=sum(part.fill_count for part in parts),
                fill_fees=sum(part.fill_fees for part in parts))
    return summed


# The headline's declarations. The aggregated portfolio reuses the sums its rates are built from,
# so each rule stands once.
PORTFOLIO_HEADLINE_FIGURES: Dict[str, FigureDeclaration] = {
    'unit_count': FigureDeclaration(Reduction.COUNT),
    'total_trades': FigureDeclaration(Reduction.SUM, empty_value=0),
    'winning_trades': FigureDeclaration(Reduction.SUM, empty_value=0),
    'losing_trades': FigureDeclaration(Reduction.SUM, empty_value=0),
    'win_rate': FigureDeclaration(Reduction.DERIVE),
    'profit_factor': FigureDeclaration(Reduction.DERIVE),
    'total_profit': FigureDeclaration(Reduction.SUM, empty_value=0.0),
    'total_loss': FigureDeclaration(Reduction.SUM, empty_value=0.0),
    'net_profit': FigureDeclaration(Reduction.DERIVE),
    # The deepest by magnitude, ties to the first unit — the rule the ledger fold applies to its
    # companions; the peak, the share and the unit come from that same unit
    'account_max_drawdown': FigureDeclaration(Reduction.MAX_ABS, empty_value=0.0),
    'max_equity': FigureDeclaration(Reduction.COMPANION, companion_of='account_max_drawdown',
                                    empty_value=0.0),
    'account_max_dd_pct': FigureDeclaration(Reduction.COMPANION,
                                            companion_of='account_max_drawdown', empty_value=0.0),
    'account_max_drawdown_unit': FigureDeclaration(Reduction.COMPANION,
                                                   companion_of='account_max_drawdown',
                                                   source='name', empty_value=''),
    'total_fees': FigureDeclaration(Reduction.SUM, empty_value=0.0),
    'fees_charged': FigureDeclaration(Reduction.SUM, empty_value=0.0),
    'fill_fees_by_source': FigureDeclaration(Reduction.DERIVE),
    # Summed over the units, where the ledger reads one account's latest value — #657
    'unrealized_pnl': FigureDeclaration(Reduction.SUM, empty_value=0.0),
    'final_equity': FigureDeclaration(Reduction.DERIVE),
    'total_final_equity': FigureDeclaration(Reduction.SUM, source='final_equity',
                                            empty_value=0.0),
    'total_initial_balance': FigureDeclaration(Reduction.SUM, source='initial_balance',
                                               empty_value=0.0),
    # Summed over the units, where the ledger reads one account's latest value — #657
    'open_position_count': FigureDeclaration(Reduction.SUM, source=open_position_count,
                                             empty_value=0),
}

PORTFOLIO_HEADLINE_TABLE = FoldTable(
    name='portfolio_headline',
    figures=PORTFOLIO_HEADLINE_FIGURES,
    derivations={
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'net_profit': net_profit,
        'fill_fees_by_source': fill_fees_by_source,
        'final_equity': final_equity_of_one_account,
    },
    order=GroupOrder.SORTED,
    grain=('currency',),
    row_model=PortfolioUnitRow,
    served_as=PortfolioAggregateRow,
    # The group's identity, named by the family — it holds even for a group of no unit
    outside_the_fold={'currency': 'the group: the family names the currency it folds'},
)


# --- the aggregated portfolio ----------------------------------------------------------------


def _highest_equity(rows: Sequence[Any]) -> Tuple[float, str]:
    """
    The highest peak equity among the units, and the unit that reached it.

    Strictly greater from 0.0: a tie keeps the first unit, and when no unit rose above 0.0 the
    answer is 0.0 with no unit — as the executive block always read it.

    Args:
        rows: The units

    Returns:
        The peak and its unit's name
    """
    peak, unit = 0.0, ''
    for row in rows:
        if row.max_equity > peak:
            peak, unit = row.max_equity, row.name
    return peak, unit


def highest_equity(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    The highest peak equity among the units.

    Args:
        combined: The figures being built
        rows: The units

    Returns:
        The peak, 0.0 when none rose above it
    """
    return _highest_equity(rows)[0]


def highest_equity_unit(combined: Dict[str, Any], rows: Sequence[Any]) -> str:
    """
    The unit that reached the highest peak equity.

    Args:
        combined: The figures being built
        rows: The units

    Returns:
        Its name, '' when none rose above 0.0
    """
    return _highest_equity(rows)[1]


def average_win(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    The mean winning trade, from the summed gross profit and winners.

    Args:
        combined: The figures being built
        rows: The units

    Returns:
        Gross profit per winner; 0.0 without a winner
    """
    winning = combined['winning_trades']
    return combined['total_profit'] / winning if winning > 0 else 0.0


def average_loss(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    The mean losing trade's size, from the summed gross loss and losers.

    Args:
        combined: The figures being built
        rows: The units

    Returns:
        Gross loss per loser, as a magnitude; 0.0 without a loser
    """
    losing = combined['losing_trades']
    return abs(combined['total_loss']) / losing if losing > 0 else 0.0


def average_initial(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    The mean starting balance of the units.

    Args:
        combined: The figures being built
        rows: The units

    Returns:
        Summed initial balance per unit; 0.0 without a unit
    """
    count = combined['unit_count']
    return combined['initial_balance'] / count if count > 0 else 0.0


def balance_pnl(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    The balance moved over the run: final less initial.

    Args:
        combined: The figures being built
        rows: The units

    Returns:
        The difference
    """
    return combined['final_balance'] - combined['initial_balance']


def balance_pnl_pct(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    The balance moved over the run, in percent of the initial balance.

    Args:
        combined: The figures being built, the balance P&L already derived
        rows: The units

    Returns:
        The percentage; 0.0 without an initial balance
    """
    initial = combined['initial_balance']
    return (combined['balance_pnl'] / initial * 100) if initial > 0 else 0.0


def recovery_factor(combined: Dict[str, Any], rows: Sequence[Any]) -> Optional[float]:
    """
    One account's P&L over its own decline — undefined once the P&L is several accounts'.

    Args:
        combined: The figures being built, the balance P&L and the deepest decline already folded
        rows: The units

    Returns:
        The factor; 0.0 without a decline; None over several units
    """
    if combined['unit_count'] > 1:
        return None
    max_dd = combined['account_max_drawdown']
    return combined['balance_pnl'] / abs(max_dd) if max_dd != 0 else 0.0


def average_spread(combined: Dict[str, Any], rows: Sequence[Any]) -> Optional[float]:
    """
    The spread cost per trade.

    Args:
        combined: The figures being built, the spread total and the trades already folded
        rows: The units

    Returns:
        The mean; None when the spread total is unmeasured; 0.0 without a trade
    """
    total_spread = combined['total_spread_cost']
    if total_spread is None:
        return None
    return total_spread / combined['total_trades'] if combined['total_trades'] > 0 else 0.0


def measured_min_in_flight(row: Any) -> Optional[float]:
    """
    A unit's fastest in-flight time, where its mean was measured.

    Args:
        row: A pending orders unit row

    Returns:
        The minimum, or None when the unit measured no in-flight time
    """
    return row.min_in_flight_ms if row.avg_in_flight_ms is not None else None


def measured_max_in_flight(row: Any) -> Optional[float]:
    """
    A unit's slowest in-flight time, where its mean was measured.

    Args:
        row: A pending orders unit row

    Returns:
        The maximum, or None when the unit measured no in-flight time
    """
    return row.max_in_flight_ms if row.avg_in_flight_ms is not None else None


def active_limit_count(row: Any) -> int:
    """
    A unit's resting limit orders, counted.

    Args:
        row: A pending orders unit row

    Returns:
        How many it holds
    """
    return len(row.active_limit_orders)


def active_stop_count(row: Any) -> int:
    """
    A unit's resting stop orders, counted.

    Args:
        row: A pending orders unit row

    Returns:
        How many it holds
    """
    return len(row.active_stop_orders)


_INPUT_OF_A_RATE = 'folded, not served: an input of the aggregated rates'

PENDING_TOTALS_TABLE = FoldTable(
    name='aggregated_portfolio_pending',
    figures={
        'pending_total_submitted': FigureDeclaration(Reduction.SUM, source='total_submitted',
                                                     empty_value=0),
        'pending_total_accepted': FigureDeclaration(Reduction.SUM, source='total_accepted',
                                                    empty_value=0),
        'pending_total_rejected': FigureDeclaration(Reduction.SUM, source='total_rejected',
                                                    empty_value=0),
        'pending_total_never_confirmed': FigureDeclaration(
            Reduction.SUM, source='total_never_confirmed', empty_value=0),
        'pending_total_expired': FigureDeclaration(Reduction.SUM, source='total_expired',
                                                   empty_value=0),
        'pending_in_flight_count': FigureDeclaration(Reduction.SUM, source='in_flight_count',
                                                     empty_value=0),
        # Weighted by the orders each unit measured, as one pass over them would
        'pending_avg_in_flight_ms': FigureDeclaration(
            Reduction.DERIVE, source='avg_in_flight_ms', weighted_by='pending_in_flight_count'),
        'pending_min_in_flight_ms': FigureDeclaration(Reduction.MIN, source=measured_min_in_flight),
        'pending_max_in_flight_ms': FigureDeclaration(Reduction.MAX, source=measured_max_in_flight),
        'pending_active_limit_count': FigureDeclaration(Reduction.SUM, source=active_limit_count,
                                                        empty_value=0),
        'pending_active_stop_count': FigureDeclaration(Reduction.SUM, source=active_stop_count,
                                                       empty_value=0),
    },
    row_model=PendingOrdersUnitRow,
    served_as=AggregatedPortfolioRow,
    serves_whole=False,
    outside_the_fold={'pending_in_flight_count': 'folded, not served: the weight of the mean'},
)


def spot_scenarios(combined: Dict[str, Any], rows: Sequence[Any]) -> List[AggregatedPortfolioSpotScenarioRow]:
    """
    Each unit's dual balance and estimated value — a listing, one row per unit.

    The currency split and the estimate are the UNIT's own, stamped from the broker config (#265)
    and derived once in the portfolio builder. Splitting the symbol string here read `BTCUSDT` as
    base `BTCU` / quote `SDT`, and a second estimate dropped the initial base holding whenever the
    unit ended without one.

    Args:
        combined: The figures being built
        rows: The units

    Returns:
        One row per unit, in unit order
    """
    listing = []
    for r in rows:
        quote = r.quote_currency or r.currency
        base = r.base_currency
        base_bal = r.balances.get(base, 0.0) if base else 0.0
        listing.append(AggregatedPortfolioSpotScenarioRow(
            scenario_name=r.name, quote_currency=quote, base_currency=base,
            quote_balance=r.balances.get(quote, 0.0), base_balance=base_bal,
            quote_initial=r.initial_balances.get(quote, 0.0),
            base_initial=r.initial_balances.get(base, 0.0) if base else 0.0,
            last_price=r.last_price, est_current=r.spot_est_current,
            est_initial=r.spot_est_initial, has_base_holdings=_holds_base(r)))
    return listing


def _holds_base(row: Any) -> bool:
    """
    Whether a unit holds a base-asset balance it can value.

    Args:
        row: A portfolio unit row

    Returns:
        True with a price and a non-zero base balance
    """
    base_bal = row.balances.get(row.base_currency, 0.0) if row.base_currency else 0.0
    return row.last_price > 0 and base_bal != 0.0


def any_base_holdings(combined: Dict[str, Any], rows: Sequence[Any]) -> bool:
    """
    Whether any unit holds a base-asset balance it can value.

    Args:
        combined: The figures being built
        rows: The units

    Returns:
        True when one does
    """
    return any(_holds_base(row) for row in rows)


SPOT_BALANCES_TABLE = FoldTable(
    name='aggregated_portfolio_spot',
    figures={
        'spot_scenarios': FigureDeclaration(Reduction.DERIVE),
        # Added in a loop, as the executive spot block always did — #657
        'spot_total_est_current': FigureDeclaration(Reduction.SUM, source='spot_est_current',
                                                    addition=Addition.SEQUENTIAL),
        'spot_total_est_initial': FigureDeclaration(Reduction.SUM, source='spot_est_initial',
                                                    addition=Addition.SEQUENTIAL),
        'spot_has_base_holdings': FigureDeclaration(Reduction.DERIVE),
    },
    derivations={'spot_scenarios': spot_scenarios, 'spot_has_base_holdings': any_base_holdings},
    row_model=PortfolioUnitRow,
    served_as=AggregatedPortfolioRow,
    serves_whole=False,
)

_HEADLINE = PORTFOLIO_HEADLINE_FIGURES

AGGREGATED_PORTFOLIO_TABLE = FoldTable(
    name='aggregated_portfolio',
    figures={
        # The headline's sums and its deepest decline, the inputs of the rates below
        'unit_count': _HEADLINE['unit_count'],
        'total_trades': _HEADLINE['total_trades'],
        'winning_trades': _HEADLINE['winning_trades'],
        'losing_trades': _HEADLINE['losing_trades'],
        'total_profit': _HEADLINE['total_profit'],
        'total_loss': _HEADLINE['total_loss'],
        'account_max_drawdown': _HEADLINE['account_max_drawdown'],
        'account_max_dd_pct': _HEADLINE['account_max_dd_pct'],
        'account_max_drawdown_scenario': _HEADLINE['account_max_drawdown_unit'],
        'total_long_trades': FigureDeclaration(Reduction.SUM, empty_value=0),
        'total_short_trades': FigureDeclaration(Reduction.SUM, empty_value=0),
        'avg_win': FigureDeclaration(Reduction.DERIVE),
        'avg_loss': FigureDeclaration(Reduction.DERIVE),
        'initial_balance': FigureDeclaration(Reduction.SUM, empty_value=0.0),
        'final_balance': FigureDeclaration(Reduction.SUM, source='current_balance',
                                           empty_value=0.0),
        'avg_initial': FigureDeclaration(Reduction.DERIVE),
        'balance_pnl': FigureDeclaration(Reduction.DERIVE),
        'balance_pnl_pct': FigureDeclaration(Reduction.DERIVE),
        'recovery_factor': FigureDeclaration(Reduction.DERIVE),
        'highest_equity': FigureDeclaration(Reduction.DERIVE),
        'highest_equity_scenario': FigureDeclaration(Reduction.DERIVE),
        'total_spread_cost': FigureDeclaration(Reduction.SUM_COMPLETE),
        'total_modelled_spread_cost': FigureDeclaration(Reduction.SUM, empty_value=0.0),
        'total_commission': FigureDeclaration(Reduction.SUM, empty_value=0.0),
        'total_swap': FigureDeclaration(Reduction.SUM, empty_value=0.0),
        'maker_fee': FigureDeclaration(Reduction.SUM, empty_value=0.0),
        'taker_fee': FigureDeclaration(Reduction.SUM, empty_value=0.0),
        'avg_spread': FigureDeclaration(Reduction.DERIVE),
    },
    derivations={
        'avg_win': average_win,
        'avg_loss': average_loss,
        'avg_initial': average_initial,
        'balance_pnl': balance_pnl,
        'balance_pnl_pct': balance_pnl_pct,
        'recovery_factor': recovery_factor,
        'highest_equity': highest_equity,
        'highest_equity_scenario': highest_equity_unit,
        'avg_spread': average_spread,
    },
    row_model=PortfolioUnitRow,
    served_as=AggregatedPortfolioRow,
    outside_the_fold={
        **{name: _INPUT_OF_A_RATE for name in ('unit_count', 'total_trades', 'winning_trades',
                                               'losing_trades', 'total_profit', 'total_loss',
                                               'account_max_drawdown')},
        'headline': 'a nested table: the same units folded by the headline table',
        'is_spot': 'the group: the family passes whether these units trade at spot',
        'label': "the group: the family's name for a margin or spot part",
        **{name: 'folded from the units\' execution rows by their table'
           for name in AGGREGATED_EXECUTION_TABLE.figures},
        **{name: 'folded from the units\' pending rows by their table'
           for name in PENDING_TOTALS_TABLE.figures
           if name not in PENDING_TOTALS_TABLE.outside_the_fold},
        **{name: 'at spot only, folded by the spot table' for name in SPOT_BALANCES_TABLE.figures},
    },
)
